"""Local XEMBRA control plane. No external packages required by the controller."""
import argparse
import collections
import contextlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
import logging
import signal

ROOT = Path(os.environ.get('XEMBRA_CLUSTER_ROOT', Path(__file__).resolve().parents[1]))
STATE = Path(os.environ.get('XEMBRA_CONTROL_STATE', ROOT / 'logs' / 'cluster'))
STATE.mkdir(parents=True, exist_ok=True)
DB = STATE / 'control.db'
SCRIPT = Path(__file__).resolve()
PORT = int(os.environ.get('XEMBRA_DASHBOARD_PORT', '8765'))
BIND = os.environ.get('XEMBRA_DASHBOARD_BIND', '127.0.0.1')
ALLOWED_HOSTS = set(os.environ.get('XEMBRA_ALLOWED_HOSTS',
    f'127.0.0.1:{PORT},localhost:{PORT}').split(','))
SHUTDOWN = threading.Event()


@contextlib.contextmanager
def database():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def init():
    with database() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, enabled INTEGER,
          killed INTEGER, interval INTEGER, max_workers INTEGER, next_run REAL, stop INTEGER);
        INSERT OR IGNORE INTO settings VALUES(1,1,0,60,2,0,0);
        CREATE TABLE IF NOT EXISTS pulses(name TEXT PRIMARY KEY, stamp REAL, pid INTEGER);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, batch INTEGER, state TEXT,
          created REAL, started REAL, finished REAL, worker TEXT, code INTEGER, detail TEXT);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, stamp REAL, sender TEXT,
          recipient TEXT, kind TEXT, payload TEXT, consumed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS crashes(id INTEGER PRIMARY KEY, stamp REAL, component TEXT,
          code INTEGER, detail TEXT);
        ''')


def pulse(name):
    with database() as db:
        db.execute('INSERT OR REPLACE INTO pulses VALUES(?,?,?)', (name,time.time(),os.getpid()))


def settings():
    with database() as db:
        return dict(db.execute('SELECT * FROM settings WHERE id=1').fetchone())


def message(sender, recipient, kind, payload):
    with database() as db:
        db.execute('INSERT INTO messages(stamp,sender,recipient,kind,payload) VALUES(?,?,?,?,?)',
                   (time.time(),sender,recipient,kind,json.dumps(payload)))


def crash(component, code, detail):
    with database() as db:
        db.execute('INSERT INTO crashes(stamp,component,code,detail) VALUES(?,?,?,?)',
                   (time.time(),component,code,detail[-4000:]))


def lock(name):
    handle = (STATE / (name + '.lock')).open('a+b')
    if os.fstat(handle.fileno()).st_size == 0:
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def alive(name):
    handle = lock(name)
    if handle:
        handle.close()
        return False
    return True


def spawn(args, output=None, detached=False):
    flags = ((subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
             if detached else subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0)
    return subprocess.Popen([sys.executable,str(SCRIPT),*map(str,args)],cwd=ROOT,
        stdin=subprocess.DEVNULL,stdout=output or subprocess.DEVNULL,
        stderr=subprocess.STDOUT,creationflags=flags,start_new_session=detached and os.name!='nt')


def snapshot():
    with database() as db:
        result = {'settings':settings(), 'guardian_running':alive('guardian')}
        result['pulses'] = [dict(r) for r in db.execute('SELECT * FROM pulses')]
        result['jobs'] = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 50')]
        result['crashes'] = [dict(r) for r in db.execute('SELECT * FROM crashes ORDER BY id DESC LIMIT 20')]
        result['messages'] = [dict(r) for r in db.execute('SELECT * FROM messages ORDER BY id DESC LIMIT 30')]
        result['totals'] = dict(db.execute("SELECT count(*) jobs, sum(state='done') completed, sum(state='failed') failed FROM jobs").fetchone())
    result['now'] = time.time()
    return result


def command(kind, data=None):
    data = data or {}
    with database() as db:
        if kind == 'kill':
            db.execute('UPDATE settings SET killed=1,enabled=0 WHERE id=1')
            db.execute("UPDATE jobs SET state='cancelled',finished=? WHERE state='pending'",(time.time(),))
        elif kind == 'resume':
            db.execute('UPDATE settings SET killed=0,enabled=1,next_run=? WHERE id=1',(time.time(),))
        elif kind == 'pause':
            db.execute('UPDATE settings SET enabled=0 WHERE id=1')
        elif kind == 'schedule':
            interval = int(data.get('interval',60))
            maximum = int(data.get('max_workers',2))
            if not 10 <= interval <= 86400 or not 1 <= maximum <= 4:
                raise ValueError('Interval must be 10–86400 seconds; workers must be 1–4.')
            db.execute('UPDATE settings SET interval=?,max_workers=? WHERE id=1',(interval,maximum))
        elif kind == 'run':
            if db.execute('SELECT killed FROM settings').fetchone()[0]:
                raise ValueError('Kill switch is latched. Resume explicitly first.')
            if db.execute("SELECT count(*) FROM jobs WHERE state='pending'").fetchone()[0] >= 20:
                raise ValueError('Queue limit reached.')
            batch = int(time.time()*1000)
            db.execute("INSERT INTO jobs(batch,state,created) VALUES(?,'pending',?)",(batch,time.time()))
        elif kind == 'stop':
            db.execute('UPDATE settings SET stop=1,killed=1,enabled=0 WHERE id=1')
        else:
            raise ValueError('Unknown command')
    message('dashboard','controller',kind,data)


def worker(job_id, slot):
    name = 'worker-' + str(slot)
    folder = STATE / name
    folder.mkdir(exist_ok=True)
    # Code snapshot; private generated functions and SQLite state persist per worker.
    for source in ROOT.glob('*.py'):
        target = folder / source.name
        if source.name != 'xembra_generated.py' or not target.exists():
            shutil.copy2(source,target)
    handler = RotatingFileHandler(folder / 'worker.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO); logger.addHandler(handler)
    tail = collections.deque(maxlen=25)
    child = None
    reason = ''
    try:
        child = subprocess.Popen([sys.executable,'-u',str(folder / 'cluster_test.py')],cwd=folder,
            env=dict(os.environ,XEMBRA_DAEMON='1',PYTHONIOENCODING='utf-8'),
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        def read():
            for line in child.stdout:
                tail.append(line.rstrip()); logger.info(line.rstrip())
        reader = threading.Thread(target=read,daemon=True); reader.start()
        start = time.monotonic()
        while child.poll() is None:
            pulse(name)
            with database() as db:
                row = db.execute("SELECT stamp FROM pulses WHERE name='controller'").fetchone()
            cfg = settings()
            if cfg['killed'] or cfg['stop']:
                reason = 'cancelled'; break
            if not row or time.time()-row[0] > 12:
                reason = 'controller heartbeat lost'; break
            if time.monotonic()-start > 300:
                reason = 'worker timeout (300 seconds)'; break
            time.sleep(.5)
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10); reader.join(timeout=3)
        code = child.returncode
        state = 'cancelled' if reason == 'cancelled' else ('failed' if reason or code else 'done')
        detail = reason + '\n' + '\n'.join(tail)
        with database() as db:
            db.execute('UPDATE jobs SET state=?,finished=?,code=?,detail=? WHERE id=?',
                       (state,time.time(),code,detail[-4000:],job_id))
        if state == 'failed':
            crash(name,code,detail)
        message(name,'controller','job_finished',{'job':job_id,'state':state,'code':code})
    finally:
        if child and child.poll() is None:
            child.kill(); child.wait()
        handler.close()


def controller():
    handle = lock('controller')
    if not handle:
        raise SystemExit('Controller already running')
    workers = {}
    try:
        with database() as db:
            db.execute("UPDATE jobs SET state='failed',finished=?,detail='Controller restarted; worker lease lost' WHERE state='running'",(time.time(),))
        while not settings()['stop']:
            pulse('controller')
            cfg = settings()
            for slot,(proc,job) in list(workers.items()):
                if proc.poll() is not None:
                    with database() as db:
                        row = db.execute('SELECT state FROM jobs WHERE id=?',(job,)).fetchone()
                        if row[0] == 'running':
                            db.execute("UPDATE jobs SET state='failed',finished=?,code=?,detail='Worker process exited unexpectedly' WHERE id=?",(time.time(),proc.returncode,job))
                            db.execute('INSERT INTO crashes(stamp,component,code,detail) VALUES(?,?,?,?)',
                                       (time.time(),'worker-'+str(slot),proc.returncode,'Worker process exited unexpectedly'))
                    del workers[slot]
            with database() as db:
                # Atomic consumption makes the mailbox useful for local daemon IPC.
                db.execute("UPDATE messages SET consumed=1 WHERE recipient='controller' AND consumed=0")
                recent = db.execute("SELECT count(*) FROM crashes WHERE stamp>? AND component LIKE 'worker-%'",(time.time()-300,)).fetchone()[0]
                if recent >= 5 and not cfg['killed']:
                    db.execute('UPDATE settings SET killed=1,enabled=0 WHERE id=1')
                    db.execute('INSERT INTO messages(stamp,sender,recipient,kind,payload) VALUES(?,?,?,?,?)',
                               (time.time(),'controller','guardian','circuit_breaker',json.dumps({'failures':recent})))
                    continue
                if cfg['enabled'] and not cfg['killed'] and time.time() >= cfg['next_run']:
                    # One cluster-wide batch with one evolution run per configured worker.
                    pending = db.execute("SELECT count(*) FROM jobs WHERE state IN ('pending','running')").fetchone()[0]
                    if pending == 0:
                        batch = int(time.time()*1000)
                        db.executemany("INSERT INTO jobs(batch,state,created) VALUES(?,'pending',?)",
                                       [(batch,time.time()) for _ in range(cfg['max_workers'])])
                    db.execute('UPDATE settings SET next_run=? WHERE id=1',(time.time()+cfg['interval'],))
                if not cfg['killed']:
                    queued = db.execute("SELECT id FROM jobs WHERE state='pending' ORDER BY id").fetchall()
                    desired = min(cfg['max_workers'],len(workers)+len(queued))
                    for slot in range(1,desired+1):
                        if slot in workers or not queued:
                            continue
                        job = queued.pop(0)[0]
                        db.execute("UPDATE jobs SET state='running',started=?,worker=? WHERE id=?",(time.time(),'worker-'+str(slot),job))
                        proc = spawn(['worker','--job',job,'--slot',slot])
                        workers[slot] = (proc,job)
            time.sleep(.5)
    finally:
        # Workers observe stop/kill or lease expiry and terminate their own child.
        for proc,job in workers.values():
            try: proc.wait(timeout=15)
            except subprocess.TimeoutExpired: proc.terminate(); proc.wait(timeout=5)
        handle.close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self,*args):
        pass

    def reply(self,status,body,kind='application/json'):
        raw = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.end_headers(); self.wfile.write(raw)

    def trusted(self):
        return self.headers.get('Host') in ALLOWED_HOSTS

    def do_GET(self):
        if not self.trusted():
            return self.reply(403,'{}')
        if self.path == '/':
            html = (ROOT / 'scripts' / 'cluster_dashboard.html').read_text(encoding='utf-8')
            return self.reply(200,html.replace('__TOKEN__',self.server.token),'text/html')
        if self.path == '/api/status':
            return self.reply(200,json.dumps(snapshot()))
        return self.reply(404,'{}')

    def do_POST(self):
        if not self.trusted() or not secrets.compare_digest(self.headers.get('X-Control-Token',''),self.server.token):
            return self.reply(403,'{"error":"Forbidden"}')
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 4096: raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(size))
            command(data['command'],data)
            self.reply(200,'{"ok":true}')
        except (ValueError,KeyError,TypeError) as exc:
            self.reply(400,json.dumps({'error':str(exc)}))


def guardian():
    handle = lock('guardian')
    if not handle: raise SystemExit('Guardian already running')
    server = ThreadingHTTPServer((BIND,PORT),Handler)
    server.daemon_threads = True
    server.token = secrets.token_urlsafe(32)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    proc = None
    failures = 0
    next_start = 0
    launched = 0
    was_killed = False
    try:
        while not SHUTDOWN.is_set() and not settings()['stop']:
            pulse('guardian')
            cfg = settings()
            if was_killed and not cfg['killed']:
                failures = 0
            was_killed = bool(cfg['killed'])
            if proc and proc.poll() is not None:
                code = proc.returncode
                if not cfg['killed']:
                    crash('controller',code,'Controller exited; guardian will restart it')
                    failures = failures+1 if time.time()-launched < 120 else 1
                proc = None
                next_start = time.time()+max(13,min(60,2**min(failures,6)))
                if failures >= 5: command('kill')
            if proc and not cfg['killed']:
                with database() as db:
                    row = db.execute("SELECT stamp FROM pulses WHERE name='controller'").fetchone()
                stamp = row[0] if row else launched
                if time.time()-max(stamp,launched) > 20:
                    crash('controller',-1,'Stale controller heartbeat; restarting')
                    proc.terminate()
            if not proc and not cfg['killed'] and time.time() >= next_start:
                proc = spawn(['controller'])
                launched = time.time()
            if cfg['killed'] and proc:
                # Keep dashboard available; stop scheduling, workers read the durable latch.
                pass
            time.sleep(.5)
    finally:
        with database() as db: db.execute('UPDATE settings SET stop=1 WHERE id=1')
        if proc:
            try: proc.wait(timeout=18)
            except subprocess.TimeoutExpired: proc.terminate(); proc.wait(timeout=5)
        server.shutdown(); server.server_close(); handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['start','guardian','controller','worker','status','stop','kill','resume','run','pause','schedule'])
    parser.add_argument('--job',type=int); parser.add_argument('--slot',type=int)
    parser.add_argument('--interval',type=int,default=60)
    parser.add_argument('--max-workers',type=int,default=2)
    args = parser.parse_args(); init()
    if args.command == 'guardian':
        def shutdown(signum,frame):
            SHUTDOWN.set()
        signal.signal(signal.SIGTERM,shutdown)
        signal.signal(signal.SIGINT,shutdown)
        with database() as db: db.execute('UPDATE settings SET stop=0 WHERE id=1')
        guardian()
    elif args.command == 'controller': controller()
    elif args.command == 'worker': worker(args.job,args.slot)
    elif args.command == 'status': print(json.dumps(snapshot(),indent=2))
    elif args.command == 'start':
        if alive('guardian'): raise SystemExit(f'Already running: http://127.0.0.1:{PORT}')
        with database() as db: db.execute('UPDATE settings SET stop=0 WHERE id=1')
        with (STATE / 'guardian.log').open('a',encoding='utf-8') as out:
            proc = spawn(['guardian'],output=out,detached=True)
        for _ in range(20):
            if proc.poll() is not None: raise SystemExit('Startup failed; inspect logs/cluster/guardian.log')
            if alive('guardian'):
                print(f'Dashboard: http://127.0.0.1:{PORT}'); return
            time.sleep(.25)
        raise SystemExit('Startup not confirmed')
    else:
        command(args.command,vars(args))
        print('Command accepted:',args.command)


if __name__ == '__main__': main()
