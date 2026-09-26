"""Show one question end to end: planner -> tools -> verifier -> published answer.

  Replay a saved trajectory (no model calls, no retrieval server):
    PYTHONPATH=src .venv/bin/python scripts/demo.py --replay docs/examples/f2_column_swap_caught
  Ask a new question live (needs the local retrieval server and LLM_* in .env):
    PYTHONPATH=src .venv/bin/python scripts/demo.py --question "贵州茅台2025年上半年合并口径营业收入是多少？金额以亿元表示，四舍五入保留两位小数。"
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

WIDTH = 110
COLORS = {'title': '1', 'planner': '36', 'tool': '33', 'verifier': '35', 'ok': '32', 'bad': '31', 'dim': '2'}


class Printer:
    def __init__(self, color):
        self.color = color

    def __call__(self, text, style=None, indent=0):
        for i, line in enumerate(str(text).split('\n')):
            line = ' ' * indent + line
            line = line if len(line) <= WIDTH else line[:WIDTH - 1] + '…'
            print(f'\033[{COLORS[style]}m{line}\033[0m' if self.color and style else line, flush=True)


def short(text, n=90):
    text = ' '.join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + '…'


def chunk_label(c):
    title = c.get('table_title') or (c.get('section_path') or '').split('>')[-1].strip()
    return f"{c['chunk_id'][:8]}  p.{c.get('page_start')}  {c.get('chunk_type', '')}  {short(title, 40)}"


def render(event, out):
    kind = event['event']
    if kind == 'question':
        out(f"问题：{event['question']}", 'title')
        out(f"（{event.get('arm', '')} 架构）", 'dim')
    elif kind == 'teacher_decision':
        a, role = event['action'], event['role']
        if role == 'planner':
            out(f"\n[规划 · 第{event['step'] + 1}次模型调用] 动作 = {a['action']}", 'planner')
            out(f"理由：{short(a.get('reason', ''), 150)}", indent=2)
            for t in a.get('plan') or []:
                mark = {'resolved': '✓', 'pending': '·', 'blocked': '✗'}.get(t['status'], '?')
                out(f"{mark} {t['id']} {short(t['question'], 70)}", 'dim', 4)
        elif role == 'verifier':
            verdict = '通过' if a['sufficient'] and not a['issues'] else '拒绝'
            out(f"\n[核验 · 第{event['step'] + 1}次模型调用] {verdict}", 'ok' if verdict == '通过' else 'bad')
            for i in a.get('issues', []):
                out(f"{i['code']}：{short(i['detail'], 160)}", 'bad', 2)
                if i.get('suggestion'):
                    out(f"建议：{short(i['suggestion'], 150)}", 'dim', 4)
        else:
            out(f"\n[{role}] 模型调用", 'planner')
    elif kind == 'tool_request':
        args = event['args']
        if event['tool'] == 'search':
            out(f"→ 检索 [{args['company']}] {short(args['query'], 60)}（{args['mode']}）", 'tool', 2)
        elif event['tool'] == 'read_evidence_context':
            out(f"→ 读取上下文 锚点 {args['anchor_id'][:8]} 范围 {args['scope']} 游标 {args['cursor']}", 'tool', 2)
        else:
            values = '、'.join(f"{v['value']}{v['unit']}" for v in args['values'])
            out(f"→ 计算 {args['operation']}({values}) → {args['output_unit']}，保留 {args['round_digits']} 位", 'tool', 2)
    elif kind == 'tool_response':
        r = event['response']
        if 'error' in r:
            out(f"× 工具错误：{short(r.get('detail', r['error']), 100)}", 'bad', 4)
        elif event['tool'] in ('search', 'read_evidence_context'):
            results = r.get('results', [])
            out(f"返回 {len(results)} 个证据块", 'dim', 4)
            for c in results[:5]:
                out(chunk_label(c), 'dim', 6)
        else:
            out(f"= {r['value']} {r['unit']}", 'ok', 4)
    elif kind == 'invalid_plan':
        out(f"× 规划不合法（{event['problem']}），动作未执行，反馈给规划者", 'bad', 2)
    elif kind == 'budget_reservation':
        out('（预算保留：本步只能提交候选或做最终计算）', 'dim', 2)
    elif kind == 'error':
        out(f"× 运行错误：{event['message']}", 'bad')
    elif kind == 'final':
        f = event['response']
        out('\n' + '─' * 60)
        if f['status'] == 'answered':
            company = f" · 选中公司 {f['selected_company']}" if f.get('selected_company') else ''
            out(f"答案：{f['answer_value']} {f['answer_unit']}{company}", 'ok')
            out(f"引用：{', '.join(c[:8] for c in f['citations'])}", 'dim')
            text = '说明：' + ' '.join(f['explanation'].split())
            out('\n'.join(text[i:i + 52] for i in range(0, len(text), 52)))   # wrap, never cut the explanation
        else:
            out(f"拒答：{f['explanation']}", 'bad')
        out(f"结束原因：{event['terminal_reason']}", 'dim')


def summary(events, out):
    calls = sum(e['event'] == 'teacher_decision' for e in events)
    tools = sum(e['event'] == 'tool_request' for e in events)
    rejections = sum(e['event'] == 'verification' and not e['sufficient'] for e in events)
    out(f"模型调用 {calls} 次 · 工具调用 {tools} 次 · 核验拒绝 {rejections} 次", 'dim')


def replay(folder, out):
    folder = Path(folder)
    meta = folder / 'meta.json'
    if meta.exists():
        m = json.loads(meta.read_text())
        out(f"示例：{m['note']}", 'dim')
        if m.get('fault'):
            out(f"注入的故障（模型不知道）：{m['fault']}", 'bad')
        out('')
    events = [json.loads(l) for l in (folder / 'trajectory.jsonl').read_text().splitlines()]
    for e in events:
        render(e, out)
    summary(events, out)


def live(question, config_path, url, outdir, out):
    from finmhqa.agent.evidence_context import EvidenceContext
    from finmhqa.agent.graph import AgentGraph
    from finmhqa.agent.openai_compat import OpenAICompatTeacher
    from finmhqa.agent.pilot_tools import request_json, search
    config = json.loads(Path(config_path).read_text())
    spec = config['teacher']
    teacher = OpenAICompatTeacher(spec['base_url'], spec['model'], repair=bool(config.get('format_repair')))
    health = request_json(url + '/health')
    dataset = health['admission']['dataset_sha256']
    context = EvidenceContext(config['context_index'], dataset, config.get('context_version', 2)) if config.get('context_index') else None
    budgets = dict(config['budgets'], reserve_calculation=config.get('reserve_calculation', False), calculator=config.get('calculator', 'v1'),
                   **{k: config[k] for k in ('verifier', 'planner') if k in config})
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=False)
    events = []
    with (outdir / 'trajectory.jsonl').open('x') as f:
        def record(e):
            events.append(e)
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
            f.flush()
            render(e, out)
        out(f"配置：{config.get('name', config_path)}", 'dim')
        record({'event': 'question', 'id': 'demo', 'question': question, 'arm': config['arm']})
        model = lambda q, history, step, prompt, schema: teacher.decide(q, history, outdir / f'step_{step:02}', system=prompt, output_schema=schema)
        agent = AgentGraph(model, lambda args: search(url, args, dataset), record, config['arm'], synthesis=config['synthesis'],
                           context_reader=context.read if context else None, reserve_verifier=config['reserve_verifier'], **budgets)
        try:
            agent.run(question)
        except Exception as exc:
            record({'event': 'error', 'message': f'{type(exc).__name__}: {exc}'})
    summary(events, out)
    out(f"完整轨迹已保存：{outdir}", 'dim')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--replay', type=Path, help='folder with trajectory.jsonl (e.g. docs/examples/...)')
    g.add_argument('--question')
    p.add_argument('--config', type=Path, default=Path('configs/bench_b3_graph_repair_v2_planner_v2.json'))
    p.add_argument('--url', default='http://127.0.0.1:8100')
    p.add_argument('--out', type=Path, help='where a live run saves its trajectory (default: a new temp folder)')
    p.add_argument('--no-color', action='store_true')
    a = p.parse_args()
    out = Printer(color=not a.no_color and sys.stdout.isatty())
    if a.replay:
        replay(a.replay, out)
    else:
        live(a.question, a.config, a.url, a.out or Path(tempfile.mkdtemp(prefix='finmhqa_demo_')) / 'run', out)


if __name__ == '__main__':
    main()
