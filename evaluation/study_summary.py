"""Pair raw exported trials only when task, participant and outcome match."""
import json
import sys


def summarize(runs):
    groups = {}
    for r in runs:
        if not r.get('finished_at'):
            continue
        groups.setdefault((r['participant'], r['task_id']), {}).setdefault(r['mode'], []).append(r)
    pairs = []
    for (participant, task), modes in groups.items():
        if len(modes.get('manual', [])) != 1 or len(modes.get('agent', [])) != 1:
            continue
        m, a = modes['manual'][0], modes['agent'][0]
        if m['outcome'] != 'ready' or a['outcome'] != 'ready' or m['execution_mode'] != 'human' or a['execution_mode'] == 'scripted':
            continue
        pairs.append({'participant': participant, 'task_id': task, 'manual_seconds': m['elapsed_seconds'],
                      'agent_seconds': a['elapsed_seconds'], 'difference_seconds': m['elapsed_seconds'] - a['elapsed_seconds'],
                      'manual_actions': m['actions'], 'agent_actions': a['actions'],
                      'agent_execution_mode': a['execution_mode'], 'price_parity_asserted': False})
    return {'completed_runs': sum(bool(r.get('finished_at')) for r in runs),
            'failed_or_abandoned': sum(r.get('outcome') in ('failed', 'abandoned') for r in runs),
            'pairs': pairs, 'scope': 'Participant-entered basket-readiness trials. No independent participation verification, no live/snapshot price parity or general time-saving claim.'}


if __name__ == '__main__':
    print(json.dumps(summarize(json.load(open(sys.argv[1]))), indent=2))
