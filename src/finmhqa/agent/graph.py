"""Bounded four-role LangGraph; transport and event sink are injectable."""
from copy import deepcopy
import json
import re
import time
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from jsonschema import validate
from .codex_teacher import SYSTEM, SCHEMA
from .graph_contracts import PLAN_SCHEMA, VERIFY_SCHEMA, VERIFY_SCHEMA_V2, FINAL_SCHEMA, PLANNER, VERIFIER, VERIFIER_V2, SYNTHESIZER, CONTEXT_PLAN_SCHEMA, CONTEXT_PLANNER, CALCULATOR_V2_PROMPT, calculator_v2, CONTEXT_FLAT, CONTEXT_FLAT_SCHEMA, PLANNER_V2_NOTE, planner_v2
from .pilot_tools import calculate, decimal


class AgentState(TypedDict, total=False):
    question: str
    history: list
    plan: list
    evidence: dict
    tried: list
    calculations: list
    feedback: dict
    rejections: list
    action: dict
    candidate: dict
    final: dict
    replan_count: int
    model_calls: int
    search_calls: int
    tool_calls: int
    verified: bool
    halt: bool
    terminal_reason: str


def initial(question):
    return dict(question=question,history=[],plan=[],evidence={},tried=[],calculations=[],feedback={},
                action={},candidate={},final={},replan_count=0,model_calls=0,search_calls=0,
                tool_calls=0,verified=False,halt=False,terminal_reason='')


def issue(code,detail,suggestion='补充缺少的证据并改变查询。'):
    return {'code':code,'detail':detail,'suggestion':suggestion}


def final_checks(final,state):
    problems=[]
    if final['status']=='insufficient':return problems
    ids=final['citations']
    if not ids or not set(ids).issubset(state['evidence']):
        problems.append(issue('UNSEEN_CITATION','答案缺少引用或引用未返回的证据。'))
    try:
        valid=any(decimal(c['value'])==decimal(final['answer_value']) and c['unit']==final['answer_unit'] for c in state['calculations'])
    except (ValueError,ArithmeticError):valid=False
    if not valid:problems.append(issue('UNCALCULATED_ANSWER','最终数字及单位不对应成功计算结果。'))
    return problems


