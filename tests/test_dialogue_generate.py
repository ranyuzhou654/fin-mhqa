import json
from pathlib import Path
import pytest
from finmhqa.bench.dialogue import GENERATORS, d1_bridge, d3_correction
from finmhqa.bench.generate import Ledger

LEDGER = Path('data/bench/ledger_v1/facts.jsonl')
pytestmark = pytest.mark.skipif(not LEDGER.exists(), reason='fact ledger is local data')


@pytest.fixture(scope='module')
def ledger():
    return Ledger([json.loads(l) for l in LEDGER.read_text().splitlines()])


def test_every_turn_has_gold_and_a_full_standalone_form(ledger):
    for gen in GENERATORS.values():
        for d in gen(ledger)[:200]:
            assert d['id'].startswith('b2-') and 2 <= len(d['turns']) <= 4
            for i, t in enumerate(d['turns'], 1):
                assert t['turn'] == i and t['facts'] and t['answer']['status'] == 'answered'
                assert t['standalone'].startswith('根据')            # written out in full
                assert all(r < i for r in t['refers_to'])           # references only point backwards
                if i > 1:
                    assert t['phenomena'] and t['refers_to']        # every follow-up needs the context
                    assert '根据' not in t['utterance']             # and does not restate it


def test_followups_omit_what_the_context_fixed(ledger):
    d = next(d for d in d1_bridge(ledger) if len(d['turns']) == 4)
    first, second, third = (t['utterance'] for t in d['turns'][:3])
    company_b = d['turns'][1]['standalone'].split('，')[1][:4]
    assert '营业收入' not in second or '营业收入' in first
    assert company_b[:2] in second                                  # the new company is named
    assert d['turns'][2]['answer']['selected_company'] in d['turns'][2]['standalone']
    assert d['turns'][2]['answer']['selected_company'] not in third  # the chosen company is only referred to


def test_correction_turn_switches_to_parent_scope(ledger):
    for d in d3_correction(ledger):
        t1, t2 = d['turns']
        assert '合并口径' in t1['utterance'] and '母公司' in t2['utterance']
        assert t1['answer']['value'] != t2['answer']['value'] and 'correction' in t2['phenomena']


def test_ids_are_stable(ledger):
    assert [d['id'] for d in d1_bridge(ledger)[:20]] == [d['id'] for d in d1_bridge(ledger)[:20]]
