"""Isolated control-plane integration checks; never run the real cluster."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'cluster_control.py'


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.env = dict(os.environ,XEMBRA_CLUSTER_ROOT=str(self.root),XEMBRA_CONTROL_STATE=str(self.state),XEMBRA_WORKLOAD='legacy')
        self.previous = {key:os.environ.get(key) for key in ['XEMBRA_CLUSTER_ROOT','XEMBRA_CONTROL_STATE','XEMBRA_WORKLOAD']}
        os.environ.update({key:self.env[key] for key in self.previous})
        spec = importlib.util.spec_from_file_location('control_test',SCRIPT)
        self.control = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.control)
        self.control.init()
        self.proc = None

    def tearDown(self):
        self.control.command('stop')
        if self.proc:
            try: self.proc.wait(timeout=22)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait(); self.fail('Controller did not stop')
        self.wait_for(lambda:not self.control.alive('controller'),timeout=10)
        for key,value in self.previous.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
        self.temp.cleanup()

    def wait_for(self,predicate,timeout=10):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            value=predicate()
            if value: return value
            time.sleep(.1)
        self.fail('Condition not reached within timeout')

    def launch(self,role='controller'):
        self.proc=subprocess.Popen([sys.executable,str(SCRIPT),role],env=self.env,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def test_kill_latch_validation_and_message_delivery(self):
        self.control.command('run')
        self.control.command('kill')
        with self.assertRaises(ValueError): self.control.command('run')
        with self.assertRaises(ValueError): self.control.command('schedule',{'max_workers':50})
        self.assertEqual(self.control.snapshot()['jobs'][0]['state'],'cancelled')
        self.control.command('resume')
        self.assertEqual(self.control.settings()['killed'],0)
        self.assertEqual(self.control.snapshot()['messages'][0]['kind'],'resume')

    def test_two_workers_isolate_writes_and_return_messages(self):
        (self.root/'cluster_test.py').write_text("from pathlib import Path\nimport time\nPath('result.txt').write_text('private')\ntime.sleep(.2)\nprint('completed')\n")
        self.launch()
        self.wait_for(lambda:self.control.snapshot()['totals']['completed']==2)
        snap=self.control.snapshot()
        self.assertEqual({j['worker'] for j in snap['jobs']},{'worker-1','worker-2'})
        self.assertFalse((self.root/'result.txt').exists())
        for slot in [1,2]: self.assertEqual((self.state/f'worker-{slot}'/'result.txt').read_text(),'private')
        self.wait_for(lambda:all(m['consumed'] for m in self.control.snapshot()['messages'] if m['recipient']=='controller'))

    def test_learning_workload_dispatches_and_exposes_metrics(self):
        self.env['XEMBRA_WORKLOAD']='learning'
        (self.root/'cluster_test.py').write_text("raise RuntimeError('legacy workload must not run')\n")
        (self.root/'learning_worker.py').write_text("import json\nfrom pathlib import Path\nPath('learning_metrics.json').write_text(json.dumps({'generation':1,'test':{'compressed_bytes':42}}))\n")
        self.launch()
        self.wait_for(lambda:self.control.snapshot()['totals']['completed']==2)
        reports=self.control.snapshot()['learning']
        self.assertEqual(len(reports),2)
        self.assertTrue(all(item['report']['test']['compressed_bytes']==42 for item in reports))

    def test_crash_recorded_and_circuit_breaker_latches(self):
        (self.root/'cluster_test.py').write_text("raise RuntimeError('deliberate crash')\n")
        self.control.command('pause')
        for _ in range(5): self.control.command('run')
        self.launch()
        # Five process launches and durable SQLite writes can exceed 10s on Docker Desktop.
        # The contract is five failures within five minutes, not a 10s startup deadline.
        self.wait_for(lambda:self.control.settings()['killed']==1,timeout=30)
        snap=self.control.snapshot()
        self.assertGreaterEqual(len(snap['crashes']),5)
        self.assertIn('deliberate crash',snap['crashes'][0]['detail'])

    def test_xc_dispatch_copies_program_and_exposes_reports(self):
        self.env['XEMBRA_WORKLOAD']='xc'
        (self.root/'xc').mkdir()
        (self.root/'xc'/'mara_memory.xc').write_text('fixture')
        (self.root/'xc_worker.py').write_text("import json\nfrom pathlib import Path\nassert Path('xc/mara_memory.xc').read_text()=='fixture'\nPath('xc_metrics.json').write_text(json.dumps({'events_total':8,'action':'HELP'}))\n")
        self.launch()
        self.wait_for(lambda:self.control.snapshot()['totals']['completed']==2)
        reports=self.control.snapshot()['xc']
        self.assertEqual(len(reports),2)
        self.assertTrue(all(item['report']['events_total']==8 for item in reports))

    def test_dashboard_rejects_unauthenticated_controls(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        self.env['XEMBRA_DASHBOARD_PORT']=str(port)
        (self.root/'scripts').mkdir()
        (self.root/'scripts'/'cluster_dashboard.html').write_text('token=__TOKEN__')
        self.control.command('kill')
        self.launch('guardian')
        def ready():
            try:
                return urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status',timeout=1).status==200
            except OSError: return False
        self.wait_for(ready)
        request=urllib.request.Request(f'http://127.0.0.1:{port}/api/command',data=b'{"command":"resume"}',method='POST')
        with self.assertRaises(urllib.error.HTTPError) as err: urllib.request.urlopen(request)
        self.assertEqual(err.exception.code,403)
        err.exception.close()
        self.assertEqual(self.control.settings()['killed'],1)

    def test_kill_stops_busy_worker_and_requires_resume(self):
        (self.root/'cluster_test.py').write_text("import time\ntime.sleep(100)\n")
        self.control.command('pause'); self.control.command('run')
        self.launch()
        self.wait_for(lambda:any(p['name']=='worker-1' for p in self.control.snapshot()['pulses']))
        self.control.command('kill')
        self.wait_for(lambda:self.control.snapshot()['jobs'][0]['state']=='cancelled')
        time.sleep(.6)
        self.assertEqual(len(self.control.snapshot()['jobs']),1)

    def test_guardian_resurrects_failed_controller(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        self.env['XEMBRA_DASHBOARD_PORT']=str(port)
        (self.root/'cluster_test.py').write_text("import time\ntime.sleep(100)\n")
        self.control.command('pause')
        self.control.command('run')
        self.launch('guardian')
        def controller_pid():
            return next((p['pid'] for p in self.control.snapshot()['pulses'] if p['name']=='controller'),None)
        first=self.wait_for(controller_pid)
        self.wait_for(lambda:any(p['name']=='worker-1' for p in self.control.snapshot()['pulses']))
        # This PID belongs to the just-created isolated test guardian's controller.
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(first),'/F'],check=True,capture_output=True)
        else:
            os.kill(first,signal.SIGKILL)
        second=self.wait_for(lambda:controller_pid() if controller_pid()!=first else None,timeout=22)
        self.assertNotEqual(first,second)
        self.assertTrue(any(c['component']=='controller' for c in self.control.snapshot()['crashes']))
        job=self.control.snapshot()['jobs'][0]
        self.assertEqual(job['state'],'failed')
        self.assertIn('heartbeat lost',job['detail'])

    @unittest.skipIf(os.name=='nt','POSIX SIGTERM shutdown is exercised inside Docker')
    def test_guardian_restart_preserves_pause_and_kill_state(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        self.env['XEMBRA_DASHBOARD_PORT']=str(port)
        self.control.command('pause')
        for killed in [0,1]:
            if killed:self.control.command('kill')
            for _ in range(2):
                self.launch('guardian')
                self.wait_for(lambda:self.control.alive('guardian'))
                self.assertEqual(self.control.settings()['killed'],killed)
                self.assertEqual(self.control.settings()['enabled'],0)
                self.proc.terminate();self.proc.wait(timeout=10)
                self.assertFalse(self.control.alive('guardian'))


if __name__=='__main__': unittest.main()
