"""Render readable views from real journals; never generate missing trajectory steps."""
import argparse
import json
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    lines=['# 内置教师真实工具轨迹审核入口','','以下由实际运行日志转换，不补写缺失步骤。包含结构化动作、真实检索返回概览、计算结果与最终回答；完整证据正文保存在对应JSONL。','']
    for folder in sorted(a.run.glob('bridge-pilot-*')):
        p=folder/'trajectory.jsonl'
        if not p.exists():continue
        rows=list(map(json.loads,p.read_text().splitlines()))
        lines+=['',f'## {folder.name}','']
        for row in rows:
            if row['event']=='question':lines += [row['question'],'']
            elif row['event']=='teacher_decision':lines += [f"**步骤 {row['step']+1}：{row['action']['action']}** — {row['action']['reason']}",'']
            elif row['event']=='tool_request':lines += ['```json',json.dumps({'tool':row['tool'],'args':row['args']},ensure_ascii=False,indent=2),'```','']
            elif row['event']=='tool_response':
                if row['tool']=='search':
                    lines += ['返回的主证据：','']
                    for c in row['response']['results']:
                        if not c['attached']:lines.append(f"- {c['company']}，物理p{c['page_start']}–{c['page_end']}，`{c['chunk_id']}`；{c['chunk_type']}。")
                    lines += ['']
                else:lines += ['计算工具返回：`'+json.dumps(row['response'],ensure_ascii=False)+'`','']
            elif row['event']=='final':lines += ['最终回答：','',row['response']['explanation'],'',f"数值：{row['response']['answer_value']} {row['response']['answer_unit']}；状态：{row['response']['status']}。",'']
            elif row['event']=='error':lines += ['运行失败：'+row['message'],'']
        lines += [f"[完整轨迹](evidence/bridge_pilot_v1/trajectories/{folder.name}.jsonl)",'']
    a.out.write_text('\n'.join(lines))

if __name__=='__main__':main()
