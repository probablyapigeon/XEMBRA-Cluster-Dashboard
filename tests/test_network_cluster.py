import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from network_store import Store
from network_cluster import Client,Replica
from shared_evolution import compression,validate

SPEC={'version':1,'text_limit':120,'sentiment':.2,'eeg_mean':.3,'eeg_std':.4}
RESULT={'artifact':SPEC,'qt':[1.,2.],'alpha':[.5,.5],'generation':1}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'coordinator.db',['a','b','c'])
    def tearDown(self):self.temp.cleanup()
    def test_majority_and_one_artifact_per_round(self):
        self.store.schedule(True);job=self.store.claim('a')
        self.store.finish('a',job['id'],job['lease'],RESULT)
        proposal=self.store.status()['proposals'][0]['id']
        self.assertEqual(self.store.vote('a',proposal)['state'],'pending')
        self.assertEqual(self.store.vote('a',proposal)['state'],'pending')
        self.assertEqual(self.store.vote('b',proposal)['state'],'committed')
        second=self.store.claim('b');other=dict(RESULT,artifact=dict(SPEC,sentiment=.9))
        self.store.finish('b',second['id'],second['lease'],other)
        self.assertEqual(len(self.store.status()['proposals']),1)
    def test_lease_expiry_reassignment_and_idempotent_results(self):
        self.store.schedule(True);old=self.store.claim('a')
        with self.store.db() as db:db.execute('UPDATE jobs SET expires=0 WHERE id=?',(old['id'],))
        self.store.heartbeat('a')
        with self.assertRaises(ValueError):self.store.finish('a',old['id'],old['lease'],RESULT)
        fresh=self.store.claim('a');self.assertNotEqual(old['lease'],fresh['lease'])
        with self.assertRaises(ValueError):self.store.finish('a',old['id'],old['lease'],RESULT)
        self.store.finish('a',fresh['id'],fresh['lease'],RESULT)
        self.assertTrue(self.store.finish('a',fresh['id'],fresh['lease'],RESULT)['duplicate'])
    def test_idle_heartbeat_does_not_extend_abandoned_work(self):
        self.store.schedule(True);job=self.store.claim('a')
        before=job['expires'];self.store.heartbeat('a')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT expires FROM jobs WHERE id=?',(job['id'],)).fetchone()[0],before)
        self.store.heartbeat('a',job=job['id'],lease=job['lease'])
        with self.store.db() as db:
            self.assertGreater(db.execute('SELECT expires FROM jobs WHERE id=?',(job['id'],)).fetchone()[0],before)
    def test_kill_survives_coordinator_restart(self):
        self.store.control('kill')
        restarted=Store(self.store.path,['a','b','c'])
        self.assertIsNone(restarted.claim('a'))
        with self.assertRaises(ValueError):restarted.schedule(True)
        restarted.control('resume');self.assertIsNotNone(restarted.schedule(True))
    def test_mailbox_persists_and_ack_is_recipient_scoped(self):
        self.store.send('a','b','hello')
        messages=self.store.heartbeat('b')['messages'];self.assertEqual(messages[0]['body'],'hello')
        self.store.ack('a',[messages[0]['id']]);self.assertTrue(self.store.heartbeat('b')['messages'])
        self.store.ack('b',[messages[0]['id']]);self.assertFalse(self.store.heartbeat('b')['messages'])
    def test_replication_replay_hash_validation_and_persistent_state(self):
        self.store.schedule(True);job=self.store.claim('a');self.store.finish('a',job['id'],job['lease'],RESULT)
        proposal=self.store.status()['proposals'][0]['id'];self.store.vote('a',proposal);self.store.vote('b',proposal)
        replica=Replica(self.root/'node');events=self.store.events(0);replica.replicate(events);replica.replicate(events);replica.apply()
        self.assertEqual(json.loads((self.root/'node'/'evolution_state.json').read_text())['artifact'],SPEC)
        restarted=Replica(self.root/'node');self.assertEqual(restarted.cursor(),len(events))
        broken=dict(events[-1],seq=len(events)+1)
        with self.assertRaises(ValueError):restarted.replicate([broken])
        self.assertEqual(restarted.cursor(),len(events))
    def test_remote_python_is_rejected_and_tls_required(self):
        with self.assertRaises(ValueError):validate({'python':'import os'})
        with self.assertRaises(ValueError):Client('http://remote.example:8770','a'*48)
        with self.assertRaises(ValueError):self.store.claim('intruder')
        class Psi:source='text';raw_data='x'*200
        self.assertEqual(len(compression(SPEC)(Psi())['summary']),120)
    def test_join_is_node_bound_single_use_expiring_and_revocable(self):
        ticket=self.store.issue_join('a')
        with self.assertRaises(ValueError):self.store.redeem_join('b',ticket)
        credential=self.store.redeem_join('a',ticket)
        self.assertEqual(self.store.authenticate_node(credential),'a')
        with self.assertRaises(ValueError):self.store.redeem_join('a',ticket)
        replacement=self.store.redeem_join('a',self.store.issue_join('a'))
        self.assertIsNone(self.store.authenticate_node(credential))
        self.assertEqual(self.store.authenticate_node(replacement),'a')
        self.store.revoke('a');self.assertIsNone(self.store.authenticate_node(replacement))
        expired=self.store.issue_join('a')
        with self.store.db() as db:db.execute('UPDATE join_tickets SET expires=0')
        with self.assertRaises(ValueError):self.store.redeem_join('a',expired)


class NetworkIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.token=secrets.token_urlsafe(48);self.tokenfile=self.root/'token';self.tokenfile.write_text(self.token)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        self.client=Client(f'http://127.0.0.1:{self.port}',self.token)
        self.processes=[];self.logs=[]
        source=self.root/'source';source.mkdir()
        (source/'cluster_test.py').write_text('''import json,os,time
from pathlib import Path
p=Path('evolution_state.json');s=json.loads(p.read_text());n=1. if os.environ['XEMBRA_NODE_ID']=='a' else 3.
s.update(generation=s.get('generation',0)+1,qt=[n,n],alpha=[n,n],artifact={'version':1,'text_limit':120,'sentiment':n,'eeg_mean':.3,'eeg_std':.4})
time.sleep(.1);p.write_text(json.dumps(s))
''')
        self.source=source
        self.launch(['coordinator','--state',str(self.root/'server'),'--nodes','a,b','--port',str(self.port)])
        self.wait_for(lambda:self.request_ready())
    def launch(self,args,tokenfile=None):
        log=(self.root/f'process-{len(self.processes)}.log').open('wb');self.logs.append(log)
        process=subprocess.Popen([sys.executable,str(ROOT/'scripts'/'network_cluster.py'),*args,'--token-file',str(tokenfile or self.tokenfile)],stdout=log,stderr=subprocess.STDOUT)
        self.processes.append(process);return process
    def request_ready(self):
        try:return self.client.call('/api/status')
        except (OSError,ValueError):return None
    def wait_for(self,predicate,timeout=20):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            value=predicate()
            if value:return value
            time.sleep(.2)
        details=[]
        for path in self.root.glob('process-*.log'):details.append(path.read_text(errors='replace')[-3000:])
        self.fail('Condition timed out\n'+'\n'.join(details))
    def tearDown(self):
        try:self.client.call('/api/control',{'action':'kill'})
        except (OSError,ValueError):pass
        time.sleep(.5)
        for process in reversed(self.processes):
            if process.poll() is None:process.terminate()
        for process in self.processes:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        for log in self.logs:log.close()
        # Watchdog wrappers get time to reap workloads after agent termination.
        time.sleep(.5);self.temp.cleanup()
    def start_agent(self,node):
        tokenfile=self.root/(node+'.token')
        if not tokenfile.exists():
            ticket=self.client.call('/api/issue-join',{'node':node})['result']['ticket']
            credential=Client(self.client.url,'').call('/api/join',{'node':node,'ticket':ticket})['result']['token']
            tokenfile.write_text(credential)
        return self.launch(['agent','--node',node,'--state',str(self.root/node),'--source',str(self.source),'--url',self.client.url],tokenfile)
    def test_two_nodes_schedule_share_sync_replicate_message_and_restart(self):
        a=self.start_agent('a');self.start_agent('b')
        self.wait_for(lambda:len([p for p in self.client.call('/api/status')['proposals'] if p['state']=='committed'])>=2)
        def synced():
            try:
                states=[json.loads((self.root/n/'evolution_state.json').read_text()) for n in ['a','b']]
                return all(s.get('accepted_qt_round')==1 and s['qt']==[2.,2.] for s in states)
            except (OSError,ValueError):return False
        self.wait_for(synced)
        states=[json.loads((self.root/n/'evolution_state.json').read_text()) for n in ['a','b']]
        self.assertEqual(states[0]['artifact'],states[1]['artifact'])
        self.assertIn(states[0]['artifact']['sentiment'],[1.,3.])
        self.client.call('/api/send',{'node':'a','recipient':'b','body':'cross-node hello'})
        def delivered():
            db=sqlite3.connect(self.root/'b'/'replica.db')
            try:
                return db.execute("SELECT 1 FROM mailbox WHERE body='cross-node hello'").fetchone()
            finally:db.close()
        self.wait_for(delivered)
        first=json.loads((self.root/'a'/'evolution_state.json').read_text())['generation']
        a.terminate();a.wait(timeout=5);self.start_agent('a')
        self.client.call('/api/schedule',{})
        self.wait_for(lambda:json.loads((self.root/'a'/'evolution_state.json').read_text())['generation']>first)
        self.wait_for(lambda:any(p['kind']=='qt' and p['round']==2 and p['state']=='committed' for p in self.client.call('/api/status')['proposals']))
        self.assertEqual({n['id'] for n in self.client.call('/api/status')['nodes']},{'a','b'})
    def test_http_requires_authentication(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:urllib.request.urlopen(self.client.url+'/api/status')
        self.assertEqual(exc.exception.code,401);exc.exception.close()
    def test_node_cannot_impersonate_peer_or_control_cluster(self):
        ticket=self.client.call('/api/issue-join',{'node':'a'})['result']['ticket']
        credential=Client(self.client.url,'').call('/api/join',{'node':'a','ticket':ticket})['result']['token']
        node=Client(self.client.url,credential)
        self.assertFalse(node.call('/api/heartbeat',{'node':'a'})['result']['killed'])
        for path,data in [('/api/heartbeat',{'node':'b'}),('/api/control',{'action':'kill'}),('/api/issue-join',{'node':'b'})]:
            with self.assertRaises(ValueError):node.call(path,data)
        self.client.call('/api/revoke',{'node':'a'})
        with self.assertRaises(ValueError):node.call('/api/heartbeat',{'node':'a'})

    def test_invalid_worker_results_become_failed_jobs(self):
        (self.source/'cluster_test.py').write_text("import json\nfrom pathlib import Path\nPath('evolution_state.json').write_text(json.dumps({'artifact':{'python':'not allowed'},'qt':[1,1],'alpha':[1,1]}))\n")
        self.start_agent('a');self.start_agent('b')
        self.wait_for(lambda:all(j['state']=='failed' for j in self.client.call('/api/status')['jobs']))
        self.assertTrue(all('Unsupported compression' in j['result'] for j in self.client.call('/api/status')['jobs']))


if __name__=='__main__':unittest.main()
