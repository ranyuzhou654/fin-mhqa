"""Frozen benchmark access rules and program scoring."""
import hashlib
import json
import pytest
from finmhqa.bench.guard import load_questions
from finmhqa.bench.scoring import score_item, summarize, across_runs, paired_bootstrap


def frozen(tmp_path, registered=()):
    root = tmp_path / 'frozen'
    root.mkdir(parents=True)
    files = {}
    for split in ('dev', 'test'):
        f = root / f'{split}_questions.jsonl'
        f.write_text(json.dumps({'id': f'b1-{split}', 'question': 'q'}) + '\n')
        files[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    (root / 'manifest.json').write_text(json.dumps({'name': 'benchmark_v1', 'files': files}))
    config = tmp_path / 'config.json'
    config.write_text('{"name": "candidate"}')
    registry = tmp_path / 'registry.json'
    shas = [hashlib.sha256(config.read_bytes()).hexdigest()] if registered else []
    registry.write_text(json.dumps({'registered': [{'sha256': s} for s in shas]}))
    return root, config, registry


def test_dev_is_open_and_test_needs_final_and_registered_config(tmp_path):
    root, config, registry = frozen(tmp_path)
    rows, meta = load_questions('dev', frozen=root, registry=registry)
    assert rows == [{'id': 'b1-dev', 'question': 'q'}] and meta['split'] == 'dev'
    with pytest.raises(PermissionError, match='--final'):
        load_questions('test', config=config, frozen=root, registry=registry)
    with pytest.raises(PermissionError, match='not registered'):
        load_questions('test', final=True, config=config, frozen=root, registry=registry)
    with pytest.raises(ValueError, match='only applies'):
        load_questions('dev', final=True, frozen=root, registry=registry)
    root, config, registry = frozen(tmp_path / 'b', registered=True)
    assert load_questions('test', final=True, config=config, frozen=root, registry=registry)[0][0]['id'] == 'b1-test'
    config.write_text('{"name": "edited after registration"}')
    with pytest.raises(PermissionError, match='not registered'):
        load_questions('test', final=True, config=config, frozen=root, registry=registry)


def test_tampered_question_file_refused(tmp_path):
    root, _, registry = frozen(tmp_path)
    (root / 'dev_questions.jsonl').write_text('{"id": "b1-dev", "question": "changed"}\n')
    with pytest.raises(ValueError, match='frozen manifest'):
        load_questions('dev', frozen=root, registry=registry)


FACT = {'f1': {'chunk_id': 'c1', 'raw_value': '1,234.56', 'source_sha256': 's'}}
EVENTS = [{'event': 'tool_response', 'tool': 'search', 'response': {'results': [
    {'chunk_id': 'c1', 'text': '营业收入 1,234.56', 'source_sha256': 's'}, {'chunk_id': 'c2', 'text': '其他', 'source_sha256': 's'}]}}]


def gold(status='answered', value='2.60', unit='%', selected=None, qtype='T4', facts=('f1',)):
    return {'id': 'b1-x', 'type': qtype, 'tags': ['hard_location'], 'facts': list(facts),
            'answer': {'status': status, 'value': value, 'unit': unit, 'selected_company': selected}}


def result(status='answered', value='2.6', unit='%', selected=None, citations=('c1',), error=None):
    return {'final': {'status': status, 'answer_value': value, 'answer_unit': unit, 'selected_company': selected,
                      'citations': list(citations)}, 'error': error, 'decision_calls': 5, 'tool_calls': 3, 'seconds': 10.0,
            'usage': [{'input_tokens': 100, 'output_tokens': 5}]}


def test_numeric_unit_selection_and_abstention_rules():
    assert score_item(gold(), result(), EVENTS, FACT)['correct']                       # 2.6 == 2.60
    assert score_item(gold(), result(unit='％'), EVENTS, FACT)['correct']               # full-width percent
    assert not score_item(gold(), result(value='2.61'), EVENTS, FACT)['correct']
    assert not score_item(gold(), result(unit='亿元'), EVENTS, FACT)['correct']
    assert not score_item(gold(selected='甲'), result(selected='乙'), EVENTS, FACT)['correct']
    assert not score_item(gold(), result(error='RuntimeError'), EVENTS, FACT)['correct']
    assert not score_item(gold(), None, [], FACT)['correct']                            # not run counts as wrong
    refusal = gold(status='insufficient', value='', unit='', qtype='T6', facts=())
    assert score_item(refusal, result(status='insufficient', value='', unit='', citations=()), EVENTS, FACT)['correct']
    assert not score_item(refusal, result(), EVENTS, FACT)['correct']


def test_citation_validity_and_hop_coverage():
    s = score_item(gold(), result(citations=('c1',)), EVENTS, FACT)
    assert s['citations_valid'] and s['hops_cited'] == [True]
    s = score_item(gold(), result(citations=('c2', 'ghost')), EVENTS, FACT)
    assert s['citations_valid'] is False and s['hops_cited'] == [False]


def test_summary_keeps_errors_in_denominators_and_bootstrap_is_paired():
    answerable = [dict(score_item(gold(), result(), EVENTS, FACT), id=f'a{i}') for i in range(3)]
    failed = dict(score_item(gold(), result(error='boom'), EVENTS, FACT), id='a3')
    abstained = dict(score_item(gold(), result(status='insufficient', value='', unit='', citations=()), EVENTS, FACT), id='a4')
    summary = summarize(answerable + [failed, abstained])
    assert summary['accuracy'] == {'n': 5, 'value': 0.6} and summary['errors'] == 1
    assert summary['false_abstention']['value'] == 0.2
    runs_a = [answerable + [failed, abstained]]
    runs_b = [[dict(s, correct=True) for s in runs_a[0]]]
    diff = paired_bootstrap(runs_a, runs_b, resamples=500)
    assert diff['difference'] == 0.4 and diff['b_better_items'] == 2 and diff['ci95'][0] >= 0
    assert across_runs(runs_a + runs_b)['items_always_correct'] == 3
    with pytest.raises(ValueError, match='same items'):
        paired_bootstrap(runs_a, [runs_a[0][:2]])
