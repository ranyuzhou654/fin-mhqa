"""benchmark_v1 scoring: exact answers after rounding, abstention, citation validity, hop coverage and cost.

Errors and missing runs stay in every denominator. Nothing here calls a model.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import random
import statistics
from finmhqa.harness.runs import classify_error

UNIT_ALIASES = {'％': '%', '百分比': '%', '百分点': '%'}


def norm_unit(unit):
    unit = (unit or '').strip()
    return UNIT_ALIASES.get(unit, unit)


def same_number(a, b):
    try:
        return Decimal(str(a).replace(',', '')) == Decimal(str(b).replace(',', ''))
    except (InvalidOperation, ValueError):
        return False


def observed_chunks(events):
    chunks = {}
    for e in events:
        if e.get('event') == 'tool_response' and e.get('tool') in ('search', 'read_evidence_context'):
            for c in e['response'].get('results', []):
                chunks[c['chunk_id']] = c
    return chunks


def fact_cited(fact, cited):
    """A hop counts as cited when a cited chunk is the fact's own chunk or carries its raw value in the same report."""
    return any(c['chunk_id'] == fact['chunk_id'] or (fact['raw_value'] in c.get('text', '')
               and c.get('source_sha256') == fact['source_sha256']) for c in cited)


def score_item(gold, result, events, facts):
    final = (result or {}).get('final') or {}
    error = (result or {}).get('error') if result else 'NOT_RUN'
    answer = gold['answer']
    status = final.get('status')
    if error:
        correct = False
    elif answer['status'] == 'insufficient':
        correct = status == 'insufficient'
    else:
        correct = (status == 'answered' and same_number(final.get('answer_value'), answer['value'])
                   and norm_unit(final.get('answer_unit')) == norm_unit(answer['unit'])
                   and (answer['selected_company'] is None or final.get('selected_company') == answer['selected_company']))
    seen = observed_chunks(events)
    citations = final.get('citations') or []
    cited = [seen[c] for c in citations if c in seen]
    hops = [fact_cited(facts[f], cited) for f in gold['facts']] if status == 'answered' else []
    tool_errors = sum(1 for e in events if e.get('event') == 'tool_response' and 'error' in e.get('response', {})
                      and e.get('tool') == 'search')
    usage = {}
    for u in (result or {}).get('usage') or []:
        for k, v in (u or {}).items():
            if isinstance(v, int):
                usage[k] = usage.get(k, 0) + v
    return {'id': gold['id'], 'type': gold['type'], 'tags': gold['tags'], 'correct': correct, 'error': error,
            'error_class': 'not_run' if error == 'NOT_RUN' else classify_error(error),
            'status': status, 'expected_status': answer['status'], 'terminal_reason': (result or {}).get('terminal_reason', 'NOT_RUN'),
            'citations_valid': set(citations) <= set(seen) if status == 'answered' else None,
            'hops_cited': hops, 'model_calls': (result or {}).get('decision_calls', 0), 'tool_calls': (result or {}).get('tool_calls', 0),
            'seconds': (result or {}).get('seconds'), 'usage': usage, 'search_errors': tool_errors,
            'busy_retries': (result or {}).get('retrieval_busy_retries', 0),
            'format_repairs': (result or {}).get('format_repairs', 0)}


def rate(items, key=lambda s: s['correct']):
    items = list(items)
    return {'n': len(items), 'value': round(sum(map(key, items)) / len(items), 4) if items else None}


def p95(values):
    values = sorted(v for v in values if v is not None)
    return values[max(0, int(round(0.95 * len(values))) - 1)] if values else None


