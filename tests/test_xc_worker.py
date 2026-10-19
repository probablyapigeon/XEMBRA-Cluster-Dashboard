import json
from pathlib import Path
import tempfile
import unittest

import xc_runtime as xc
from xc_worker import EVENTS, run_batch

SOURCE = Path(__file__).resolve().parents[1] / 'xc' / 'mara_memory.xc'


class XCWorkerTests(unittest.TestCase):
    def test_persistence_equals_uninterrupted_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            first = run_batch(directory, SOURCE)
            second = run_batch(directory, SOURCE)
            expected = xc.Runtime(xc.compile_file(SOURCE))
            expected.run_events(EVENTS * 2)
            checkpoint = json.loads((Path(directory)/'xc_checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(checkpoint, expected.export_checkpoint())
            self.assertEqual(first['events_total'], 8)
            self.assertEqual(second['events_before'], 8)
            self.assertEqual(second['events_total'], 16)
            self.assertEqual(second['state'], expected.state_dict())

    def test_private_checkpoints_and_bounded_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a', Path(directory)/'b'
            for _ in range(10):
                report = run_batch(a, SOURCE)
            fresh = run_batch(b, SOURCE)
            self.assertEqual(report['stored_memories']['Episodic'], 64)
            self.assertEqual(fresh['events_total'], 8)
            self.assertEqual(report['events_total'], 80)
            self.assertLess(report['last_16_checkpoint_bytes'], report['checkpoint_bytes'])

    def test_source_mismatch_preserves_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_batch(root, SOURCE)
            before = (root/'xc_checkpoint.json').read_bytes()
            changed = root/'changed.xc'
            changed.write_text(SOURCE.read_text(encoding='utf-8').replace('seed = 17701','seed = 17702'), encoding='utf-8')
            with self.assertRaises(xc.XCValidationError):
                run_batch(root, changed)
            self.assertEqual(before, (root/'xc_checkpoint.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
