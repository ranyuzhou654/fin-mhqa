"""P2.1 offline estimate of context-compression strategies on recorded prompts; no model calls.

For every recorded model input (planner and verifier) the history is rewritten by each strategy and measured.
Risk: a chunk the model referenced in that step's decision (plan evidence_ids, final citations, context anchor)
whose full text the strategy would have hidden in that step's input.
  S0 as recorded
  S1 slim      keep only reasoning-relevant chunk fields and response fields (lossless for reasoning)
  S2 dedup     S1, and a chunk already shown earlier in the same input becomes a reference
  S3 compact   S2, and chunks in older observations that are not pinned (current plan evidence_ids, candidate
               citations) keep only their title line; the latest observation keeps full text (lossy)
  S4 fold      S2, and a table_kv chunk whose table_md twin (same parent table and part) was already shown, in an
               earlier observation or in the same response, becomes a reference to the twin; over all 2,780
               corpus pairs every kv number is in the md text. Folding only looks backwards, so an earlier part
               of the input never changes when observations are added (prefix-stable, keeps provider caching)
"""
import argparse
from copy import deepcopy
import glob
import json
from pathlib import Path

CHUNK_KEEP = ('chunk_id', 'company', 'chunk_type', 'page_start', 'page_end', 'table_title', 'unit', 'text')
RESPONSE_DROP = ('dataset_sha256', 'index_admission', 'results')


def slim_chunk(c):
    return {k: c[k] for k in CHUNK_KEEP if k in c}


def title_line(c):
    lines = c.get('text', '').split('\n')
    head = next((l for l in lines[1:] if l.startswith('表：')), lines[1] if len(lines) > 1 else lines[0])
    return head[:80]


def transform(items, strategy, pinned):
    if strategy == 'S0':
        return items
    items = deepcopy(items)
    obs_idx = [i for i, x in enumerate(items) if isinstance(x, dict) and x.get('observations')]
    latest = obs_idx[-1] if obs_idx else None
    shown, twins = set(), {}
    for i, x in enumerate(items):
        if not isinstance(x, dict) or not x.get('observations'):
            continue
        for o in x['observations']:
            r = o['response']
            if 'results' not in r:
                continue
            new = []
            twins.update({(c.get('parent_table_id'), c.get('part')): c['chunk_id'] for c in r['results'] if c.get('chunk_type') == 'table_md'})
            for c in r['results']:
                cid = c['chunk_id']
                if strategy in ('S2', 'S3', 'S4') and cid in shown:
                    new.append({'chunk_id': cid, 'repeat': '同一证据块已在前文给出'})
                    continue
                if strategy == 'S4' and c.get('chunk_type') == 'table_kv' and (c.get('parent_table_id'), c.get('part')) in twins:
                    new.append({'chunk_id': cid, 'chunk_type': 'table_kv', 'same_as': twins[(c.get('parent_table_id'), c.get('part'))],
                                'note': '与同表同分段的 table_md 内容相同，见该块'})
                    continue
                if strategy == 'S3' and i != latest and cid not in pinned:
                    new.append({**{k: c[k] for k in ('chunk_id', 'chunk_type', 'page_start') if k in c},
                                'title': title_line(c), 'text_omitted': '旧观测，全文未展开'})
                else:
                    new.append(slim_chunk(c))
                    shown.add(cid)
            o['response'] = {**{k: v for k, v in r.items() if k not in RESPONSE_DROP}, 'results': new}
    return items


def full_text_visible(items):
    chunks = [c for x in items if isinstance(x, dict) for o in x.get('observations', []) for c in o['response'].get('results', [])]
    full = {c['chunk_id'] for c in chunks if 'text' in c}
    return full | {c['chunk_id'] for c in chunks if c.get('same_as') in full}   # a folded kv is readable through its twin


def referenced(action):
    ids = set()
    for t in action.get('plan') or []:
        ids.update(t.get('evidence_ids', []))
    if action.get('final'):
        ids.update(action['final'].get('citations') or [])
    if action.get('context'):
        ids.add(action['context']['anchor_id'])
    for i in action.get('issues') or []:
        ids.update(i.get('evidence_ids') or [])
    return ids