def summarize(scores):
    answerable = [s for s in scores if s['expected_status'] == 'answered']
    unanswerable = [s for s in scores if s['expected_status'] == 'insufficient']
    answered = [s for s in scores if s['status'] == 'answered']
    hops = [h for s in answered for h in s['hops_cited']]
    tags = sorted({t for s in scores for t in s['tags']})
    tokens = {}
    for s in scores:
        for k, v in s['usage'].items():
            tokens[k] = tokens.get(k, 0) + v
    return {
        'accuracy': rate(scores),
        'by_type': {t: rate(s for s in scores if s['type'] == t) for t in sorted({s['type'] for s in scores})},
        'by_tag': {t: rate(s for s in scores if t in s['tags']) for t in tags},
        'abstention_recall': rate(unanswerable),
        # a refusal reached by exhausting budgets is scored like a deliberate one; report them apart
        'terminal_reasons': {'answerable': dict(Counter(s['terminal_reason'] for s in answerable)),
                             'unanswerable': dict(Counter(s['terminal_reason'] for s in unanswerable))},
        'false_abstention': rate(answerable, key=lambda s: s['status'] == 'insufficient' and not s['error']),
        'errors': sum(bool(s['error']) for s in scores),
        # infrastructure failures (quota, transport, not run) are separated from agent failures; all stay wrong
        'errors_by_class': dict(Counter(s.get('error_class') for s in scores if s['error'])),
        # infrastructure health: failed searches reach the agent as tool errors and depress scores
        'search_errors': sum(s.get('search_errors', 0) for s in scores),
        'busy_retries': sum(s.get('busy_retries', 0) for s in scores),
        'format_repairs': sum(s.get('format_repairs', 0) for s in scores),
        'citation_validity': rate(answered, key=lambda s: bool(s['citations_valid'])),
        'hop_citation_coverage': {'n': len(hops), 'value': round(sum(hops) / len(hops), 4) if hops else None},
        'mean_model_calls': round(statistics.mean(s['model_calls'] for s in scores), 3),
        'mean_tool_calls': round(statistics.mean(s['tool_calls'] for s in scores), 3),
        'mean_seconds': round(statistics.mean(s['seconds'] for s in scores if s['seconds'] is not None), 2)
                        if any(s['seconds'] is not None for s in scores) else None,
        'p95_seconds': p95(s['seconds'] for s in scores),
        'tokens': tokens,
    }


def across_runs(run_scores):
    """Mean and spread of accuracy over repeated runs, plus per-item correct counts."""
    accs = [summarize(s)['accuracy']['value'] for s in run_scores]
    per_item = {}
    for scores in run_scores:
        for s in scores:
            per_item.setdefault(s['id'], []).append(s['correct'])
    return {'runs': len(run_scores), 'accuracy_per_run': accs, 'accuracy_mean': round(statistics.mean(accs), 4),
            'accuracy_sd': round(statistics.stdev(accs), 4) if len(accs) > 1 else None,
            'items_always_correct': sum(all(v) for v in per_item.values()),
            'items_never_correct': sum(not any(v) for v in per_item.values()), 'items': len(per_item)}


def paired_bootstrap(runs_a, runs_b, resamples=10000, seed=20260923):
    """Accuracy difference B − A with a 95% bootstrap interval over items; each item uses its mean over runs."""
    def item_means(runs):
        out = {}
        for scores in runs:
            for s in scores:
                out.setdefault(s['id'], []).append(float(s['correct']))
        return {k: sum(v) / len(v) for k, v in out.items()}
    a, b = item_means(runs_a), item_means(runs_b)
    if set(a) != set(b):
        raise ValueError('paired comparison needs the same items')
    ids = sorted(a)
    diffs = [b[i] - a[i] for i in ids]
    rng = random.Random(seed)
    boot = sorted(statistics.mean(rng.choice(diffs) for _ in ids) for _ in range(resamples))
    return {'items': len(ids), 'difference': round(statistics.mean(diffs), 4),
            'ci95': [round(boot[int(0.025 * resamples)], 4), round(boot[int(0.975 * resamples) - 1], 4)],
            'b_better_items': sum(d > 0 for d in diffs), 'a_better_items': sum(d < 0 for d in diffs)}
