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


def test_reserved_last_call_is_for_verification():
    state,events,m=run([query(),calc(),finish(),PASS],max_models=4,synthesis='verified',reserve_verifier=True)
    assert state['final']==F and state['model_calls']==4
    assert m.inputs[2][-1]['budget']['finalize_only'] is True
    assert any(e['event']=='budget_reservation' for e in events)


def test_reserved_budget_rejects_extra_search_instead_of_unverified_publication():
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        run([query(),calc(),query('extra')],max_models=4,synthesis='verified',reserve_verifier=True)


def test_rejected_last_verification_does_not_publish_or_exceed_budget():
    state,events,_=run([query(),calc(),finish(),reject('CONFLICT')],max_models=4,synthesis='verified',reserve_verifier=True)
    assert state['final']['status']=='insufficient' and state['model_calls']==4


def context_action(anchor='c1'):
    return dict(action('read_evidence_context'),context={'anchor_id':anchor,'scope':'document_units','cursor':0})


def test_context_result_is_citable_and_counted_in_shared_budget():
    anchor=dict(C,source_sha256='hash')
    extra=dict(anchor,chunk_id='units',text='单位：元')
    f=dict(F,citations=['c1','units'])
    steps=[dict(a,context=a.get('context')) for a in [query(),context_action(),calc(),finish(f)]]+[PASS]
    state,events,_=run(steps,lambda _: {'results':[anchor]},context_reader=lambda _: {'results':[extra]},synthesis='verified',reserve_verifier=True)
    assert state['final']==f and state['tool_calls']==3 and state['search_calls']==1


@pytest.mark.parametrize('unseen',[True,False])
def test_context_requires_observed_anchor_and_matching_source(unseen):
    anchor=dict(C,source_sha256='hash'); calls=[]
    def read(args):
        calls.append(args)
        return {'results':[dict(anchor,chunk_id='bad',source_sha256='other')]}
    steps=[dict(query(),context=None),context_action('unseen' if unseen else 'c1')]
    state,events,_=run(steps,lambda _: {'results':[anchor]},context_reader=read,synthesis='verified',max_replans=0)
    assert state['final']['status']=='insufficient' and 'bad' not in state['evidence']
    assert len(calls)==(0 if unseen else 1)


def test_frozen_budget_leaves_one_revision_cycle_after_full_tool_use():
    # teacher_v1: 8 tool steps + finish + rejected verify still leaves finish + verify (M3.1 case 007).
    tools=[query(f'q{i}') for i in range(6)]+[calc(),calc()]
    state,events,m=run([*tools,finish(),reject('SCOPE_MISMATCH'),finish(),PASS],
                       max_models=12,synthesis='verified',reserve_verifier=True)
    assert state['final']==F and state['model_calls']==12 and state['tool_calls']==8
    assert state['replan_count']==1


def test_prompt_states_actual_model_budget():
    prompts=[];m=Scripted([query(),calc(),finish(),PASS])
    def model(q,payload,step,prompt,schema):
        prompts.append(prompt);return m(q,payload,step,prompt,schema)
    AgentGraph(model,lambda _: {'results':[C]},lambda e:None,max_models=12,synthesis='verified').run('甲收入多少')
    assert '合计最多12次' in prompts[0] and '合计最多10次' not in prompts[0]


def test_old_ten_call_budget_cannot_revise_after_full_tool_use():
    tools=[query(f'q{i}') for i in range(6)]+[calc(),calc()]
    state,_,_=run([*tools,finish(),reject('SCOPE_MISMATCH')],max_models=10,synthesis='verified',reserve_verifier=True)
    assert state['final']['status']=='insufficient' and state['terminal_reason']=='MODEL_BUDGET'


def test_last_tool_call_is_reserved_for_final_calculation():
    from jsonschema import ValidationError
    searches=[query(f'q{i}') for i in range(6)]+[calc()]
    state,events,_=run([*searches,calc('100'),finish(),PASS],max_models=12,synthesis='verified',
                       reserve_verifier=True,reserve_calculation=True)
    assert state['final']==F and state['tool_calls']==8
    assert any(e.get('calculation_only') for e in events)
    with pytest.raises(ValidationError):
        run([query('a'),query('b'),query('c')],max_tools=3,max_searches=6,synthesis='verified',reserve_calculation=True)


