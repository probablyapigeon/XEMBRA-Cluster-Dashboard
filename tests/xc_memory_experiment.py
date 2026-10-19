"""Read-only XC runtime evaluation: persistence, bounded memory and decision fidelity.

No runtime changes, training, live bridge calls, or network actions.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import statistics


def size(value):
    return len(json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))


def evaluate(root, output, holdout=False):
    runtime_path = root / 'runtime' / 'xc.py'
    spec = importlib.util.spec_from_file_location('xc_evaluation_target', runtime_path)
    xc = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = xc
    spec.loader.exec_module(xc)
    source = (root / 'mara_memory.xc').read_text(encoding='utf-8')
    # All histories fit in this reference store. Only this test IR differs from Mara.
    ir = xc.compile_text(source.replace('capacity = 64', 'capacity = 4096'), 'evaluation-reference.xc')
    cases = []
    for seed in (range(100, 110) if holdout else range(4)):
        rng = random.Random(seed)
        for delay in ((2, 8, 32, 128) if holdout else (0, 1, 4, 16, 80)):
            prefix = [rng.choice(['PlayerGift', 'PlayerTalk']) for _ in range(4)]
            for marker in ('PlayerGift', 'PlayerHit'):
                events = prefix + [marker] + ['PlayerTalk'] * delay
                past = xc.Runtime(ir)
                for event in events:
                    past.dispatch(event)
                memory = json.loads(json.dumps(past.export_memory()))
                checkpoint = json.loads(json.dumps(past.export_checkpoint()))
                reference = past.dispatch('PlayerTalk')
                reloaded = xc.Runtime(ir, checkpoint_data=checkpoint)
                resumed = reloaded.dispatch('PlayerTalk')
                if resumed != reference or reloaded.export_memory() != past.export_memory():
                    raise AssertionError('Checkpoint resume differs from uninterrupted execution')
                case = {'seed': seed, 'neutral_delay': delay, 'marker': marker,
                        'events': events, 'reference_action': reference['action'],
                        'toy_target_action': 'REFUSE' if marker == 'PlayerHit' else 'HELP',
                        'raw_event_log_json_bytes': size(events), 'modes': {}}
                for mode in ('full_checkpoint', 'checkpoint_last_16', 'checkpoint_last_4',
                             'full_episodic', 'last_64', 'last_16', 'last_4', 'state_only', 'no_history'):
                    if mode in ('state_only', 'no_history', 'full_checkpoint') or mode.startswith('checkpoint_last_'):
                        if mode != 'no_history':
                            reduced = copy.deepcopy(checkpoint)
                            if mode == 'state_only':
                                reduced['memory']['stores']['Episodic'] = []
                            elif mode.startswith('checkpoint_last_'):
                                capacity = int(mode.rsplit('_', 1)[1])
                                reduced['memory']['stores']['Episodic'] = reduced['memory']['stores']['Episodic'][-capacity:]
                            rt = xc.Runtime(ir, checkpoint_data=reduced)
                            stored_bytes = size(reduced)
                        else:
                            rt = xc.Runtime(ir)
                            stored_bytes = 0
                    else:
                        reduced = copy.deepcopy(memory)
                        if mode.startswith('last_'):
                            capacity = int(mode.split('_')[1])
                            reduced['stores']['Episodic'] = reduced['stores']['Episodic'][-capacity:]
                        rt = xc.Runtime(ir, memory_data=reduced)
                        stored_bytes = size(reduced)
                    entries = len(rt.memory_stores['Episodic'])
                    response = rt.dispatch('PlayerTalk')
                    case['modes'][mode] = {
                        'action': response['action'],
                        'matches_full_replay': response['action'] == reference['action'],
                        'toy_task_correct': response['action'] == case['toy_target_action'],
                        'stored_json_bytes': stored_bytes, 'entries_before_probe': entries,
                        'hostile_signal': response['measures']['hostile_memory']}
                cases.append(case)
    summary = {}
    for mode in cases[0]['modes']:
        values = [case['modes'][mode] for case in cases]
        summary[mode] = {
            'agreement_with_full_replay_percent': 100*sum(v['matches_full_replay'] for v in values)/len(values),
            'toy_task_accuracy_percent': 100*sum(v['toy_task_correct'] for v in values)/len(values),
            'mean_stored_json_bytes': statistics.mean(v['stored_json_bytes'] for v in values),
            'max_stored_entries': max(v['entries_before_probe'] for v in values)}
    report = {
        'runtime_path': str(runtime_path),
        'runtime_sha256': hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'case_count': len(cases), 'checkpoint_exact_resume_cases': len(cases),
        'dataset': 'confirmation: new seeds and delays' if holdout else 'exploratory: seeds 0-3',
        'reference_toy_task_accuracy_percent': 100*sum(c['reference_action']==c['toy_target_action'] for c in cases)/len(cases),
        'mean_raw_event_log_json_bytes': statistics.mean(c['raw_event_log_json_bytes'] for c in cases),
        'summary': summary,
        'limits': [
            'Handwritten Mara policy, not trained or validated psychology.',
            'Toy task is HELP after a gift and REFUSE after a hit despite neutral talk delays; this is an evaluation rule, not a universal correct social response.',
            'Agreement measures preservation of full replay behavior, not intelligence or correctness.',
            'Episodic modes restore memory into initial state; state_only restores state and counters with episodic memory removed.',
            'Reference capacity raised to 4096 in test IR only. Bounded modes truncate persisted entries without changing runtime code.',
            'JSON byte measurements count saved artifacts, not shared runtime/program storage. Raw event log requires replay; state/memory stores trade storage for prepared state.',
            'Inputs are symbolic events, not raw images/audio/text observations. No claim of compression of those raw modalities.',
            'No IIT, consciousness, chaos or general compression claim is tested.'],
        'cases': cases}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'metrics.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('logs/xc-memory-evaluation'))
    parser.add_argument('--holdout', action='store_true')
    arguments = parser.parse_args()
    evaluate(arguments.runtime_root, arguments.output, arguments.holdout)
