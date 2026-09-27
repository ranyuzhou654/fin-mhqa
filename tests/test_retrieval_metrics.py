import math
import pytest
from finmhqa.retrieval.metrics import (first_rank, hit_at, hop_metrics, mean_ci, mrr_at, ndcg_at, paired_diff,
                                       query_metrics, recall_at)


def test_hand_computed_values():
    ranking, relevant = ['a', 'x', 'b', 'y', 'z'], {'a', 'b', 'c'}
    assert first_rank(ranking, relevant) == 1
    assert hit_at(ranking, relevant, 1) == 1 and hit_at(['x'], relevant, 1) == 0
    assert recall_at(ranking, relevant, 3) == pytest.approx(2 / 3)
    assert mrr_at(['x', 'y', 'b'], relevant, 5) == pytest.approx(1 / 3)
    assert mrr_at(['x', 'y', 'b'], relevant, 2) == 0            # first hit beyond the cutoff
    dcg = 1 + 1 / math.log2(4)
    idcg = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at(ranking, relevant, 5) == pytest.approx(dcg / idcg)


def test_ndcg_ideal_uses_at_most_k_relevant():
    # one relevant copy found at rank 1 of a cutoff-1 list is perfect even if more copies exist
    assert ndcg_at(['a'], {'a', 'b', 'c'}, 1) == 1
    assert ndcg_at(['a', 'b'], {'a', 'b'}, 10) == 1


def test_edge_cases():
    assert query_metrics([], {'a'})['hit@5'] == 0
    assert recall_at(['a'], set(), 5) == 0 and ndcg_at(['a'], set(), 5) == 0
    with pytest.raises(ValueError):
        query_metrics(['a', 'a'], {'a'})


def test_metrics_are_monotone_in_k():
    m = query_metrics(['x', 'y', 'a', 'z', 'b'], {'a', 'b'}, ks=(1, 3, 5))
    for name in ('hit', 'recall', 'mrr'):
        assert m[f'{name}@1'] <= m[f'{name}@3'] <= m[f'{name}@5']


def test_rerank_does_not_change_recall_at_full_depth():
    before, after = ['a', 'x', 'b', 'y'], ['b', 'y', 'x', 'a']
    rel = {'a', 'b'}
    assert recall_at(before, rel, 4) == recall_at(after, rel, 4)
    assert mrr_at(after, rel, 4) == mrr_at(before, rel, 4) == 1


def test_hop_metrics():
    m = hop_metrics([1, 4, None], ks=(1, 5))
    assert m['hop_recall@1'] == pytest.approx(1 / 3) and m['hop_recall@5'] == pytest.approx(2 / 3)
    assert m['all_hops@5'] == 0 and hop_metrics([2, 3], ks=(5,))['all_hops@5'] == 1


def test_bootstrap_intervals():
    ci = mean_ci([0, 1] * 20)
    assert ci['mean'] == 0.5 and ci['ci95'][0] < 0.5 < ci['ci95'][1]
    assert mean_ci([1.0] * 5)['ci95'] == [1.0, 1.0]
    d = paired_diff([0, 0, 1, 1], [1, 1, 1, 0])
    assert d['difference'] == 0.25 and d['b_better'] == 2 and d['a_better'] == 1


def test_score_reads_every_stage_from_traces(tmp_path, monkeypatch):
    import json
    import sys
    from argparse import Namespace
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
    import retrieval_metrics as rm
    monkeypatch.setattr(rm, 'DEV30_RESULTS', tmp_path / 'absent.json')

    def ranked(ids):
        return [{'chunk_id': i} for i in ids]
    traces = []
    for qid in ('q1', 'q2'):
        for mode in ('dense', 'bm25', 'hybrid'):
            reranked = ['g', 'x', 'y'] if qid == 'q1' else ['x', 'y', 'z']
            trace = {'dense': ranked(['x', 'g', 'y']) if mode != 'bm25' else None,
                     'bm25': ranked(['y', 'x', 'g']) if mode != 'dense' else None,
                     'fused': ranked(['x', 'g', 'y']) if mode == 'hybrid' else None, 'reranked': ranked(reranked)}
            traces.append({'query_id': qid, 'mode': mode, 'primary': reranked, 'trace': trace})
    (tmp_path / 'traces.jsonl').write_text(''.join(json.dumps(t) + '\n' for t in traces))
    (tmp_path / 'queries.jsonl').write_text(''.join(json.dumps({'query_id': q, 'relevant': ['g']}) + '\n' for q in ('q1', 'q2')))
    (tmp_path / 'manifest.json').write_text(json.dumps({'set': 'dev30'}))
    rm.score(Namespace(runs=tmp_path, out=tmp_path / 'report.json'))
    report = json.loads((tmp_path / 'report.json').read_text())
    stages = report['labels']['stages']
    assert set(stages) == set(rm.STAGES) and report['checks']['rerank_top5_equals_results']
    assert stages['rrf+rerank']['hit@1']['mean'] == 0.5 and stages['rrf']['mrr@10']['mean'] == 0.5
    assert stages['bm25']['hit@1']['mean'] == 0 and report['labels']['not_found_in_30']['rrf+rerank'] == ['q2']
