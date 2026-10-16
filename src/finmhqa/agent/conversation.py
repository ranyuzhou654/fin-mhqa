"""Multi-turn dialogue strategies around the frozen single-question agent (docs/benchmark/BENCHMARK_V2_DESIGN.md).

The agent graph itself is unchanged. A strategy only decides what the agent is given for the current turn:

  S1 independent   the current utterance alone (lower bound)
  S2 transcript    earlier user turns and the system's own published answers, then the current utterance
  S3 rewrite       one model call rewrites the current utterance into a standalone question; the agent sees only that
  S4 rewrite_carry S3, plus the evidence cited by earlier published answers, seeded as already-observed evidence

Earlier answers are the system's own outputs, never gold, so a wrong turn propagates as it would for a real user.
"""
from copy import deepcopy
from .context_policy import CHUNK_KEEP
from .graph import AgentGraph, initial

STRATEGIES = ('S1', 'S2', 'S3', 'S4')

REWRITER = '''你是对话改写器。输入是此前的对话记录（用户的问题与系统已发布的答案）和用户的最新一句。
把最新一句改写成一个完整、独立的问题，使不看对话记录的人也能准确回答：
- 写明公司名称、指标名称、口径（合并或母公司）、期间、金额单位与小数位要求；比率或增长率要写明公式；
- 「它」「这家」「较高的那家」「两者」等指代，按对话记录确定具体对象。需要比较选择的，可以依据系统已给出的答案写明具体公司，也可以把比较条件完整写进问题；
- 「同期」「上年同期」「前者减后者」等省略，补全为具体内容；用户纠正了前文时，以纠正后的要求为准；
- 只改写，不回答问题，不添加对话中没有的信息。
输出 JSON：{"standalone": 改写后的问题, "reason": 简要说明补全了什么}。'''
REWRITE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['standalone', 'reason'],
                  'properties': {'standalone': {'type': 'string', 'minLength': 4}, 'reason': {'type': 'string'}}}
CARRY_NOTE = '以下证据块是此前轮次中已核验并发布的答案所引用的原文，本轮可以直接使用和引用，也可以照常检索或读取上下文。'


def answer_text(final):
    """One line for the transcript: the system's published answer, or that it found the evidence insufficient."""
    if not final:
        return '（本轮出错，没有答案）'
    if final.get('status') != 'answered':
        return '证据不足，未给出答案'
    text = f"{final.get('answer_value')} {final.get('answer_unit', '')}".strip()
    if final.get('selected_company'):
        text += f"（选中的公司：{final['selected_company']}）"
    return text


def transcript(previous):
    """previous: [{'utterance', 'final'}] in order."""
    return '\n'.join(f"第{i}轮 用户：{p['utterance']}\n第{i}轮 系统答案：{answer_text(p['final'])}"
                     for i, p in enumerate(previous, 1))


def with_transcript(previous, utterance):
    return f"以下是此前的对话记录：\n{transcript(previous)}\n\n当前问题：{utterance}"


def rewrite_payload(previous, utterance):
    return {'对话记录': transcript(previous), '最新一句': utterance}


def carried_evidence(previous):
    """Chunks cited by earlier published answers (deduplicated, in order of first citation)."""
    out = {}
    for p in previous:
        final = p.get('final') or {}
        if final.get('status') != 'answered':
            continue
        for cid in final.get('citations', []):
            chunk = (p.get('evidence') or {}).get(cid)
            if chunk is not None and cid not in out:
                out[cid] = chunk
    return out


class CarryAgentGraph(AgentGraph):
    """The frozen graph, started with earlier turns' cited evidence already in its evidence pool (strategy S4).

    The chunks enter state['evidence'] with all fields (context reads and the publication guard need them) and the
    history with the compact fields of compact_v1, as one observation the planner and verifier can see."""

    def run(self, question, carried=None, note=CARRY_NOTE, source='carried_evidence'):
        """source names where the seeded chunks come from (earlier turns, or the document memory)."""
        state = initial(question)
        if carried:
            state['evidence'].update(deepcopy(carried))
            view = [{k: c[k] for k in CHUNK_KEEP if k in c} for c in carried.values()]
            state['history'].append({'action': {'action': source, 'reason': note},
                                     'observations': [{'tool': source, 'args': {'chunks': len(view)},
                                                       'response': {'results': view}}]})
            self.record({'event': source, 'chunk_ids': list(carried)})
        return self.graph.invoke(state, {'recursion_limit': 64})
