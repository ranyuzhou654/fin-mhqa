"""S6 baseline report on benchmark_v1 Dev: B0-B3, three runs each, paired comparisons; no model calls."""
import argparse
import json
from pathlib import Path
import statistics
from evaluate_benchmark import load_run, LEDGER
from finmhqa.bench.scoring import summarize, across_runs, paired_bootstrap

CONFIGS = {'B0': 'b0_closed_book', 'B1': 'b1_single_retrieval', 'B2': 'b2_flat', 'B3': 'b3_graph'}
PAIRS = [('B0', 'B1', '检索的作用'), ('B1', 'B2', 'Agent 循环（多步检索 + 上下文 + 计算器）的作用'),
         ('B2', 'B3', '多角色（规划 + 核验）的作用'), ('B1', 'B3', '完整 Agent 相对固定 RAG')]


def mean_of(summaries, path):
    values = []
    for s in summaries:
        v = s
        for key in path:
            v = v.get(key) if isinstance(v, dict) else None
        if v is not None:
            values.append(v)
    return round(statistics.mean(values), 4) if values else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs-dir', type=Path, default=Path('data/bench/runs'))
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    facts = {f['fact_id']: f for f in map(json.loads, LEDGER.read_text().splitlines())}
    runs, report = {}, {'split': 'dev', 'configs': {}, 'pairs': []}
    for name, cfg in CONFIGS.items():
        dirs = sorted(a.runs_dir.glob(f'dev_{cfg}_r[0-9]'))
        loaded = [load_run(d, facts) for d in dirs]
        if len({c for _, c, _ in loaded}) != 1:
            raise ValueError(f'{name}: runs use different configs')
        runs[name] = [s for _, _, s in loaded]
        summaries = [summarize(s) for s in runs[name]]
        types = sorted(summaries[0]['by_type'])
        tags = sorted(summaries[0]['by_tag'])
        report['configs'][name] = {
            'config': f'configs/bench_{cfg}.json', 'runs': [str(d) for d in dirs], **across_runs(runs[name]),
            'by_type_mean': {t: mean_of(summaries, ('by_type', t, 'value')) for t in types},
            'by_tag_mean': {t: mean_of(summaries, ('by_tag', t, 'value')) for t in tags},
            'abstention_recall_mean': mean_of(summaries, ('abstention_recall', 'value')),
            'false_abstention_mean': mean_of(summaries, ('false_abstention', 'value')),
            'errors_per_run': [s['errors'] for s in summaries],
            'search_errors_per_run': [s['search_errors'] for s in summaries],
            'busy_retries_per_run': [s['busy_retries'] for s in summaries],
            'citation_validity_mean': mean_of(summaries, ('citation_validity', 'value')),
            'hop_citation_coverage_mean': mean_of(summaries, ('hop_citation_coverage', 'value')),
            'model_calls_mean': mean_of(summaries, ('mean_model_calls',)),
            'tool_calls_mean': mean_of(summaries, ('mean_tool_calls',)),
            'seconds_mean': mean_of(summaries, ('mean_seconds',)),
            'p95_seconds_mean': mean_of(summaries, ('p95_seconds',)),
            'input_tokens_per_run': [s['tokens'].get('input_tokens', 0) for s in summaries],
            'output_tokens_per_run': [s['tokens'].get('output_tokens', 0) for s in summaries],
            'terminal_reasons_per_run': [s['terminal_reasons'] for s in summaries],
        }
    for base, cand, meaning in PAIRS:
        report['pairs'].append({'baseline': base, 'candidate': cand, 'question': meaning,
                                **paired_bootstrap(runs[base], runs[cand])})
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    for name, c in report['configs'].items():
        print(name, c['accuracy_per_run'], 'mean', c['accuracy_mean'], 'sd', c['accuracy_sd'], 'errors', c['errors_per_run'],
              'search_errors', c['search_errors_per_run'], 'calls', c['model_calls_mean'], 'tools', c['tool_calls_mean'])
    for pr in report['pairs']:
        print(pr['baseline'], '->', pr['candidate'], pr['difference'], pr['ci95'], f"+{pr['b_better_items']}/-{pr['a_better_items']}")


if __name__ == '__main__':
    main()