def analyze(pattern):
    rows = []
    for case in sorted(c for c in glob.glob(pattern + '/*/*/') if Path(c, 'trajectory.jsonl').exists()):
        decisions = {e['step']: e for e in map(json.loads, open(case + 'trajectory.jsonl')) if e['event'] == 'teacher_decision'}
        for step_dir in sorted(glob.glob(case + 'step_*/')):
            step = int(step_dir.rstrip('/').split('_')[-1])
            if step not in decisions or not Path(step_dir, 'usage.json').exists():
                continue
            text = Path(step_dir, 'input.txt').read_text()
            head, body = text[:text.index('{"question"')], json.loads(text[text.index('{"question"'):])
            h = body['history']
            role = decisions[step]['role']
            if isinstance(h, dict):   # verifier payload
                items = h.get('history', [])
                pinned = {i for t in h.get('plan', []) for i in t.get('evidence_ids', [])} | set(h['candidate'].get('citations') or [])
            else:
                items = h
                plans = [x['plan'] for x in items if isinstance(x, dict) and 'plan' in x]
                pinned = {i for t in (plans[-1] if plans else []) for i in t.get('evidence_ids', [])}
            refs = referenced(decisions[step]['action'])
            sizes, hidden = {}, {}
            for s in ('S0', 'S1', 'S2', 'S3', 'S4'):
                new_items = transform(items, s, pinned)
                new_h = {**h, 'history': new_items} if isinstance(h, dict) else new_items
                sizes[s] = len(head) + len(json.dumps({**body, 'history': new_h}, ensure_ascii=False))
                observed = {c['chunk_id'] for x in items if isinstance(x, dict) for o in x.get('observations', [])
                            for c in o['response'].get('results', [])}
                hidden[s] = len((refs & observed) - full_text_visible(new_items) - {c for c in refs if c not in observed})
            usage = json.loads(Path(step_dir, 'usage.json').read_text())
            rows.append({'role': role, 'step': step, 'tokens': usage['input_tokens'], 'sizes': sizes, 'hidden_refs': hidden,
                         'refs': len(refs)})
    return rows


def summarize(rows):
    ratio = sum(r['tokens'] for r in rows) / sum(r['sizes']['S0'] for r in rows)   # tokens per char, measured
    out = {'model_inputs': len(rows), 'tokens_per_char': round(ratio, 3), 'strategies': {}}
    for s in ('S0', 'S1', 'S2', 'S3', 'S4'):
        chars = sum(r['sizes'][s] for r in rows)
        out['strategies'][s] = {
            'estimated_input_tokens': round(chars * ratio),
            'reduction_vs_S0': round(1 - chars / sum(r['sizes']['S0'] for r in rows), 3),
            'late_step_reduction': round(1 - sum(r['sizes'][s] for r in rows if r['step'] >= 4) / max(1, sum(r['sizes']['S0'] for r in rows if r['step'] >= 4)), 3),
            'inputs_with_hidden_reference': sum(r['hidden_refs'][s] > 0 for r in rows),
            'hidden_references': sum(r['hidden_refs'][s] for r in rows),
        }
    out['referenced_chunks_total'] = sum(r['refs'] for r in rows)
    out['by_role'] = {role: {s: round(1 - sum(r['sizes'][s] for r in rows if r['role'] == role) / sum(r['sizes']['S0'] for r in rows if r['role'] == role), 3)
                             for s in ('S1', 'S2', 'S3', 'S4')} for role in sorted({r['role'] for r in rows})}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs', nargs='+', required=True, help='glob patterns of graph-arm run folders')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    report = {}
    for pattern in a.runs:
        rows = [r for d in sorted(glob.glob(pattern)) for r in analyze(d)]
        report[pattern] = summarize(rows)
        print(pattern, json.dumps(report[pattern]['strategies'], ensure_ascii=False))
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
