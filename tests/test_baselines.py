"""B0/B1 baselines share the event format and never gain tools they are not meant to have."""
from finmhqa.agent.baselines import ClosedBook, SingleRetrieval

FINAL = {'status': 'answered', 'answer_value': '1.00', 'answer_unit': '亿元', 'selected_company': None,
         'citations': ['made-up'], 'explanation': 'x'}


def model_returning(final, seen):
    def model(question, payload, step, prompt, schema):
        seen.append((payload, prompt, schema))
        return dict(final), {'input_tokens': 1}
    return model


def test_closed_book_has_no_tools_and_logs_dropped_citations():
    seen, events = [], []
    state = ClosedBook(model_returning(FINAL, seen), events.append).run('贵州茅台2025年上半年营业收入？')
    assert state['final']['citations'] == [] and state['tool_calls'] == 0 and state['model_calls'] == 1
    assert any(e['event'] == 'closed_book_citations_dropped' and e['citations'] == ['made-up'] for e in events)
    assert seen[0][0] == [] and set(seen[0][2]['properties']) >= {'status', 'answer_value', 'citations'}


def test_single_retrieval_searches_each_named_company_once_then_answers():
    seen, events, calls = [], [], []
    def search(args):
        calls.append(args)
        return {'results': [{'chunk_id': args['company'], 'company': args['company'], 'page_start': 1, 'text': 't'}]}
    final = dict(FINAL, citations=['贵州茅台'])
    state = SingleRetrieval(model_returning(final, seen), search, events.append).run('比较贵州茅台与五粮液营业收入')
    assert [c['company'] for c in calls] == ['五粮液', '贵州茅台'] and all(c['mode'] == 'hybrid' for c in calls)
    assert state['tool_calls'] == 2 and state['model_calls'] == 1 and state['final'] == final
    assert {e['chunk_id'] for e in seen[0][0]} == {'五粮液', '贵州茅台'}
    none = []
    SingleRetrieval(model_returning(FINAL, []), lambda a: none.append(a) or {'results': []}, lambda e: None).run('山西汾酒营业收入')
    assert none == [{'query': '山西汾酒营业收入', 'company': None, 'mode': 'hybrid'}]


def test_search_error_is_recorded_and_answer_still_requested():
    events = []
    def broken(args):
        raise TimeoutError('slow')
    state = SingleRetrieval(model_returning(dict(FINAL, status='insufficient', answer_value='', citations=[]), []),
                            broken, events.append).run('贵州茅台营业收入')
    assert state['final']['status'] == 'insufficient'
    assert any(e['event'] == 'tool_response' and e['response']['error'] == 'TimeoutError' for e in events)
