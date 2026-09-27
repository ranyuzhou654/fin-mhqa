"""Ranking metrics for retrieval evaluation (binary relevance).

A query's gold is a set of relevant chunk ids. In this corpus a fact often appears in several chunks (the statement
table, its key-value split, the summary table), and any one of them is enough to answer. So:

- Hit@K (success@K): at least one relevant chunk in the top K. The primary metric, matching the existing Hit@5.
- Recall@K: |relevant ∩ top K| / |relevant|. With equivalent copies it measures how many copies surface, not
  whether the fact is answerable; report it but read Hit@K for answerability.
- MRR@K: 1 / rank of the first relevant chunk (0 if none in the top K).
- nDCG@K: DCG over the top K divided by the ideal DCG with min(|relevant|, K) relevant chunks on top.

For a multi-hop question with one gold set per hop, hop_recall@K is the fraction of hops with a hit in the top K
and all_hops@K is whether every hop has one.
"""
import math
import random
import statistics

KS = (1, 3, 5, 10, 20, 30)


def first_rank(ranking, relevant):
    return next((r for r, c in enumerate(ranking, 1) if c in relevant), None)


def hit_at(ranking, relevant, k):
    return float(any(c in relevant for c in ranking[:k]))


def recall_at(ranking, relevant, k):
    return len(set(ranking[:k]) & set(relevant)) / len(relevant) if relevant else 0.0


def mrr_at(ranking, relevant, k):
    r = first_rank(ranking[:k], relevant)
    return 1 / r if r else 0.0


def ndcg_at(ranking, relevant, k):
    dcg = sum(1 / math.log2(r + 1) for r, c in enumerate(ranking[:k], 1) if c in relevant)
    ideal = sum(1 / math.log2(r + 1) for r in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def query_metrics(ranking, relevant, ks=KS):
    """All metrics for one ranked list of chunk ids against one gold set."""
    if len(ranking) != len(set(ranking)):
        raise ValueError('ranking has duplicate ids')
    relevant = set(relevant)
    out = {'first_rank': first_rank(ranking, relevant), 'depth': len(ranking), 'n_relevant': len(relevant)}
    for k in ks:
        out.update({f'hit@{k}': hit_at(ranking, relevant, k), f'recall@{k}': recall_at(ranking, relevant, k),
                    f'mrr@{k}': mrr_at(ranking, relevant, k), f'ndcg@{k}': ndcg_at(ranking, relevant, k)})
    return out


def hop_metrics(first_ranks, ks=KS):
    """first_ranks: best rank of each hop's evidence (None if not found). Question-level coverage at each K."""
    out = {}
    for k in ks:
        covered = [r is not None and r <= k for r in first_ranks]
        out[f'hop_recall@{k}'] = sum(covered) / len(covered)
        out[f'all_hops@{k}'] = float(all(covered))
    return out


def mean_ci(values, resamples=10000, seed=20260927):
    """Mean with a 95% percentile bootstrap interval over items."""
    rng = random.Random(seed)
    boot = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(resamples))
    return {'mean': round(statistics.fmean(values), 4),
            'ci95': [round(boot[int(0.025 * resamples)], 4), round(boot[int(0.975 * resamples) - 1], 4)]}


def paired_diff(a, b, resamples=10000, seed=20260927):
    """Mean of b − a over the same items, with a 95% bootstrap interval."""
    if len(a) != len(b):
        raise ValueError('paired comparison needs the same items')
    res = mean_ci([y - x for x, y in zip(a, b)], resamples, seed)
    return {'difference': res['mean'], 'ci95': res['ci95'],
            'b_better': sum(y > x for x, y in zip(a, b)), 'a_better': sum(y < x for x, y in zip(a, b))}


def aggregate(rows, metrics, ci=True):
    """rows: per-query metric dicts. Returns {metric: {mean, ci95}} (or plain means)."""
    return {m: (mean_ci([r[m] for r in rows]) if ci else round(statistics.fmean(r[m] for r in rows), 4)) for m in metrics}
