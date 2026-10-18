"""Durable coordinator state and majority-voted replication log."""
import contextlib
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shared_evolution import validate, vector, fingerprint


class Store:
    def __init__(self,path,nodes):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        if not nodes or len(set(nodes))!=len(nodes): raise ValueError('Configure distinct voter node IDs')
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY, voters TEXT, killed INTEGER, interval INTEGER, next REAL);
            CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, heartbeat REAL, cursor INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, round INTEGER, target TEXT, state TEXT, owner TEXT, lease TEXT, expires REAL, attempts INTEGER DEFAULT 0, result TEXT);
            CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY, round INTEGER, kind TEXT, digest TEXT, value TEXT, state TEXT, UNIQUE(round,kind,digest));
            CREATE TABLE IF NOT EXISTS votes(proposal INTEGER,node TEXT,PRIMARY KEY(proposal,node));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, stamp REAL, kind TEXT, value TEXT, previous TEXT, digest TEXT);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,sender TEXT,recipient TEXT,body TEXT,acked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS join_tickets(digest TEXT PRIMARY KEY,node TEXT,expires REAL,used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS credentials(digest TEXT PRIMARY KEY,node TEXT,revoked INTEGER DEFAULT 0);
            ''')
            db.execute('INSERT OR IGNORE INTO config VALUES(1,?,0,120,0)',(json.dumps(sorted(nodes)),))
            if json.loads(db.execute('SELECT voters FROM config').fetchone()[0])!=sorted(nodes):
                raise ValueError('Voter membership is persistent; use the original --nodes list')

    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def append(self,db,kind,value):
        row=db.execute('SELECT seq,digest FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        seq=row['seq']+1 if row else 1; previous=row['digest'] if row else '0'*64
        raw=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
        digest=hashlib.sha256((previous+str(seq)+kind+raw).encode()).hexdigest()
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(seq,time.time(),kind,raw,previous,digest))
        return seq

    def configuration(self,db):
        cfg=dict(db.execute('SELECT * FROM config').fetchone());cfg['voters']=json.loads(cfg['voters']);return cfg

    def node(self,db,node):
        if node not in self.configuration(db)['voters']: raise ValueError('Node is not a configured voter')

    def issue_join(self,node,ttl=900):
        if type(ttl) is not int or not 30<=ttl<=3600:raise ValueError('Join ticket lifetime must be 30–3600 seconds')
        ticket=secrets.token_urlsafe(48)
        with self.db() as db:
            self.node(db,node)
            db.execute('INSERT INTO join_tickets VALUES(?,?,?,0)',(hashlib.sha256(ticket.encode()).hexdigest(),node,time.time()+ttl))
            self.append(db,'join_ticket_issued',{'node':node,'expires_in':ttl})
        return ticket

    def redeem_join(self,node,ticket):
        if not isinstance(ticket,str) or not 32<=len(ticket)<=256:raise ValueError('Invalid join ticket')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self.node(db,node)
            digest=hashlib.sha256(ticket.encode()).hexdigest()
            row=db.execute('SELECT * FROM join_tickets WHERE digest=?',(digest,)).fetchone()
            if not row or row['node']!=node or row['used'] or row['expires']<time.time():
                raise ValueError('Join ticket is expired, used, or not valid for this node')
            credential=secrets.token_urlsafe(48)
            db.execute('UPDATE join_tickets SET used=1 WHERE digest=?',(digest,))
            db.execute('UPDATE credentials SET revoked=1 WHERE node=?',(node,))
            db.execute('INSERT INTO credentials VALUES(?,?,0)',(hashlib.sha256(credential.encode()).hexdigest(),node))
            self.append(db,'node_enrolled',{'node':node})
            return credential

    def authenticate_node(self,credential):
        if not isinstance(credential,str) or len(credential)>256:return None
        with self.db() as db:
            row=db.execute('SELECT node FROM credentials WHERE digest=? AND revoked=0',
                           (hashlib.sha256(credential.encode()).hexdigest(),)).fetchone()
            return row['node'] if row else None

    def revoke(self,node):
        with self.db() as db:
            self.node(db,node)
            db.execute('UPDATE credentials SET revoked=1 WHERE node=?',(node,))
            db.execute('UPDATE join_tickets SET used=1 WHERE node=?',(node,))
            self.append(db,'node_revoked',{'node':node})

    def schedule(self,manual=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cfg=self.configuration(db)
            if cfg['killed']: raise ValueError('Kill switch is latched')
            if not manual and time.time()<cfg['next']: return None
            if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]: return None
            current=db.execute('SELECT max(round) FROM jobs').fetchone()[0] or 0
            round_id=current+1
            db.executemany("INSERT INTO jobs(round,target,state) VALUES(?,?,'queued')",[(round_id,n) for n in cfg['voters']])
            db.execute('UPDATE config SET next=? WHERE id=1',(time.time()+cfg['interval'],))
            self.append(db,'scheduled',{'round':round_id,'nodes':cfg['voters']})
            return round_id

    def check(self,db):
        cfg=self.configuration(db)
        return bool(cfg['killed'])

    def heartbeat(self,node,cursor=0,job=None,lease=None):
        with self.db() as db:
            self.node(db,node)
            db.execute('INSERT INTO nodes VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,cursor=excluded.cursor',(node,time.time(),cursor))
            db.execute("UPDATE jobs SET expires=? WHERE owner=? AND id=? AND lease=? AND state='running' AND expires>=?",(time.time()+20,node,job,lease,time.time()))
            return {'killed':self.check(db),'proposals':[dict(r) for r in db.execute("SELECT * FROM proposals WHERE state='pending'")],
                    'messages':[dict(r) for r in db.execute('SELECT * FROM messages WHERE recipient=? AND acked=0',(node,))]}

    def claim(self,node):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self.node(db,node)
            if self.check(db): return None
            # Retry only on the target node; duplicate results require the current lease.
            db.execute("UPDATE jobs SET state='queued',owner=NULL,lease=NULL WHERE state='running' AND expires<? AND attempts<3",(time.time(),))
            db.execute("UPDATE jobs SET state='failed',result='lease expired three times' WHERE state='running' AND expires<? AND attempts>=3",(time.time(),))
            row=db.execute("SELECT * FROM jobs WHERE target=? AND state='queued' ORDER BY id LIMIT 1",(node,)).fetchone()
            if not row: return None
            if db.execute("SELECT 1 FROM jobs WHERE owner=? AND state='running'",(node,)).fetchone(): return None
            lease=secrets.token_hex(16)
            db.execute("UPDATE jobs SET state='running',owner=?,lease=?,expires=?,attempts=attempts+1 WHERE id=?",(node,lease,time.time()+20,row['id']))
            return dict(db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())

    def propose(self,db,round_id,kind,value):
        if db.execute("SELECT 1 FROM proposals WHERE round=? AND kind=? AND state='committed'",(round_id,kind)).fetchone():
            return
        raw=json.dumps(value,sort_keys=True,allow_nan=False);digest=hashlib.sha256(raw.encode()).hexdigest()
        db.execute("INSERT OR IGNORE INTO proposals(round,kind,digest,value,state) VALUES(?,?,?,?,'pending')",(round_id,kind,digest,raw))

    def finish(self,node,job,lease,result):
        vector(result['qt']);vector(result['alpha']);validate(result['artifact'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self.node(db,node)
            row=db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()
            if not row or row['owner']!=node or row['lease']!=lease: raise ValueError('Stale or foreign lease')
            if row['state']=='done': return {'duplicate':True}
            if row['state']!='running' or row['expires']<time.time() or self.check(db): raise ValueError('Lease is no longer active')
            db.execute("UPDATE jobs SET state='done',result=? WHERE id=?",(json.dumps(result,allow_nan=False),job))
            self.propose(db,row['round'],'artifact',result['artifact'])
            all_rows=db.execute('SELECT result,state FROM jobs WHERE round=?',(row['round'],)).fetchall()
            if all(r['state']=='done' for r in all_rows):
                states=[json.loads(r['result']) for r in all_rows]
                avg={key:[sum(s[key][i] for s in states)/len(states) for i in range(2)] for key in ['qt','alpha']}
                self.propose(db,row['round'],'qt',avg)
            self.append(db,'result',{'node':node,'job':job,'round':row['round'],'artifact':fingerprint(result['artifact'])})
        return {'duplicate':False}

    def fail(self,node,job,lease,detail):
        with self.db() as db:
            self.node(db,node)
            row=db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()
            if not row or row['owner']!=node or row['lease']!=lease or row['state']!='running': raise ValueError('Stale lease')
            db.execute("UPDATE jobs SET state='failed',result=? WHERE id=?",(str(detail)[-4000:],job))
            self.append(db,'failure',{'node':node,'job':job,'detail':str(detail)[-4000:]})

    def vote(self,node,proposal):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self.node(db,node)
            row=db.execute('SELECT * FROM proposals WHERE id=?',(proposal,)).fetchone()
            if not row: raise ValueError('Unknown proposal')
            if self.check(db): raise ValueError('Kill switch is latched')
            if row['state']!='pending': return {'state':row['state']}
            value=json.loads(row['value'])
            if row['kind']=='artifact': validate(value)
            else: vector(value['qt']);vector(value['alpha'])
            db.execute('INSERT OR IGNORE INTO votes VALUES(?,?)',(proposal,node))
            quorum=len(self.configuration(db)['voters'])//2+1
            if db.execute('SELECT count(*) FROM votes WHERE proposal=?',(proposal,)).fetchone()[0]>=quorum:
                # At most one accepted value of each kind per evolution round.
                db.execute("UPDATE proposals SET state=CASE WHEN id=? THEN 'committed' ELSE 'superseded' END WHERE round=? AND kind=? AND state='pending'",(proposal,row['round'],row['kind']))
                self.append(db,'committed',{'proposal':proposal,'round':row['round'],'kind':row['kind'],'value':value,'quorum':quorum})
            return {'state':db.execute('SELECT state FROM proposals WHERE id=?',(proposal,)).fetchone()[0]}

    def events(self,after):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 200',(after,))]

    def send(self,sender,recipient,body):
        if not isinstance(body,str) or len(body)>4096: raise ValueError('Message must be at most 4096 characters')
        with self.db() as db:
            self.node(db,sender);self.node(db,recipient)
            db.execute('INSERT INTO messages(sender,recipient,body) VALUES(?,?,?)',(sender,recipient,body))

    def ack(self,node,ids):
        with self.db() as db:
            self.node(db,node)
            for ident in ids[:200]: db.execute('UPDATE messages SET acked=1 WHERE id=? AND recipient=?',(ident,node))

    def control(self,action,interval=None):
        with self.db() as db:
            if action=='kill':
                db.execute('UPDATE config SET killed=1')
                db.execute("UPDATE jobs SET state='cancelled' WHERE state IN ('queued','running')")
            elif action=='resume': db.execute('UPDATE config SET killed=0,next=?',(time.time(),))
            elif action=='interval':
                if type(interval) is not int or not 10<=interval<=86400: raise ValueError('Interval must be 10–86400')
                db.execute('UPDATE config SET interval=?',(interval,))
            else: raise ValueError('Unknown control')
            self.append(db,'control',{'action':action,'interval':interval})

    def status(self):
        with self.db() as db:return {'config':self.configuration(db),'nodes':[dict(r) for r in db.execute('SELECT * FROM nodes')],
            'jobs':[dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 40')],
            'proposals':[dict(r) for r in db.execute('SELECT * FROM proposals ORDER BY id DESC LIMIT 30')],
            'events':self.events(max(0,(db.execute('SELECT max(seq) FROM events').fetchone()[0] or 0)-20))}
