"""Controlled failure injection tests; semantic verifier outputs are explicit fixtures."""
from copy import deepcopy
import pytest
from finmhqa.agent.graph import AgentGraph

C={'chunk_id':'c1','company':'甲','text':'2025H1合并营业收入100元','report_period':'2025H1'}
F={'status':'answered','answer_value':'100.00','answer_unit':'元','selected_company':None,'citations':['c1'],'explanation':'收入100元'}
P=[{'id':'q1','question':'收入','depends_on':[],'status':'pending','conclusion':'','evidence_ids':[]}]

def action(kind,**kw):
    return dict(action=kind,reason='fixture',searches=None,calculation=None,final=None,plan=deepcopy(P),**kw) if not kw else {**action(kind),**kw}

def query(q='营业收入',mode='hybrid'):
    return action('search',searches=[{'query':q,'company':'甲','mode':mode}])

def calc(value='100'):
    return action('calculate',calculation={'operation':'convert','values':[{'value':value,'unit':'元'}],'output_unit':'元','round_digits':2})

def finish(f=None):return action('finish',final=deepcopy(f or F))
PASS={'sufficient':True,'resolved':['q1'],'issues':[]}

def reject(code):return {'sufficient':False,'resolved':[], 'issues':[{'subquestion':'q1','code':code,'detail':'fixture detail','evidence_ids':['c1'],'suggestion':'查询表头单位及期间'}]}

class Scripted:
    def __init__(self,steps):self.steps=iter(steps);self.inputs=[]
    def __call__(self,q,payload,step,prompt,schema):
        self.inputs.append(deepcopy(payload));r=next(self.steps)
        return deepcopy(r),{'input_tokens':1,'output_tokens':1}

def run(steps,retriever=None,arm='structured',**budgets):
    m=Scripted(steps);events=[]
    g=AgentGraph(m,retriever or (lambda _: {'results':[C]}),events.append,arm,**budgets)
    return g.run('甲收入多少'),events,m

def test_real_graph_roles_and_answer():
    state,events,_=run([query(),calc(),finish(),PASS,F])
    assert state['final']==F
    assert {e['name'] for e in events if e['event']=='node'}=={'planner','executor','verifier','synthesizer'}

@pytest.mark.parametrize('code',['UNIT_MISSING','PERIOD_MISMATCH','CONFLICT'])
def test_structured_feedback_is_delivered_and_query_changes(code):
    state,events,m=run([query(),calc(),finish(),reject(code),query('报表单位 2025H1','bm25'),finish(),PASS,F])
    assert state['terminal_reason']=='ANSWERED' and state['replan_count']==1
    assert m.inputs[4][-1]['feedback']['issues'][0]['code']==code
    requests=[e['args'] for e in events if e['event']=='tool_request' and e['tool']=='search']
    assert requests[0]!=requests[1]


def test_generic_ablation_hides_verifier_details():
    state,events,m=run([query(),calc(),finish(),reject('UNIT_MISSING'),query('报表单位','bm25'),finish(),PASS,F],arm='generic')
    feedback=m.inputs[4][-1]['feedback']
    assert 'issues' not in feedback and 'tried' not in feedback
    assert 'UNIT_MISSING' not in str(m.inputs[4])
    assert state['final']==F


def test_transient_retrieval_error_recovered_and_retained():
    calls=[]
    def retrieval(args):
        calls.append(args)
        if len(calls)==1:raise TimeoutError('injected timeout')
        return {'results':[C]}
    state,events,m=run([query(),query('营业收入 本报告期','bm25'),calc(),finish(),PASS,F],retrieval)
    assert state['final']==F and state['replan_count']==1
    assert any(e.get('response',{}).get('error')=='TimeoutError' for e in events)
    assert m.inputs[1][-1]['feedback']['issues'][0]['code']=='TOOL_ERROR'


def test_empty_retrieval_and_duplicate_do_not_loop_forever():
    state,events,_=run([query(),query(),query()],lambda _: {'results':[]})
    assert state['terminal_reason']=='REPLAN_BUDGET'
    assert sum(e['event']=='tool_request' for e in events)==1


def test_unseen_citation_rejected_before_semantic_verifier():
    bad=deepcopy(F);bad['citations']=['invented']
    state,events,_=run([query(),calc(),finish(bad)],max_replans=0)
    assert state['final']['status']=='insufficient'
    assert any(e.get('feedback',{}).get('issues',[{}])[0].get('code')=='UNSEEN_CITATION' for e in events if e['event']=='verification')


def test_calculation_must_equal_final_answer():
    state,_,_=run([query(),calc('99'),finish()],max_replans=0)
    assert state['final']['status']=='insufficient'


def test_synthesizer_cannot_change_verified_value():
    bad=deepcopy(F);bad['answer_value']='101.00'
    with pytest.raises(ValueError,match='changed verified field'):run([query(),calc(),finish(),PASS,bad])


def test_tool_and_model_budgets():
    state,events,_=run([query(),calc()],max_tools=1)
    assert state['terminal_reason']=='TOOL_BUDGET'
    state,events,_=run([query()],max_models=1)
    assert state['terminal_reason']=='MODEL_BUDGET'


def test_dependency_cycle_rejected():
    a=query();a['plan'][0]['depends_on']=['q1']
    with pytest.raises(ValueError,match='dependencies'):run([a])


def test_verified_publication_at_exact_model_budget():
    state,events,_=run([query(),calc(),finish(),PASS],max_models=4,synthesis='verified')
    assert state['final']==F and state['model_calls']==4
    assert any(e['event']=='verified_publication' for e in events)
    assert not any(e.get('role')=='synthesizer' for e in events)


def test_verified_publication_preserves_reviewed_explanation():
    final=deepcopy(F);final['explanation']='这一解释已经核验，不得再让模型添加事实。'
    state,_,_=run([query(),calc(),finish(final),PASS],synthesis='verified')
    assert state['final']==final


def test_verified_publication_does_not_bypass_verifier():
    state,_,_=run([query(),calc(),finish(),reject('CONFLICT')],max_models=4,synthesis='verified')
    assert state['final']['status']=='insufficient'
