"""Recompute retrieval traces for the searches of recorded trajectories (needs the local retrieval server, no model).

For every recorded search the same arguments are sent again with trace=True. The recomputed final results must
have exactly the recorded chunk ids (primary and attached); otherwise nothing is written for that case.
"""
import argparse
import glob
import json
from pathlib import Path
from finmhqa.agent.pilot_tools import request_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cases', nargs='+', required=True, help='globs of folders with trajectory.jsonl')
    p.add_argument('--url', default='http://127.0.0.1:8100')
    a = p.parse_args()
    for folder in sorted({Path(f) for g in a.cases for f in glob.glob(g) if (Path(f) / 'trajectory.jsonl').exists()}):
        events = [json.loads(l) for l in (folder / 'trajectory.jsonl').read_text().splitlines()]
        rows, pending, ok = [], None, True
        for e in events:
            if e['event'] == 'tool_request' and e['tool'] == 'search':
                pending = e
            elif e['event'] == 'tool_response' and e['tool'] == 'search' and pending:
                body = request_json(a.url + '/search', dict(pending['args'], top_k=5, n_cand=30, expand_parent=True, trace=True))
                same = [c['chunk_id'] for c in body['results']] == [c['chunk_id'] for c in e['response'].get('results', [])]
                ok &= same
                rows.append({'index': len(rows), 'args': pending['args'], 'trace': body['trace'], 'matches_recorded': same})
                pending = None
        if rows and ok:
            (folder / 'retrieval_traces.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        print(folder, f'{len(rows)} searches', 'written' if rows and ok else 'NOT written: recomputed results differ from the recording')


if __name__ == '__main__':
    main()
