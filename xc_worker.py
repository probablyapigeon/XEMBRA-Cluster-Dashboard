"""One bounded XC event batch, with durable per-worker state and memory."""
import copy
import json
import os
from pathlib import Path
import time

import xc_runtime as xc

EVENTS = ['PlayerGift', 'PlayerTalk', 'PlayerHit', 'PlayerTalk',
          'PlayerIgnore', 'PlayerTalk', 'PlayerGift', 'PlayerTalk']


def packed(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(packed(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def run_batch(folder, source):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    ir = xc.compile_file(source)
    checkpoint_path = folder / 'xc_checkpoint.json'
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.exists() else None
    runtime = xc.Runtime(ir, checkpoint_data=checkpoint)
    start = runtime.event_counter
    trace = [runtime.dispatch(event) for event in EVENTS]
    checkpoint = runtime.export_checkpoint()
    # A read-only comparison: would fewer memories preserve the NEXT probe action?
    reduced = copy.deepcopy(checkpoint)
    for entries in reduced['memory']['stores'].values():
        del entries[:-16]
    full_probe = xc.Runtime(ir, checkpoint_data=checkpoint).dispatch('PlayerTalk')
    short_probe = xc.Runtime(ir, checkpoint_data=reduced).dispatch('PlayerTalk')
    report = {'workload': 'xc', 'runtime_version': xc.RUNTIME_VERSION,
              'program': ir['program']['name'], 'source_sha256': ir['source']['sha256'],
              'completed_at': time.time(), 'events_before': start,
              'events_total': runtime.event_counter, 'batch_events': EVENTS,
              'action': runtime.action, 'state': runtime.state_dict(),
              'actions_this_batch': [step['action'] for step in trace],
              'stored_memories': {name: len(entries) for name, entries in runtime.memory_stores.items()},
              'checkpoint_bytes': len(packed(checkpoint)),
              'last_16_checkpoint_bytes': len(packed(reduced)),
              'next_probe_full_action': full_probe['action'],
              'next_probe_last_16_action': short_probe['action'],
              'next_probe_matches': full_probe['action'] == short_probe['action'],
              'limits': 'Scripted demonstration events and handwritten policy, not learned intelligence. '
                        'Last-16 comparison tests one hypothetical next event only; the saved checkpoint retains full configured memory.'}
    save(checkpoint_path, checkpoint)
    save(folder / 'xc_metrics.json', report)
    return report


if __name__ == '__main__':
    print(json.dumps(run_batch(Path.cwd(), Path(__file__).resolve().parent / 'xc' / 'mara_memory.xc'), indent=2))
