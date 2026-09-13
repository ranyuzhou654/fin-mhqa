"""Readable review generated solely from persisted runtime events."""
import argparse
import json
from pathlib import Path


def render(run,out):
    lines=['# M3 逐题真实运行与反馈审核','', '这些是可见决策及实际工具记录，不是内部思维链。所有组与所有失败都保留。','']
    for arm in ('structured','generic','flat'):
        lines += [f'## {arm}','']
        for case in sorted((run/arm).glob('agent-dev-*')):
            events=[json.loads(l) for l in (case/'trajectory.jsonl').read_text().splitlines()]
            result=json.loads((case/'result.json').read_text())
            lines += [f'### {case.name}','',events[0]['question'],'',f"终止：{result['terminal_reason']}；工具 {result['tool_calls']}；模型 {result['decision_calls']}；重规划 {result['replan_count']}。",'']
            for e in events:
                if e['event']=='teacher_decision':
                    a=e['action'];role=e['role']
                    if role=='planner':lines += [f"- Planner {e['step']}: {a['action']}。{a['reason']}"]
                    elif role=='verifier':lines += [f"- Verifier {e['step']}: sufficient={a['sufficient']}；问题：{json.dumps(a['issues'],ensure_ascii=False)}"]
                    else:lines += [f"- Synthesizer {e['step']}: {a['answer_value']}{a['answer_unit']}。{a['explanation']}"]
                elif e['event']=='tool_request':lines += [f"- 实际 {e['tool']} 参数：`{json.dumps(e['args'],ensure_ascii=False)}`"]
                elif e['event']=='tool_response':
                    r=e['response']
                    if 'error' in r:lines += [f"- 工具错误：{r['error']}，{r['detail']}"]
                    elif e['tool']=='calculate':lines += [f"- 计算结果：{r['value']}{r['unit']}"]
                    else:lines += ['- 返回证据：'+'；'.join(f"{c['chunk_id']}（{c['company']} p{c['page_start']}）" for c in r['results'])]
                elif e['event']=='planner_feedback':lines += ['- 实际发给Planner的反馈：`'+json.dumps(e['feedback'],ensure_ascii=False)+'`']
                elif e['event']=='error':lines += ['- 运行错误：'+e['message']]
            lines += ['',f"[完整轨迹](evidence/agent_dev20_v1/trajectories/{arm}/{case.name}.jsonl)",'']
    out.write_text('\n'.join(lines))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    render(a.run,a.out)
