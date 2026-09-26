"""Context policy compact_v1: a lossless, prefix-stable view of tool history for model inputs.

The graph keeps the full history (and the trajectory logs full tool responses); only what the model reads changes:
1. chunk fields irrelevant to reasoning (hashes, extraction paths, quality flags, retrieval scores) are dropped;
2. a chunk already shown earlier in the input becomes a short reference;
3. a table_kv chunk whose table_md twin (same parent table and part) has already been shown becomes a reference
   to the twin: over all 2,780 corpus pairs every kv number appears in the md text (docs/experiments/CONTEXT_V1_PLAN.md).
Every rewrite looks only backwards, so an entry never changes once later observations are appended.
"""
from copy import deepcopy

CHUNK_KEEP = ('chunk_id', 'company', 'chunk_type', 'page_start', 'page_end', 'table_title', 'unit', 'text')
RESPONSE_DROP = ('dataset_sha256', 'index_admission')
REPEAT = '同一证据块已在前文给出'
SAME_AS = '与同表同分段的 table_md 内容相同，见该块'
POLICY_NOTE = '''
历史中的工具返回已做无损压缩：与推理无关的元数据字段已去掉；标注 repeat 的证据块在前文已完整给出；table_kv 块若标注 same_as，其内容与所指的 table_md 块相同，数值和行名以该块为准。引用时 kv 或 md 的 chunk_id 均可使用。'''


def compact_history(items):
    items = deepcopy(items)
    shown, twins = set(), {}
    for x in items:
        if not isinstance(x, dict):
            continue
        for o in x.get('observations') or []:
            r = o.get('response') or {}
            if 'results' not in r:
                continue
            twins.update({(c.get('parent_table_id'), c.get('part')): c['chunk_id'] for c in r['results']
                          if c.get('chunk_type') == 'table_md' and c.get('parent_table_id')})
            new = []
            for c in r['results']:
                cid = c['chunk_id']
                twin = twins.get((c.get('parent_table_id'), c.get('part'))) if c.get('chunk_type') == 'table_kv' else None
                if cid in shown:
                    new.append({'chunk_id': cid, 'repeat': REPEAT})
                elif twin and twin in shown | {m['chunk_id'] for m in r['results'] if m.get('chunk_type') == 'table_md'}:
                    new.append({'chunk_id': cid, 'chunk_type': 'table_kv', 'same_as': twin, 'note': SAME_AS})
                else:
                    new.append({k: c[k] for k in CHUNK_KEEP if k in c})
                    shown.add(cid)
            o['response'] = {**{k: v for k, v in r.items() if k not in RESPONSE_DROP and k != 'results'}, 'results': new}
    return items
