"""Retrieval ranking metrics (Hit / Recall / MRR / nDCG @K) per pipeline stage, plus agent evidence coverage.

Two label sets, both fixed before this script existed:
- dev30: the 30 source-page questions with source-equivalence v2 labels (docs/system/M2_V2_RESULTS.md).
- facts: the 68 distinct facts behind the Dev 50 questions, with the standard per-fact queries of the benchmark_v1
  S4 reachability check. Two relevance rules: `strict` (that check's rule: the ledger chunk, or a chunk of the same
  report with the raw value and the metric name) and `value` (any chunk of the same report with the exact raw value,
  to the cent; this also admits the summary table, which says 总资产 where the statement says 资产总计, and totals
  equal by identity such as 负债和所有者权益总计). Strict is a lower bound; value is the sensitivity check.
  Test facts are never touched.

Stages come from one traced search (the trace lists every candidate, so metrics go down to K=30):
dense, bm25 (raw channel rankings), rrf (fusion, k=60), and each followed by the cross-encoder reranker.
The production pipeline is rrf+rerank; the agent sees its top 5.

  collect  needs the local retrieval server (no model quota); writes raw traces under --out (refuses to overwrite)
  score    offline; metrics with 95% bootstrap intervals and paired stage comparisons
  agent    offline; how much of each Dev question's gold evidence the agent's own searches retrieved
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from finmhqa.bench.guard import GOLD
from finmhqa.bench.quality import reachability_query, reachable
from finmhqa.retrieval.metrics import KS, aggregate, first_rank, hop_metrics, paired_diff, query_metrics

sys.path.insert(0, str(Path(__file__).parent))
from analyze_verifier import correct  # noqa: E402

DEV30 = Path('data/eval/retrieval_dev30.v2.bound.jsonl')
DEV30_RESULTS = Path('data/eval/retrieval_dev30.v2.results.json')
FACTS = Path('data/bench/ledger_v1/facts.jsonl')
CHUNKS = Path('index/chunk_meta.jsonl')
STAGES = {'dense': ('dense', 'dense'), 'bm25': ('bm25', 'bm25'), 'rrf': ('hybrid', 'fused'),
          'dense+rerank': ('dense', 'reranked'), 'bm25+rerank': ('bm25', 'reranked'), 'rrf+rerank': ('hybrid', 'reranked')}
HEADLINE = [f'{m}@{k}' for m in ('hit', 'recall', 'mrr', 'ndcg') for k in (1, 3, 5, 10, 30)]
COMPARISONS = [('rrf', 'rrf+rerank'), ('dense', 'dense+rerank'), ('bm25', 'bm25+rerank'),
               ('dense', 'rrf'), ('bm25', 'rrf'), ('dense+rerank', 'rrf+rerank')]


def load_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def dev_facts():
    gold = load_jsonl(GOLD / 'dev_gold.jsonl')
    if any(g['split'] != 'dev' for g in gold):
        raise ValueError('expected Dev gold only')
    ledger = {f['fact_id']: f for f in load_jsonl(FACTS)}
    return gold, {i: ledger[i] for g in gold for i in g['facts']}


def fact_relevant(fact, chunks, rule='strict'):
    if rule == 'value':
        return sorted(c['chunk_id'] for c in chunks if c['source_sha256'] == fact['source_sha256'] and fact['raw_value'] in c['text'])
    return sorted(c['chunk_id'] for c in chunks if reachable(fact, [c]) == 1)


def build_queries(label_set):
    if label_set == 'dev30':
        return [{'query_id': q['query_id'], 'args': {'query': q['query']}, 'relevant': q['gold_chunk_ids'],
                 'modes': ['dense', 'bm25', 'hybrid']} for q in load_jsonl(DEV30)]
    chunks = load_jsonl(CHUNKS)
    _, facts = dev_facts()
    out = []
    for fid, fact in sorted(facts.items()):
        args = reachability_query(fact)
        out.append({'query_id': fid, 'args': {'query': args['query'], 'company': args['company']},
                    'relevant': fact_relevant(fact, chunks), 'relevant_value': fact_relevant(fact, chunks, 'value'),
                    'modes': ['hybrid']})
    return out


def collect(a):
    from finmhqa.agent.pilot_tools import request_json
    from finmhqa.harness.provenance import git_state, retrieval_lock
    a.out.mkdir(parents=True, exist_ok=False)
    queries = build_queries(a.set)
    health = request_json(a.url + '/health')
    (a.out / 'queries.jsonl').write_text(''.join(json.dumps(q, ensure_ascii=False) + '\n' for q in queries))
    manifest = {'set': a.set, 'n_queries': len(queries), 'n_cand': 30, 'top_k': 5, 'health': health, 'git': git_state(),
                'started': time.strftime('%Y-%m-%dT%H:%M:%S')}
    (a.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    with (a.out / 'traces.jsonl').open('x') as journal:
        for q in queries:
            for mode in q['modes']:
                start = time.monotonic()
                with retrieval_lock():
                    body = request_json(a.url + '/search', dict(q['args'], mode=mode, top_k=5, n_cand=30, trace=True))
                if body['dataset_sha256'] != health['admission']['dataset_sha256']:
                    raise ValueError('index changed during collection')
                primary = [r['chunk_id'] for r in body['results'] if not r['attached']]
                journal.write(json.dumps({'query_id': q['query_id'], 'mode': mode, 'seconds': round(time.monotonic() - start, 3),
                                          'primary': primary, 'trace': body['trace']}, ensure_ascii=False) + '\n')
                journal.flush()
                print(q['query_id'], mode, round(time.monotonic() - start, 1), 's', flush=True)
    manifest['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    (a.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


def stage_rankings(traces):
    """{(query_id, stage): [chunk ids in rank order]} from traced searches."""
    out = {}
    for t in sorted(traces, key=lambda t: t['mode'] != 'hybrid'):   # single-channel traces win for raw channels
        for stage, (mode, key) in STAGES.items():
            raw_channel = stage in ('dense', 'bm25')   # a hybrid trace also carries both raw channel rankings
            if (t['mode'] == mode or raw_channel) and t['trace'].get(key) is not None:
                out[(t['query_id'], stage)] = [c['chunk_id'] for c in t['trace'][key]]
    return out


def checks(traces, label_set):
    """The trace must reproduce what the pipeline returned, and dev30 must reproduce the recorded Hit@5 run."""
    out = {'rerank_top5_equals_results': all([c['chunk_id'] for c in t['trace']['reranked'][:5]] == t['primary'] for t in traces)}
    by = {(t['query_id'], t['mode']): t['trace'] for t in traces}
    same = [by[(q, 'hybrid')][ch] == by[(q, ch)][ch] for (q, m) in by if m == 'hybrid' for ch in ('dense', 'bm25') if (q, ch) in by]
    if same:
        out['raw_channels_same_in_hybrid_and_single_mode'] = f'{sum(same)}/{len(same)}'
    if label_set == 'dev30' and DEV30_RESULTS.exists():
        recorded = {(r['query_id'], r['mode']): [c['chunk_id'] for c in r['results'] if not c['attached']][:5]
                    for r in json.loads(DEV30_RESULTS.read_text())['results']}
        out['same_top5_as_recorded_dev30_v2'] = sum(recorded.get((t['query_id'], t['mode'])) == t['primary'] for t in traces)
        out['recorded_searches'] = len(traces)
    return out


def score(a):
    queries = load_jsonl(a.runs / 'queries.jsonl')
    traces = load_jsonl(a.runs / 'traces.jsonl')
    label_set = json.loads((a.runs / 'manifest.json').read_text())['set']
    rankings = stage_rankings(traces)
    stages = [s for s in STAGES if all((q['query_id'], s) in rankings for q in queries)]
    report = {'set': label_set, 'queries': len(queries), 'checks': checks(traces, label_set)}
    rules = {'strict': 'relevant', 'value': 'relevant_value'} if 'relevant_value' in queries[0] else {'strict': 'relevant'}
    for rule, field in rules.items():
        report[rule if len(rules) > 1 else 'labels'] = score_rule(queries, field, rankings, stages, label_set)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    for rule in (('strict', 'value') if len(rules) > 1 else ('labels',)):
        print_table(report[rule], f"{label_set} [{rule}]")
    print('checks', report['checks'])


def score_rule(queries, field, rankings, stages, label_set):
    per = {s: [dict(query_metrics(rankings[(q['query_id'], s)], q[field]), query_id=q['query_id']) for q in queries]
           for s in stages}
    report = {'relevant_per_query': {'median': statistics.median(len(q[field]) for q in queries),
                                     'min': min(len(q[field]) for q in queries), 'max': max(len(q[field]) for q in queries)},
              'no_relevant_chunk': [q['query_id'] for q in queries if not q[field]],
              'stages': {s: aggregate(per[s], HEADLINE) for s in stages},
              'not_found_in_30': {s: [r['query_id'] for r in per[s] if r['first_rank'] is None] for s in stages},
              'comparisons': {f'{x} -> {y}': {m: paired_diff([r[m] for r in per[x]], [r[m] for r in per[y]])
                                             for m in ('hit@1', 'hit@5', 'mrr@10', 'ndcg@10')}
                              for x, y in COMPARISONS if x in per and y in per}}
    if label_set == 'facts':
        report['questions'] = question_level(per, stages)
    report['per_query'] = {s: [{k: r[k] for k in ('query_id', 'first_rank', 'n_relevant')} for r in per[s]] for s in stages}
    return report


def question_level(per, stages):
    """Standard per-fact queries, aggregated per Dev question: fraction of hops found and all hops found at K."""
    gold, _ = dev_facts()
    multi = [g for g in gold if g['facts']]
    out = {'questions': len(multi), 'excluded_unanswerable': len(gold) - len(multi)}
    for s in stages:
        ranks = {r['query_id']: r['first_rank'] for r in per[s]}
        rows = [hop_metrics([ranks[f] for f in g['facts']]) for g in multi]
        out[s] = aggregate(rows, [f'{m}@{k}' for m in ('hop_recall', 'all_hops') for k in (1, 5, 10, 30)])
    return out


def print_table(report, title):
    cols = ['hit@1', 'hit@5', 'hit@10', 'recall@5', 'recall@10', 'mrr@10', 'ndcg@5', 'ndcg@10', 'hit@30']
    print(title)
    print('stage'.ljust(14) + ''.join(c.rjust(10) for c in cols))
    for s, m in report['stages'].items():
        print(s.ljust(14) + ''.join(f"{m[c]['mean']:10.3f}" for c in cols))


def tool_results(events, tool='search'):
    """(args, [chunk ids returned to the agent]) for each call of the tool, in order."""
    out, pending = [], None
    for e in events:
        if e['event'] == 'tool_request' and e['tool'] == tool:
            pending = e['args']
        elif e['event'] == 'tool_response' and e['tool'] == tool and pending is not None:
            out.append((pending, [c['chunk_id'] for c in (e.get('response') or {}).get('results', [])]))
            pending = None
    return out


def agent(a):
    """Evidence coverage of the agent's own searches over recorded Dev runs (what the agent actually saw)."""
    gold, facts = dev_facts()
    chunks = load_jsonl(CHUNKS)
    relevant = {fid: set(fact_relevant(f, chunks)) for fid, f in facts.items()}
    loose = {fid: set(fact_relevant(f, chunks, 'value')) for fid, f in facts.items()}
    report = {'runs': {}, 'note': 'Chunks returned to the agent: search top 5 plus attached parent tables, and chunks from '
                                  'read_evidence_context. Unanswerable questions (no facts) are excluded.'}
    for run in a.runs:
        arm = json.loads((run / 'manifest.json').read_text())['arms'][0]
        rows = []
        for g in gold:
            case = run / arm / g['id']
            if not g['facts'] or not (case / 'trajectory.jsonl').exists():
                continue
            events = load_jsonl(case / 'trajectory.jsonl')
            searches = tool_results(events)
            searched = {c for _, ids in searches for c in ids}
            seen = searched | {c for _, ids in tool_results(events, 'read_evidence_context') for c in ids}
            result = json.loads((case / 'result.json').read_text())
            row = {'id': g['id'], 'type': g['type'], 'searches': len(searches),
                   'correct': float(not result['error'] and correct(result['final'] or {}, g))}
            for name, pool, rel in (('search', searched, relevant), ('seen', seen, relevant), ('seen_value', seen, loose)):
                found = [bool(pool & rel[f]) for f in g['facts']]
                row[f'hop_recall_{name}'], row[f'all_hops_{name}'] = sum(found) / len(found), float(all(found))
            rows.append(row)
        covered = [r for r in rows if r['all_hops_seen_value']]
        missed = [r for r in rows if not r['all_hops_seen_value']]
        report['runs'][str(run)] = {
            'questions': len(rows), **aggregate(rows, ['correct'] + [f'{m}_{n}' for n in ('search', 'seen', 'seen_value')
                                                                   for m in ('hop_recall', 'all_hops')]),
            'searches_per_question': round(statistics.fmean(r['searches'] for r in rows), 2),
            'accuracy_when_all_hops_retrieved': round(statistics.fmean(r['correct'] for r in covered), 4) if covered else None,
            'accuracy_when_some_hop_missing': round(statistics.fmean(r['correct'] for r in missed), 4) if missed else None,
            'missing_hop_questions': [r['id'] for r in missed],
            'hop_recall_seen_value_by_type': {t: round(statistics.fmean(r['hop_recall_seen_value'] for r in rows if r['type'] == t), 4)
                        for t in sorted({r['type'] for r in rows})}}
    if a.cassettes:
        report['cassette_stages'] = {rule: cassette_stages(a.cassettes, gold, rel) for rule, rel in (('strict', relevant), ('value', loose))}
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    for run, r in report['runs'].items():
        print(Path(run).name.ljust(36), 'acc', r['correct']['mean'], '| all_hops search/seen/value',
              r['all_hops_search']['mean'], r['all_hops_seen']['mean'], r['all_hops_seen_value']['mean'],
              '| acc covered/missing', r['accuracy_when_all_hops_retrieved'], r['accuracy_when_some_hop_missing'])
    for rule, block in report.get('cassette_stages', {}).items():
        for s, m in block['stages'].items():
            print('cassettes', rule, s.ljust(12), {k: v['mean'] for k, v in m.items()})


