"""benchmark_v1 S4: paraphrase (DeepSeek), ambiguity judges (Sol + DeepSeek), retrieval reachability, assembly.

Every model call is one attempt stored with raw input/output; a stage can be resumed and never overwrites a
finished item. Gold answers are never sent to any model.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
from finmhqa.agent.codex_teacher import CodexTeacher
from finmhqa.agent.openai_compat import OpenAICompatTeacher
from finmhqa.agent.pilot_tools import request_json, search
from finmhqa.bench.quality import (PARAPHRASE_PROMPT, PARAPHRASE_SCHEMA, JUDGE_PROMPT, JUDGE_SCHEMA,
                                   paraphrase_problems, reachability_query, reachable)

GEN = Path('data/bench/generated_v1r2')
# Flags on these unanswerable templates that only concern the period or how to compute it restate the intended
# premise (the half-year report does not cover the period); they are kept as a recorded review decision.
WAIVABLE = {'t6_q3': '题目询问半年报未覆盖的期间，Judge 指出的正是预期的无法回答前提',
            't6_fy2025': '题目询问半年报未覆盖的全年数据，Judge 指出的正是预期的无法回答前提（年化推算不是可接受的答案）',
            't6_2023': '题目询问半年报未列示的期间，Judge 指出的正是预期的无法回答前提'}
LEDGER = Path('data/bench/ledger_v1/facts.jsonl')
CODEX = '/Applications/ChatGPT.app/Contents/Resources/codex'


def items():
    return [json.loads(l) for part in ('dev', 'test') for l in (GEN / f'{part}_gold.jsonl').read_text().splitlines()]


def run_all(out, rows, fn, workers):
    out.mkdir(parents=True, exist_ok=True)
    def one(row):
        done = out / f"{row['id']}.json"
        if done.exists():
            return json.loads(done.read_text())
        result = fn(row)
        done.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({'id': row['id'], **{k: result[k] for k in ('error',) if k in result}}, ensure_ascii=False), flush=True)
        return result
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, rows))


def paraphrase(a):
    model = OpenAICompatTeacher()
    def fn(row):
        try:
            decision, usage = model.decide(row['question'], [], a.out / 'paraphrase_calls' / row['id'],
                                           system=PARAPHRASE_PROMPT, output_schema=PARAPHRASE_SCHEMA)
            text = decision.get('question', '').strip()
            problems = paraphrase_problems(row['question'], text)
            return {'id': row['id'], 'template_question': row['question'], 'paraphrase': text, 'problems': problems,
                    'final_question': row['question'] if problems else text, 'used_paraphrase': not problems,
                    'usage': usage, 'model': model.model, 'error': None}
        except Exception as exc:
            return {'id': row['id'], 'template_question': row['question'], 'paraphrase': None, 'problems': ['call failed'],
                    'final_question': row['question'], 'used_paraphrase': False, 'usage': None, 'model': model.model,
                    'error': f'{type(exc).__name__}: {exc}'}
    results = run_all(a.out / 'paraphrase', items(), fn, a.workers)
    print(json.dumps({'used_paraphrase': sum(r['used_paraphrase'] for r in results), 'errors': sum(bool(r['error']) for r in results),
                      'rejections': Counter(p.split(':')[0] for r in results for p in r['problems'])}, ensure_ascii=False))


def final_questions(a):
    return {r['id']: r['final_question'] for r in (json.loads(p.read_text()) for p in (a.out / 'paraphrase').glob('*.json'))}


def judge(a):
    finals = final_questions(a)
    rows = items()
    if not {r['id'] for r in rows} <= set(finals):
        raise ValueError('paraphrase stage incomplete')
    if a.judge == 'sol':
        model = CodexTeacher(CODEX, a.out / f'judge_{a.judge}_cwd')
        name = 'gpt-5.6-sol (Codex, reasoning=low)'
    else:
        model = OpenAICompatTeacher()
        name = f'{model.model} ({model.base_url})'
    def fn(row):
        try:
            decision, usage = model.decide(finals[row['id']], [], a.out / f'judge_{a.judge}_calls' / row['id'],
                                           system=JUDGE_PROMPT, output_schema=JUDGE_SCHEMA)
            return {'id': row['id'], 'question': finals[row['id']], 'judge': name, **decision, 'usage': usage, 'error': None}
        except Exception as exc:
            return {'id': row['id'], 'question': finals[row['id']], 'judge': name, 'ambiguous': None, 'issues': [], 'reason': '',
                    'usage': None, 'error': f'{type(exc).__name__}: {exc}'}
    results = run_all(a.out / f'judge_{a.judge}', rows, fn, a.workers)
    print(json.dumps({'ambiguous': sum(r['ambiguous'] is True for r in results), 'clear': sum(r['ambiguous'] is False for r in results),
                      'errors': sum(bool(r['error']) for r in results)}, ensure_ascii=False))


def reach(a):
    facts = {f['fact_id']: f for f in map(json.loads, LEDGER.read_text().splitlines())}
    health = request_json(a.url + '/health')
    dataset = health['admission']['dataset_sha256']
    needed = sorted({fid for r in items() for fid in r['facts']})
    def fn(row):
        fact = facts[row['id']]
        args = reachability_query(fact)
        response = search(a.url, args, dataset)
        return {'id': fact['fact_id'], 'query': args, 'rank': reachable(fact, response['results']),
                'returned': [c['chunk_id'] for c in response['results']], 'error': None}
    results = run_all(a.out / 'reach', [{'id': f} for f in needed], fn, 1)
    print(json.dumps({'facts': len(results), 'reached_top5': sum(r['rank'] is not None for r in results)}, ensure_ascii=False))


def assemble(a):
    rows = items()
    finals = final_questions(a)
    judges = {name: {r['id']: r for r in (json.loads(p.read_text()) for p in (a.out / f'judge_{name}').glob('*.json'))}
              for name in ('sol', 'deepseek')}
    reach_rank = {r['id']: r['rank'] for r in (json.loads(p.read_text()) for p in (a.out / 'reach').glob('*.json'))}
    for name, verdicts in judges.items():
        if not {r['id'] for r in rows} <= set(verdicts) or any(verdicts[r['id']]['error'] for r in rows):
            raise ValueError(f'judge {name} incomplete or failed; rerun that stage')
    selected, dropped, waivers = {'dev': [], 'test': []}, [], []
    for part in ('dev', 'test'):
        for qtype in sorted({r['type'] for r in rows if r['split'] == part}):
            ordered = sorted((r for r in rows if r['split'] == part and r['type'] == qtype), key=lambda r: r['rank'])
            need = sum(not r['reserve'] for r in ordered)
            for r in ordered:
                flags = {n: judges[n][r['id']] for n in judges if judges[n][r['id']]['ambiguous']}
                waived = bool(flags) and r['template'] in WAIVABLE and all(
                    i['aspect'] in ('period', 'calculation') for v in flags.values() for i in v['issues'])
                if waived:
                    waivers.append({'id': r['id'], 'split': part, 'template': r['template'], 'decision': WAIVABLE[r['template']],
                                    'flagged_by': {n: {'issues': v['issues'], 'reason': v['reason']} for n, v in flags.items()}})
                elif flags:
                    dropped.append({'id': r['id'], 'split': part, 'type': qtype, 'reserve': r['reserve'], 'question': finals[r['id']],
                                    'flagged_by': {n: {'issues': v['issues'], 'reason': v['reason']} for n, v in flags.items()}})
                    continue
                if len([x for x in selected[part] if x['type'] == qtype]) < need:
                    ranks = [reach_rank.get(f) for f in r['facts']]
                    tags = sorted(set(r['tags']) | ({'retrieval_miss'} if any(x is None for x in ranks) else set()))
                    selected[part].append({**r, 'question': finals[r['id']], 'template_question': r['question'],
                                           'tags': tags, 'reach_ranks': ranks, 'backfilled': r['reserve']})
            got = len([x for x in selected[part] if x['type'] == qtype])
            if got < need:
                raise ValueError(f'{part} {qtype}: only {got}/{need} after dropping ambiguous items')
    out = a.out / 'assembled'
    out.mkdir(parents=True, exist_ok=True)
    for part, cs in selected.items():
        (out / f'{part}_gold.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in cs))
        (out / f'{part}_questions.jsonl').write_text(''.join(json.dumps({'id': c['id'], 'question': c['question']}, ensure_ascii=False) + '\n' for c in cs))
    (out / 'dropped.jsonl').write_text(''.join(json.dumps(d, ensure_ascii=False) + '\n' for d in dropped))
    (out / 'waived_flags.jsonl').write_text(''.join(json.dumps(w, ensure_ascii=False) + '\n' for w in waivers))
    summary = {'selected': {p: len(c) for p, c in selected.items()},
               'backfilled': {p: sum(c['backfilled'] for c in cs) for p, cs in selected.items()},
               'dropped': dict(Counter((d['split'], d['type'], 'reserve' if d['reserve'] else 'main') for d in dropped)),
               'dropped_by_judge': dict(Counter(n for d in dropped for n in d['flagged_by'])),
               'waived_flags': dict(Counter(w['template'] for w in waivers)),
               'paraphrase_used': {p: sum(c['question'] != c['template_question'] for c in cs) for p, cs in selected.items()},
               'retrieval_miss_items': {p: sum('retrieval_miss' in c['tags'] for c in cs) for p, cs in selected.items()},
               'hard_location_share': {p: round(sum('hard_location' in c['tags'] for c in cs) / len(cs), 3) for p, cs in selected.items()},
               'files_sha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.glob('*.jsonl'))}}
    summary['dropped'] = {'|'.join(k): v for k, v in summary['dropped'].items()}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    spot_check_page(selected['test'], out)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def spot_check_page(test, out):
    """20 random Test items for the user's review, with the source rows behind every fact."""
    facts = {f['fact_id']: f for f in map(json.loads, LEDGER.read_text().splitlines())}
    sample = sorted(random.Random(20260923).sample(test, 20), key=lambda c: c['type'])
    lines = ['# Test 抽查（20 道）', '',
             '请逐题判断：题面是否清楚、无歧义；答案是否正确（可按下方原文数值自行验算）。有问题的题在下方记录题目 ID 和原因。', '']
    for i, c in enumerate(sample, 1):
        ans = c['answer']
        shown = '无法回答（证据不足）' if ans['status'] == 'insufficient' else f"{ans['value']} {ans['unit']}" + (f"，选中公司：{ans['selected_company']}" if ans['selected_company'] else '')
        lines += [f"## {i}. `{c['id']}`（{c['type']}）", '', c['question'], '', f'**参考答案**：{shown}', '', f"推导：`{c['derivation']}`", '']
        for fid in c['facts']:
            f = facts[fid]
            lines.append(f"- {f['company']} · {'母公司' if f['scope'] == 'parent' else '合并'} · {f['metric_label']} · {f['period']}："
                         f"原文 **{f['raw_value']}** {f['unit']}（第 {f['page']} 页，块 `{f['chunk_id']}`）")
        if not c['facts']:
            lines.append('- 无事实：这道题的前提在语料中不存在，正确答案是拒答。')
        lines += ['', '判定：☐ 通过　☐ 有问题：', '']
    (out / 'SPOT_CHECK.md').write_text('\n'.join(lines))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=['paraphrase', 'judge', 'reach', 'assemble'])
    p.add_argument('--out', type=Path, default=Path('data/bench/s4_v1r1'))
    p.add_argument('--judge', choices=['sol', 'deepseek'])
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--url', default='http://127.0.0.1:8100')
    a = p.parse_args()
    {'paraphrase': paraphrase, 'judge': judge, 'reach': reach, 'assemble': assemble}[a.stage](a)


if __name__ == '__main__':
    main()