def test_flat_arm_can_read_context_and_reserve_calculation():
    # benchmark B2: flat differs from the graph only in roles, not in tools or budget rules
    anchor=dict(C,source_sha256='hash');extra=dict(anchor,chunk_id='units',text='单位：元')
    f=dict(F,citations=['c1','units'])
    flat=lambda a:{k:v for k,v in a.items() if k!='plan'}
    steps=[flat(dict(a,context=a.get('context'))) for a in [query(),context_action(),calc(),finish(f)]]
    prompts=[];m=Scripted(steps)
    def model(q,payload,step,prompt,schema):
        prompts.append((prompt,schema));return m(q,payload,step,prompt,schema)
    state=AgentGraph(model,lambda _: {'results':[anchor]},lambda e:None,'flat',synthesis='verified',
                     context_reader=lambda _: {'results':[extra]},reserve_calculation=True,calculator='v2').run('q')
    assert state['final']==f and 'read_evidence_context' in prompts[0][0] and 'plan' not in prompts[0][1]['properties']


def test_verifier_v2_sees_budget_and_previous_rejections_and_v1_payload_is_unchanged():
    from finmhqa.agent.graph_contracts import VERIFIER, VERIFIER_V2
    steps=[query(),calc(),finish(),reject('CONFLICT'),finish(),PASS]
    state,_,m=run(deepcopy(steps),synthesis='verified',verifier='v2')
    assert state['terminal_reason']=='ANSWERED'
    first,second=m.inputs[3],m.inputs[5]
    assert first['previous_rejections']==[] and first['budget']['remaining_tools']==6 and first['budget']['remaining_replans']==2
    assert second['previous_rejections'][0]['issues'][0]['code']=='CONFLICT' and second['budget']['remaining_replans']==1
    _,_,m1=run(deepcopy(steps),synthesis='verified')
    assert set(m1.inputs[3])=={'plan','candidate','history'} and VERIFIER_V2.startswith(VERIFIER)
    with pytest.raises(ValueError,match='verifier version'):
        AgentGraph(m1,None,print,verifier='v3')


def test_verifier_v2_schema_allows_cross_check_code():
    state,_,m=run([query(),calc(),finish(),reject('NEEDS_CROSS_CHECK'),query('主要会计数据','bm25'),finish(),PASS],synthesis='verified',verifier='v2')
    assert state['terminal_reason']=='ANSWERED' and m.inputs[4][-1]['feedback']['issues'][0]['code']=='NEEDS_CROSS_CHECK'


def long_plan(n=7):
    return [{'id':f'q{i}','question':'核对','depends_on':[],'status':'pending','conclusion':'','evidence_ids':[]} for i in range(n)]


def test_planner_v2_turns_invalid_dependencies_into_feedback_and_caps_size_by_schema():
    from jsonschema import ValidationError
    from finmhqa.agent.graph_contracts import PLANNER_V2_NOTE
    forward={**query(),'plan':[{**P[0],'depends_on':['q2']}]}
    state,events,m=run([forward,query(),calc(),finish(),PASS],synthesis='verified',planner='v2',verifier='v2')
    assert state['terminal_reason']=='ANSWERED' and state['replan_count']==1
    assert [e['problem'] for e in events if e['event']=='invalid_plan']==['invalid plan dependencies']
    assert sum(e['event']=='tool_request' for e in events)==2   # the invalid step's search was not executed
    assert m.inputs[1][-1]['feedback']['issues'][0]['code']=='INVALID_PLAN'
    with pytest.raises(ValueError,match='invalid plan dependencies'):
        run([forward],synthesis='verified')                    # v1 behaviour unchanged
    with pytest.raises(ValidationError,match='too long'):      # v2: the schema itself rejects >6 items (format repair fixes it)
        run([{**query(),'plan':long_plan()}],synthesis='verified',planner='v2')
    with pytest.raises(ValueError,match='planner v2 needs a graph arm'):
        AgentGraph(m,None,print,'flat',planner='v2')
    assert '最多6项' in PLANNER_V2_NOTE


def test_planner_v2_schema_is_repairable_by_validation():
    from jsonschema import validate, ValidationError
    from finmhqa.agent.graph_contracts import CONTEXT_PLAN_SCHEMA, planner_v2
    schema=planner_v2(CONTEXT_PLAN_SCHEMA)
    assert 'maxItems' not in CONTEXT_PLAN_SCHEMA['properties']['plan']
    with pytest.raises(ValidationError,match='too long'):
        validate({**query(),'context':None,'plan':long_plan()},schema)