class AgentGraph:
    def __init__(self,model,search,record,arm='structured',max_models=10,max_tools=8,max_searches=6,max_replans=2,synthesis='model',context_reader=None,reserve_verifier=False,reserve_calculation=False,calculator='v1',verifier='v1',planner='v1'):
        if arm not in ('structured','generic','flat'):raise ValueError('invalid arm')
        self.model,self.search,self.record=model,search,record
        if synthesis not in ('model','verified'):raise ValueError('invalid synthesis mode')
        if reserve_verifier and (arm == 'flat' or synthesis != 'verified' or max_models < 2):
            raise ValueError('reserved verification requires structured/generic verified mode and >=2 calls')
        self.context_reader=context_reader
        self.reserve_verifier=reserve_verifier
        # Keep the last tool call for the final unit conversion/arithmetic (M3.1 case 006 ran out after finding the value).
        self.reserve_calculation=reserve_calculation
        # v1 keeps the frozen teacher_v1/v2 prompt and schema byte-identical; v2 adds ratio/growth.
        if calculator not in ('v1','v2'):raise ValueError('invalid calculator version')
        self.calculator=calculator
        # v1 keeps the frozen verifier contract; v2 adds source precedence, period checks and no repeated rejection.
        if verifier not in ('v1','v2'):raise ValueError('invalid verifier version')
        self.verifier_version=verifier
        # planner v2: capped plan schema (repairable), merge cross-checks, invalid plans become feedback instead of crashing.
        if planner not in ('v1','v2'):raise ValueError('invalid planner version')
        if planner=='v2' and arm=='flat':raise ValueError('planner v2 needs a graph arm')
        self.planner_version=planner
        self.synthesis=synthesis
        self.arm=arm;self.max_models=max_models;self.max_tools=max_tools
        self.max_searches=max_searches;self.max_replans=max_replans
        g=StateGraph(AgentState)
        for name in ('planner','executor','verifier','synthesizer'):g.add_node(name,getattr(self,name))
        g.add_edge(START,'planner')
        g.add_conditional_edges('planner',lambda s:'synthesizer' if s['halt'] else 'executor')
        g.add_edge('executor','verifier')
        g.add_conditional_edges('verifier',lambda s:'synthesizer' if s['halt'] or s['verified'] else 'planner')
        g.add_edge('synthesizer',END)
        self.graph=g.compile()

    def call(self,s,role,payload,prompt,schema):
        if s['model_calls']>=self.max_models:raise ValueError('model budget exhausted')
        step=s['model_calls'];s['model_calls']+=1;start=time.monotonic()
        action,usage=self.model(s['question'],payload,step,prompt,schema)
        self.record({'event':'teacher_decision','step':step,'role':role,'action':action,'usage':usage,'seconds':time.monotonic()-start})
        validate(action,schema)
        return action

    def planner(self,s):
        self.record({'event':'node','name':'planner','replan_count':s['replan_count']})
        if s['model_calls']>=self.max_models - int(self.reserve_verifier):
            s.update(halt=True,terminal_reason='MODEL_BUDGET');return s
        history=list(s['history'])
        if self.arm!='flat':
            history.append({'plan':s['plan'],'feedback':s['feedback'],
                'remaining_tools':self.max_tools-s['tool_calls'],'remaining_model_calls':self.max_models-s['model_calls']})
        flat=self.arm=='flat';context=self.context_reader is not None
        prompt = (CONTEXT_FLAT if context else SYSTEM) if flat else (CONTEXT_PLANNER if context else PLANNER)
        prompt = prompt.replace('合计最多10次', f'合计最多{self.max_models}次')
        schema = deepcopy((CONTEXT_FLAT_SCHEMA if context else SCHEMA) if flat else (CONTEXT_PLAN_SCHEMA if context else PLAN_SCHEMA))
        if self.calculator=='v2':
            schema=calculator_v2(schema);prompt+=CALCULATOR_V2_PROMPT
        if self.planner_version=='v2':
            schema=planner_v2(schema);prompt+=PLANNER_V2_NOTE
        finalize = self.reserve_verifier and self.max_models-s['model_calls'] <= 2
        if self.reserve_verifier:
            history.append({'budget': {'finalize_only': finalize, 'reserved_verifier_calls': 1,
                'remaining_model_calls': self.max_models-s['model_calls'],
                'remaining_tools': self.max_tools-s['tool_calls']}})
            if finalize:
                schema['properties']['action']['enum'] = ['finish']
                prompt += '\n本步仅能finish：预算只够提交候选并验证。证据不足则insufficient。'
                self.record({'event':'budget_reservation','finalize_only':True,'remaining_models':self.max_models-s['model_calls']})
        if self.reserve_calculation and not finalize and self.max_tools-s['tool_calls']<=1:
            allowed=['calculate','finish'] if self.max_tools>s['tool_calls'] else ['finish']
            schema['properties']['action']['enum']=allowed
            prompt+='\n工具预算只剩最后一次，已保留给最终计算：本步只能calculate或finish，不能再检索或读取上下文。'
            self.record({'event':'budget_reservation','calculation_only':True,'remaining_tools':self.max_tools-s['tool_calls']})
        action=self.call(s,'planner',history,prompt,schema)
        s['action']=action;s['feedback']={};s['verified']=False
        if self.arm!='flat':
            plan=action['plan'];ids=set();problem=None
            if not 1<=len(plan)<=6:problem='plan size'
            for task in plan:
                if problem:break
                if task['id'] in ids or not set(task['depends_on']).issubset(ids):problem='invalid plan dependencies'
                elif not set(task['evidence_ids']).issubset(s['evidence']):problem='unseen plan evidence'
                ids.add(task['id'])
            if problem and self.planner_version=='v2':
                # The action is not executed; the planner gets structured feedback and one replan is consumed.
                self.record({'event':'invalid_plan','problem':problem,'items':len(plan)})
                s['action']={'action':'invalid_plan','problem':problem};return s
            if problem:raise ValueError(problem)
            s['plan']=plan
        return s

    def executor(self,s):
        self.record({'event':'node','name':'executor'})
        action=s['action'];observations=[];problems=[]
        if action['action']=='invalid_plan':
            detail={'plan size':'plan超过6项或为空。','invalid plan dependencies':'依赖引用了后面或不存在的子问题，或子问题ID重复。',
                    'unseen plan evidence':'plan引用了未观测的证据ID。'}[action['problem']]
            s['feedback']={'issues':[issue('INVALID_PLAN',detail+'本步动作未执行。','合并已解决的子问题，把核对并入原子问题，保持最多6项且依赖只引用前面的子问题，然后重新选择动作。')]}
            return s
        def tool(name,args,fn):
            step=s['model_calls']-1
            s['tool_calls']+=1
            self.record({'event':'tool_request','step':step,'tool':name,'args':args})
            start=time.monotonic()
            try:response=fn(args)
            except (TimeoutError,OSError,ValueError,KeyError,ArithmeticError,TypeError) as exc:
                response={'error':type(exc).__name__,'detail':str(exc),'recoverable':True}
            self.record({'event':'tool_response','step':step,'tool':name,'response':response,'seconds':time.monotonic()-start})
            observations.append({'tool':name,'args':args,'response':response})
            if 'error' in response:problems.append(issue('TOOL_ERROR',response['detail'],'根据错误修正参数，改变查询或mode后重试。'))
            return response
        if action['action']=='search':
            requests=action['searches'] or []
            if not 1<=len(requests)<=2:raise ValueError('search arity')
            if s['search_calls']+len(requests)>self.max_searches or s['tool_calls']+len(requests)>self.max_tools:
                s.update(halt=True,terminal_reason='TOOL_BUDGET');return s
            for args in requests:
                key=json.dumps(args,sort_keys=True,ensure_ascii=False)
                if key in s['tried']:
                    problems.append(issue('DUPLICATE_QUERY','完全相同的company/query/mode已尝试。','改写限定词或切换mode。'));continue
                s['tried'].append(key);s['search_calls']+=1
                response=tool('search',args,self.search)
                if 'error' not in response:
                    chunks=response['results'];s['evidence'].update({c['chunk_id']:c for c in chunks})
                    if not chunks:problems.append(issue('EMPTY_RETRIEVAL','本次检索没有结果。','改变关键词或检索模式，保留已解决事实。'))
        elif action['action']=='read_evidence_context':
            if s['tool_calls']>=self.max_tools:s.update(halt=True,terminal_reason='TOOL_BUDGET');return s
            args=action['context']
            def read(args):
                if self.context_reader is None:raise ValueError('context tool unavailable')
                anchor=s['evidence'].get(args['anchor_id'])
                if anchor is None:raise ValueError('context anchor was not observed')
                key='context:'+json.dumps(args,sort_keys=True,ensure_ascii=False)
                if key in s['tried']:raise ValueError('duplicate context request')
                s['tried'].append(key)
                response=self.context_reader(args)
                for c in response['results']:
                    if any(c[k]!=anchor[k] for k in ('company','source_sha256')):
                        raise ValueError('context escaped anchor document')
                return response
            response=tool('read_evidence_context',args,read)
            if 'error' not in response:
                s['evidence'].update({c['chunk_id']:c for c in response['results']})
                if not response['results']:problems.append(issue('EMPTY_CONTEXT','该范围没有已准入的上下文。','改选范围或检索，不能推断缺失内容。'))
        elif action['action']=='calculate':
            if s['tool_calls']>=self.max_tools:s.update(halt=True,terminal_reason='TOOL_BUDGET');return s
            response=tool('calculate',action['calculation'],calculate)
            if 'error' not in response:s['calculations'].append(response)
        elif action['action']=='finish':
            s['candidate']=action['final']
            if not s['candidate']:raise ValueError('missing candidate')
            problems.extend(final_checks(s['candidate'],s))
        else:raise ValueError('unknown action')
        # Feedback is separate from raw tool history so the ablation cannot leak detailed verifier output.
        s['history'].append({'action':action,'observations':observations})
        s['feedback']={'issues':problems}
        return s

    def reject(self,s,feedback):
        self.record({'event':'verification','sufficient':False,'feedback':feedback})
        s['replan_count']+=1
        s['feedback']=feedback if self.arm=='structured' else {'sufficient':False,'message':'验证未通过，请检查已有证据并重新规划。'}
        self.record({'event':'planner_feedback','arm':self.arm,'feedback':s['feedback']})
        if s['replan_count']>self.max_replans:s.update(halt=True,terminal_reason='REPLAN_BUDGET')
        return s

    def verifier(self,s):
        self.record({'event':'node','name':'verifier'})
        if s['halt']:return s
        if s['feedback'].get('issues'):
            return self.reject(s,{'resolved':[p for p in s['plan'] if p['status']=='resolved'],
                                  'issues':s['feedback']['issues'],'tried':s['tried']})
        if s['action']['action']!='finish':return s
        if s['candidate']['status']=='insufficient':
            s.update(halt=True,terminal_reason='INSUFFICIENT');return s
        if self.arm=='flat':s['verified']=True;return s
        if s['model_calls']>=self.max_models:s.update(halt=True,terminal_reason='MODEL_BUDGET');return s
        payload={'plan':s['plan'],'candidate':s['candidate'],'history':s['history']}
        prompt,schema=VERIFIER,VERIFY_SCHEMA
        if self.verifier_version=='v2':
            payload.update(previous_rejections=s.get('rejections',[]),
                           budget={'remaining_tools':self.max_tools-s['tool_calls'],'remaining_searches':self.max_searches-s['search_calls'],
                                   'remaining_model_calls':self.max_models-s['model_calls']-1,'remaining_replans':self.max_replans-s['replan_count']})
            prompt,schema=VERIFIER_V2,VERIFY_SCHEMA_V2
        check=self.call(s,'verifier',payload,prompt,schema)
        if not check['sufficient'] or check['issues']:
            if self.verifier_version=='v2':
                s['rejections']=s.get('rejections',[])+[{'candidate':s['candidate'],'issues':check['issues']}]
            return self.reject(s,{**check,'tried':s['tried']})
        self.record({'event':'verification','sufficient':True,'feedback':check})
        s['verified']=True;return s

    def synthesizer(self,s):
        self.record({'event':'node','name':'synthesizer'})
        if s['verified']:
            final=s['candidate']
            if self.arm!='flat' and self.synthesis=='model':
                if s['model_calls']>=self.max_models:
                    s.update(halt=True,verified=False,terminal_reason='MODEL_BUDGET')
                else:
                    final=self.call(s,'synthesizer',{'verified_candidate':final,'evidence':{k:s['evidence'][k] for k in final['citations']}},SYNTHESIZER,FINAL_SCHEMA)
                    for k in ('status','answer_value','answer_unit','selected_company','citations'):
                        if final[k]!=s['candidate'][k]:raise ValueError('synthesizer changed verified field: '+k)
                    if final_checks(final,s):raise ValueError('final evidence/calculation guard')
            if s['verified']:
                if final_checks(final,s):raise ValueError('publication evidence/calculation guard')
                if self.arm!='flat' and self.synthesis=='verified':
                    self.record({'event':'verified_publication','model_called':False,'explanation_unchanged':True})
                s['final']=final;s['terminal_reason']='ANSWERED'
        if not s['verified']:
            s['final']={'status':'insufficient','answer_value':'','answer_unit':'','selected_company':None,'citations':[],
                        'explanation':'证据或运行预算不足：'+s['terminal_reason']}
        self.record({'event':'final','response':s['final'],'terminal_reason':s['terminal_reason']})
        return s

    def run(self,question):
        return self.graph.invoke(initial(question),{'recursion_limit':64})
