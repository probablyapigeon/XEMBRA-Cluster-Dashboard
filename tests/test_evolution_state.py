"""Real workload regression: QT and compression persist across process restarts."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(all(importlib.util.find_spec(n) for n in ['numpy','pandas','matplotlib','yfinance']),
                     'Install requirements.txt for the real evolution test')
class EvolutionStateTest(unittest.TestCase):
    def test_real_workload_resumes_qt_and_compression(self):
        with tempfile.TemporaryDirectory() as temp:
            env=dict(os.environ,XEMBRA_DAEMON='1',PYTHONIOENCODING='utf-8')
            def run():
                result=subprocess.run([sys.executable,str(ROOT/'cluster_test.py')],cwd=temp,env=env,
                    capture_output=True,text=True,encoding='utf-8',timeout=45)
                self.assertEqual(result.returncode,0,result.stderr+'\n'+result.stdout[-2000:])
                return json.loads((Path(temp)/'evolution_state.json').read_text())
            first=run();second=run()
            self.assertEqual(first['generation'],1);self.assertEqual(second['generation'],2)
            self.assertGreater(second['alpha'][0],first['alpha'][0])
            self.assertGreater(second['qt'][0],first['qt'][0])
            self.assertEqual(second['artifact']['text_limit'],120)


if __name__=='__main__':unittest.main()
