import json
from pathlib import Path
import subprocess
import time
import unittest
import test_network_cluster as fixtures


class WatchdogTest(unittest.TestCase):
    setUp=fixtures.NetworkIntegration.setUp
    launch=fixtures.NetworkIntegration.launch
    request_ready=fixtures.NetworkIntegration.request_ready
    wait_for=fixtures.NetworkIntegration.wait_for

    def tearDown(self):
        try:
            self.client.call('/api/control',{'action':'kill'})
            status=self.root/'a'/'watchdog'/'status.json'
            if status.exists():
                self.wait_for(lambda:json.loads(status.read_text()).get('phase')=='killed' and
                              json.loads(status.read_text()).get('agent_pid') is None,timeout=10)
        finally:fixtures.NetworkIntegration.tearDown(self)

    def test_resurrection_and_durable_local_and_cluster_kill(self):
        ticket=self.client.call('/api/issue-join',{'node':'a'})['result']['ticket']
        from network_cluster import Client
        credential=Client(self.client.url,'').call('/api/join',{'node':'a','ticket':ticket})['result']['token']
        tokenfile=self.root/'a.token';tokenfile.write_text(credential)
        self.launch(['watchdog','--node','a','--state',str(self.root/'a'),'--source',str(self.source),'--url',self.client.url],tokenfile)
        path=self.root/'a'/'watchdog'/'status.json'
        def state():
            try:return json.loads(path.read_text())
            except (OSError,ValueError):return {}
        first=self.wait_for(lambda:state().get('agent_pid'))
        subprocess.run(['taskkill','/PID',str(first),'/F'],check=True,capture_output=True)
        second=self.wait_for(lambda:state().get('agent_pid') if state().get('agent_pid') not in [None,first] else None,timeout=25)
        self.assertNotEqual(first,second)
        latch=self.root/'a'/'node.killed';latch.write_text('test local kill')
        self.wait_for(lambda:state().get('phase')=='killed' and state().get('agent_pid') is None)
        time.sleep(1.2);self.assertIsNone(state().get('agent_pid'))
        latch.unlink();self.wait_for(lambda:state().get('agent_pid'))
        self.client.call('/api/control',{'action':'kill'})
        self.wait_for(lambda:latch.exists() and state().get('phase')=='killed')
        self.client.call('/api/control',{'action':'resume'})
        time.sleep(1.2);self.assertIsNone(state().get('agent_pid'))
        latch.unlink();self.wait_for(lambda:state().get('agent_pid'))


if __name__=='__main__':unittest.main()