def cassette_stages(folder, gold, relevant):
    """Per stage, the best rank any of the agent's own searches gave each gold fact (from recomputed traces).
    The cassettes are correct trajectories picked per question, so this is biased towards successful retrieval."""
    rows, used = {s: [] for s in ('dense', 'bm25', 'rrf', 'rrf+rerank')}, 0
    for g in gold:
        path = Path(folder) / g['id'] / 'retrieval_traces.jsonl'
        if not g['facts'] or not path.exists():
            continue
        used += 1
        traces = [t for t in load_jsonl(path) if t['args']['mode'] == 'hybrid']   # all four stages exist only for hybrid
        for s in rows:
            key = STAGES[s][1]
            ranks = []
            for f in g['facts']:
                found = [first_rank([c['chunk_id'] for c in t['trace'][key] or []], relevant[f]) for t in traces]
                found = [r for r in found if r]
                ranks.append(min(found) if found else None)
            rows[s].append(hop_metrics(ranks))
    metrics = [f'{m}@{k}' for m in ('hop_recall', 'all_hops') for k in (1, 5, 30)]
    return {'questions': used, 'searches': 'hybrid searches only',
            'caveat': 'cassettes hold one correct trajectory per question; biased upward',
            'stages': {s: aggregate(r, metrics) for s, r in rows.items() if r}}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('collect')
    c.add_argument('--set', choices=['dev30', 'facts'], required=True)
    c.add_argument('--url', default='http://127.0.0.1:8100')
    c.add_argument('--out', type=Path, required=True)
    s = sub.add_parser('score')
    s.add_argument('--runs', type=Path, required=True)
    s.add_argument('--out', type=Path, required=True)
    g = sub.add_parser('agent')
    g.add_argument('--runs', type=Path, nargs='+', required=True)
    g.add_argument('--cassettes', type=Path)
    g.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    {'collect': collect, 'score': score, 'agent': agent}[a.cmd](a)


if __name__ == '__main__':
    main()
