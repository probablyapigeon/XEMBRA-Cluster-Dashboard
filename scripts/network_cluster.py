"""Authenticated coordinator, durable node agent, and network mailbox CLI."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shared_evolution import validate,vector,write_json
from network_store import Store

ROOT=Path(__file__).resolve().parents[1]


def read_token(path,node=False):
    token=os.environ.get('XEMBRA_NODE_TOKEN' if node else 'XEMBRA_CLUSTER_TOKEN') or Path(path).read_text(encoding='utf-8').strip()
    if len(token)<32: raise ValueError('Use a cluster token of at least 32 characters')
    return token


@contextlib.contextmanager
def secret_output(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:yield stream
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class Client:
    def __init__(self,url,token,ca=None):
        from urllib.parse import urlparse
        parsed=urlparse(url)
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ['localhost','127.0.0.1','::1']):
            raise ValueError('Remote coordinator URLs must use HTTPS')
        self.url=url.rstrip('/');self.token=token
        self.context=ssl.create_default_context(cafile=ca)

    def call(self,path,data=None):
        raw=json.dumps(data,allow_nan=False).encode() if data is not None else None
        req=urllib.request.Request(self.url+path,data=raw,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=5,context=self.context) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail=exc.read().decode();exc.close();raise ValueError(detail) from None


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(5)
    def log_message(self,*args):pass
    def reply(self,code,data,html=False):
        raw=(data if html else json.dumps(data,allow_nan=False)).encode()
        self.send_response(code);self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json')
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(raw)
    def authorized(self):
        import secrets
        header=self.headers.get('Authorization','')
        if secrets.compare_digest(header,'Bearer '+self.server.token):
            self.identity='admin';return True
        credential=header.removeprefix('Bearer ') if header.startswith('Bearer ') else ''
        self.identity=self.server.store.authenticate_node(credential)
        return self.identity is not None
    def do_GET(self):
        if self.path=='/':return self.reply(200,(ROOT/'scripts'/'network_dashboard.html').read_text(encoding='utf-8'),True)
        if not self.authorized():return self.reply(401,{'error':'Authorization required'})
        try:
            if self.path=='/api/status':return self.reply(200,self.server.store.status())
            if self.path.startswith('/api/events?after='):
                return self.reply(200,self.server.store.events(int(self.path.split('=',1)[1])))
            self.reply(404,{'error':'Not found'})
        except ValueError as exc:self.reply(400,{'error':str(exc)})
    def do_POST(self):
        if self.path!='/api/join' and not self.authorized():return self.reply(401,{'error':'Authorization required'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=32768:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size));store=self.server.store
            if self.path=='/api/join':
                return self.reply(200,{'result':{'node':data['node'],'token':store.redeem_join(data['node'],data['ticket'])}})
            admin_routes=['/api/control','/api/schedule','/api/issue-join','/api/revoke']
            if self.identity!='admin' and (self.path in admin_routes or data.get('node')!=self.identity):
                return self.reply(403,{'error':'Credential is not authorized for this operation or node'})
            routes={
                '/api/heartbeat':lambda:store.heartbeat(data['node'],data.get('cursor',0),data.get('job'),data.get('lease')),
                '/api/claim':lambda:store.claim(data['node']),
                '/api/finish':lambda:store.finish(data['node'],data['job'],data['lease'],data['result']),
                '/api/fail':lambda:store.fail(data['node'],data['job'],data['lease'],data['detail']),
                '/api/vote':lambda:store.vote(data['node'],data['proposal']),
                '/api/send':lambda:store.send(data['node'],data['recipient'],data['body']),
                '/api/ack':lambda:store.ack(data['node'],data['ids']),
                '/api/control':lambda:store.control(data['action'],data.get('interval')),
                '/api/schedule':lambda:store.schedule(manual=True),
                '/api/issue-join':lambda:{'ticket':store.issue_join(data['node'],data.get('ttl',900))},
                '/api/revoke':lambda:store.revoke(data['node']),
            }
            if self.path not in routes:return self.reply(404,{'error':'Not found'})
            self.reply(200,{'result':routes[self.path]()})
        except (ValueError,KeyError,TypeError) as exc:self.reply(400,{'error':str(exc)})


def coordinator(args,token):
    if args.bind not in ['127.0.0.1','localhost','::1'] and not (args.cert and args.key):
        raise ValueError('Binding a remote interface requires --cert and --key for TLS')
    store=Store(Path(args.state)/'coordinator.db',args.nodes.split(','))
    server=ThreadingHTTPServer((args.bind,args.port),Handler);server.daemon_threads=True
    server.store=store;server.token=token
    if args.cert:
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(args.cert,args.key);server.socket=ctx.wrap_socket(server.socket,server_side=True)
    stop=threading.Event()
    def schedule():
        while not stop.wait(1):
            try:store.schedule()
            except ValueError:pass
    threading.Thread(target=schedule,daemon=True).start()
    print(f'Coordinator dashboard: {"https" if args.cert else "http"}://{args.bind}:{args.port}',flush=True)
    try:server.serve_forever()
    finally:stop.set();server.server_close()


class Replica:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'replica.db'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,kind TEXT,value TEXT,digest TEXT);
              CREATE TABLE IF NOT EXISTS mailbox(id INTEGER PRIMARY KEY,sender TEXT,body TEXT);
              CREATE TABLE IF NOT EXISTS accepted(kind TEXT PRIMARY KEY,round INTEGER,value TEXT);
              CREATE TABLE IF NOT EXISTS outbox(job INTEGER PRIMARY KEY,lease TEXT,result TEXT);''')
    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def cursor(self):
        with self.db() as db:return db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]
    def replicate(self,events):
        with self.db() as db:
            head=db.execute('SELECT seq,digest FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            seq=head['seq'] if head else 0;previous=head['digest'] if head else '0'*64
            for event in events:
                if event['seq']<=seq:continue
                raw=event['value']
                digest=hashlib.sha256((previous+str(event['seq'])+event['kind']+raw).encode()).hexdigest()
                if event['seq']!=seq+1 or event['previous']!=previous or event['digest']!=digest:
                    raise ValueError('Replication sequence or hash-chain mismatch')
                value=json.loads(raw)
                if event['kind']=='committed':
                    if value['kind']=='artifact':validate(value['value'])
                    elif value['kind']=='qt':vector(value['value']['qt']);vector(value['value']['alpha'])
                    else:raise ValueError('Unknown replicated artifact kind')
                    db.execute('INSERT INTO accepted VALUES(?,?,?) ON CONFLICT(kind) DO UPDATE SET round=excluded.round,value=excluded.value WHERE excluded.round>accepted.round',
                               (value['kind'],value['round'],json.dumps(value['value'])))
                db.execute('INSERT INTO events VALUES(?,?,?,?)',(event['seq'],event['kind'],raw,digest))
                seq=event['seq'];previous=digest
    def apply(self):
        target=self.folder/'evolution_state.json'
        state=json.loads(target.read_text()) if target.exists() else {'generation':0,'alpha':[1.,1.],'qt':[.5,.5]}
        with self.db() as db:
            for row in db.execute('SELECT * FROM accepted'):
                marker='accepted_'+row['kind']+'_round'
                if row['round']>state.get(marker,0):
                    value=json.loads(row['value'])
                    if row['kind']=='artifact':state['artifact']=value
                    else:state.update(value)
                    state[marker]=row['round']
        write_json(target,state)
    def mailbox(self,messages):
        with self.db() as db:
            for message in messages:
                db.execute('INSERT OR IGNORE INTO mailbox VALUES(?,?,?)',(message['id'],message['sender'],message['body']))


def node_lock(folder):
    handle=(Path(folder)/'node.lock').open('a+b')
    if os.name=='nt':
        import msvcrt
        if os.fstat(handle.fileno()).st_size==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:handle.close();raise ValueError('Node state folder is already in use')
    else:
        import fcntl
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:handle.close();raise ValueError('Node state folder is already in use')
    return handle


def agent(args,token):
    folder=Path(args.state).resolve();replica=Replica(folder);handle=node_lock(folder)
    identity=folder/'identity.json'
    expected={'node':args.node,'coordinator':args.url.rstrip('/')}
    if identity.exists() and json.loads(identity.read_text())!=expected:
        raise ValueError('Persistent node identity/coordinator differs; use a new state folder')
    write_json(identity,expected)
    # Self-bootstrap missing code from this installed checkout; preserve runtime data.
    for source in Path(args.source).glob('*.py'):
        target=folder/source.name
        if source.name!='xembra_generated.py' or not target.exists():shutil.copy2(source,target)
    client=Client(args.url,token,args.ca)
    def interrupted(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    child=None;job=None;last_contact=time.monotonic();started=0;output=None
    agent_started=time.monotonic()
    def stop_child():
        (folder/'contact.lease').unlink(missing_ok=True)
        if child and child.poll() is None:child.wait(timeout=10)
    print('Node',args.node,'ready; persistent state:',folder,flush=True)
    try:
        while True:
            if os.environ.get('XEMBRA_AGENT_WATCHDOG')=='1' and time.monotonic()-agent_started>10:
                guardian_contact=folder/'watchdog'/'contact.lease'
                if not guardian_contact.exists() or time.time()-guardian_contact.stat().st_mtime>10:
                    print('Watchdog lease lost; agent stopping',flush=True);return
            try:
                response=client.call('/api/heartbeat',{'node':args.node,'cursor':replica.cursor(),
                    'job':job['id'] if job else None,'lease':job['lease'] if job else None})['result']
                last_contact=time.monotonic()
                (folder/'contact.lease').touch()
                (folder/'agent.heartbeat').touch()
                replica.replicate(client.call('/api/events?after='+str(replica.cursor())))
                replica.mailbox(response['messages'])
                if response['messages']:client.call('/api/ack',{'node':args.node,'ids':[m['id'] for m in response['messages']]})
                if response['killed'] or (folder/'node.killed').exists():
                    stop_child()
                    child=None;job=None
                    if output:output.close();output=None
                    if os.environ.get('XEMBRA_AGENT_WATCHDOG')=='1':return
                    time.sleep(1);continue
                # Every voter validates compatibility before approving a proposal.
                for proposal in response['proposals']:
                    value=json.loads(proposal['value'])
                    if proposal['kind']=='artifact':validate(value)
                    else:vector(value['qt']);vector(value['alpha'])
                    client.call('/api/vote',{'node':args.node,'proposal':proposal['id']})
                with replica.db() as db:outbox=[dict(r) for r in db.execute('SELECT * FROM outbox')]
                for item in outbox:
                    try:
                        payload=json.loads(item['result'])
                        if '_failure' in payload:
                            client.call('/api/fail',{'node':args.node,'job':item['job'],'lease':item['lease'],'detail':payload['_failure']})
                        else:
                            client.call('/api/finish',{'node':args.node,'job':item['job'],'lease':item['lease'],'result':payload})
                    except ValueError as exc:
                        if 'lease' not in str(exc).lower():raise
                        print('Result rejected after lease expiry:',item['job'],flush=True)
                    with replica.db() as db:db.execute('DELETE FROM outbox WHERE job=?',(item['job'],))
                if child and (child.poll() is not None or time.monotonic()-started>300):
                    if child.poll() is None:stop_child()
                    output.close();output=None
                    result_path=folder/'evolution_state.json'
                    try:
                        if child.returncode!=0 or not result_path.exists():
                            raise ValueError('Worker exit '+str(child.returncode)+' or missing evolution state')
                        result=json.loads(result_path.read_text());validate(result['artifact']);vector(result['qt']);vector(result['alpha'])
                    except (ValueError,KeyError) as exc:
                        result={'_failure':str(exc)}
                    with replica.db() as db:db.execute('INSERT OR REPLACE INTO outbox VALUES(?,?,?)',(job['id'],job['lease'],json.dumps(result)))
                    child=None;job=None
                if not child:
                    replica.apply()
                    job=client.call('/api/claim',{'node':args.node})['result']
                    if job:
                        log=folder/'network-worker.log'
                        if log.exists() and log.stat().st_size>2_000_000:
                            log.replace(folder/'network-worker.previous.log')
                        output=log.open('ab')
                        child=subprocess.Popen([sys.executable,'-u',str(ROOT/'scripts'/'node_workload.py'),str(folder),str(os.getpid())],cwd=folder,
                            env=dict(os.environ,XEMBRA_DAEMON='1',XEMBRA_NODE_ID=args.node,PYTHONIOENCODING='utf-8'),stdin=subprocess.DEVNULL,
                            stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                        started=time.monotonic()
            except (OSError,ValueError,TimeoutError) as exc:
                print('Node connection/protocol error:',str(exc)[:500],flush=True)
                if child and child.poll() is None and time.monotonic()-last_contact>10:
                    stop_child()
                    child=None;job=None
                    if output:output.close();output=None
            time.sleep(1)
    finally:
        stop_child()
        if output:output.close()
        handle.close()


def watchdog(args,token):
    """Per-node supervisor. A durable local kill latch overrides resurrection."""
    folder=Path(args.state).resolve();folder.mkdir(parents=True,exist_ok=True)
    guard=folder/'watchdog';guard.mkdir(exist_ok=True)
    handle=node_lock(guard);latch=folder/'node.killed';pulse=guard/'status.json'
    client=Client(args.url,token,args.ca)
    child=None;failures=0;next_start=0;started=0;output=None
    was_killed=False
    def stop():
        nonlocal child,output
        if child:
            if child.poll() is None:child.terminate();child.wait(timeout=5)
            child=None
            # Workload wrapper notices the dead agent and reaps its own child.
        if output:output.close();output=None
    def interrupted(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    print('Node watchdog ready:',args.node,flush=True)
    try:
        while True:
            (guard/'contact.lease').touch()
            phase='waiting'
            if child and child.poll() is not None:
                code=child.returncode
                failures=failures+1 if time.monotonic()-started<120 else 1
                stop();next_start=time.monotonic()+max(11,min(60,2**min(failures,6)))
                if failures>=5:latch.write_text('Five rapid agent failures',encoding='utf-8')
                print('Agent exited:',code,'failures:',failures,flush=True)
            try:
                status=client.call('/api/status')
                if status['config']['killed']:
                    latch.write_text('Cluster kill switch',encoding='utf-8')
                if was_killed and not latch.exists():failures=0;next_start=0
                was_killed=latch.exists()
                if latch.exists():
                    stop();phase='killed'
                elif not child and time.monotonic()>=next_start:
                    arguments=[sys.executable,str(Path(__file__).resolve()),'agent','--node',args.node,
                        '--state',str(folder),'--source',args.source,'--url',args.url,'--token-file',args.token_file]
                    if args.ca:arguments+=['--ca',args.ca]
                    log=guard/'agent.log'
                    if log.exists() and log.stat().st_size>2_000_000:log.replace(guard/'agent.previous.log')
                    output=log.open('ab')
                    child=subprocess.Popen(arguments,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                        env=dict(os.environ,XEMBRA_AGENT_WATCHDOG='1'),
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    started=time.monotonic();phase='starting'
                elif child:
                    heartbeat=folder/'agent.heartbeat'
                    stamp=heartbeat.stat().st_mtime if heartbeat.exists() else 0
                    if time.monotonic()-started>20 and time.time()-stamp>20:
                        child.terminate();phase='stale-agent'
                    else:phase='running'
                else:phase='backoff'
            except (OSError,ValueError,TimeoutError) as exc:
                stop();next_start=time.monotonic()+11;phase='disconnected'
                print('Watchdog cannot reach coordinator:',str(exc)[:300],flush=True)
            write_json(pulse,{'node':args.node,'phase':phase,'agent_pid':child.pid if child else None,
                'guardian_pid':os.getpid(),'heartbeat':time.time(),'failures':failures})
            time.sleep(1)
    finally:
        stop();handle.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role',choices=['coordinator','agent','watchdog','node-kill','node-resume','status','kill','resume','schedule','send','init-token','issue-join','join','revoke'])
    parser.add_argument('--state',default=str(ROOT/'logs'/'network'))
    parser.add_argument('--token-file',default=str(ROOT/'logs'/'network'/'token'))
    parser.add_argument('--nodes',default='node-a,node-b');parser.add_argument('--node',default='node-a')
    parser.add_argument('--bind',default='127.0.0.1');parser.add_argument('--port',type=int,default=8770)
    parser.add_argument('--url',default='http://127.0.0.1:8770')
    parser.add_argument('--cert');parser.add_argument('--key');parser.add_argument('--ca')
    parser.add_argument('--recipient');parser.add_argument('--body')
    parser.add_argument('--source',default=str(ROOT),help='Local trusted checkout to snapshot into the node folder')
    parser.add_argument('--ticket-file');parser.add_argument('--output');parser.add_argument('--ttl',type=int,default=900)
    args=parser.parse_args()
    if args.role in ['node-kill','node-resume']:
        folder=Path(args.state);folder.mkdir(parents=True,exist_ok=True)
        latch=folder/'node.killed'
        if args.role=='node-kill':latch.write_text('Local operator kill switch',encoding='utf-8')
        else:latch.unlink(missing_ok=True)
        print('Local node command:',args.role);return
    if args.role=='init-token':
        import secrets
        path=Path(args.token_file);path.parent.mkdir(parents=True,exist_ok=True)
        with secret_output(path) as stream:stream.write(secrets.token_urlsafe(48))
        print('Token created at',path,'(not printed)');return
    if args.role=='join':
        if not args.ticket_file or not args.output:parser.error('join requires --ticket-file and --output for the node credential')
        ticket=Path(args.ticket_file).read_text(encoding='utf-8').strip()
        client=Client(args.url,'',args.ca)
        # Reserve the output first so a successful one-time redemption is not overwritten.
        path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
        with secret_output(path) as stream:
            result=client.call('/api/join',{'node':args.node,'ticket':ticket})['result']
            stream.write(result['token'])
        print('Node credential saved to',path,'(not printed)');return
    token=read_token(args.token_file,node=args.role in ['agent','watchdog'])
    if args.role=='coordinator':coordinator(args,token)
    elif args.role=='agent':agent(args,token)
    elif args.role=='watchdog':watchdog(args,token)
    else:
        client=Client(args.url,token,args.ca)
        if args.role=='issue-join':
            if not args.output:parser.error('issue-join requires --output for the one-time ticket')
            path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
            with secret_output(path) as stream:
                result=client.call('/api/issue-join',{'node':args.node,'ttl':args.ttl})['result']
                stream.write(result['ticket'])
            print('One-time join ticket saved to',path,'(not printed)')
        elif args.role=='revoke':print(client.call('/api/revoke',{'node':args.node}))
        elif args.role=='status':print(json.dumps(client.call('/api/status'),indent=2))
        elif args.role=='schedule':print(client.call('/api/schedule',{}))
        elif args.role=='send':print(client.call('/api/send',{'node':args.node,'recipient':args.recipient,'body':args.body}))
        else:print(client.call('/api/control',{'action':args.role}))


if __name__=='__main__':main()
