"""benchmark_v2 quality gate (docs/benchmark/BENCHMARK_V2_DESIGN.md): ambiguity judges on follow-up turns, then freeze.

  judge   --judge qwen|deepseek  every follow-up turn of every candidate dialogue (main and reserve), with the
                                 earlier user turns as context; the judges see questions only, never answers
  freeze                         drop any dialogue flagged by either judge, refill from same-type reserves in rank
                                 order, write the frozen question files and a manifest, and a local spot-check page

The rule is the one used for benchmark_v1 S4: a flag by any judge removes the dialogue; nothing is rewritten.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
from finmhqa.bench.quality import JUDGE_SCHEMA

SRC = Path('data/bench/generated_v2')
OUT = Path('data/bench/s4_v2')
FROZEN = Path('data/bench/benchmark_v2')
GOLD = Path('data/bench/benchmark_v2_gold')
# Second judge: benchmark_v1 used Sol through the Codex CLI, which is broken on this machine (missing native binary,
# 2026-10-16); qwen3.8-max is from another provider than the agent's model, keeping that requirement.
JUDGES = {'deepseek': 'deepseek-v4.1-flash', 'qwen': 'qwen3.8-max'}
JUDGE_PROMPT = (
    '你是评测题质量审核员。背景：题目面向10家A股上市公司的2025年半年度报告，报告含合并报表与母公司报表，'
    '利润表和现金流量表列示2025年上半年与2024年上半年，资产负债表列示2025年6月30日与2024年12月31日。'
    '输入是同一段对话中用户依次说的话（不含系统的回答）。请只判断「最后一句」在结合前文之后，'
    '公司、指标、口径、期间、计算方式、选择条件和答案格式是否都能唯一确定。'
    '「同期」「它」「这家」「较高的那家」「两者」「前者减后者」等指代或省略，如果按前文只有一种合理理解，就不算歧义；'
    '需要比较后选择的，只要比较条件在前文中明确，也不算歧义。不需要回答问题，也不要判断报告里有没有该数据。'
    '不调用任何工具。只输出符合schema的JSON。')


def candidates():
    for part in ('dev', 'test'):
        for line in (SRC / f'{part}_gold.jsonl').read_text().splitlines():
            yield part, json.loads(line)


def judge(a):
    from finmhqa.agent.openai_compat import OpenAICompatTeacher
    base = json.loads(Path('configs/bench_b3_graph_repair_v2_planner_v2.json').read_text())['teacher']['base_url']
    name = JUDGES[a.judge]
    model = OpenAICompatTeacher(base, name, repair=True)
    out = OUT / f'judge_{a.judge}'
    out.mkdir(parents=True, exist_ok=True)
    tasks = []
    for part, d in candidates():
        for t in d['turns'][1:]:
            key = f"{d['id']}_t{t['turn']}"
            if not (out / f'{key}.json').exists():
                tasks.append((part, d, t, key))

    def one(task):
        part, d, t, key = task
        lines = [f'第{u["turn"]}句：{u["utterance"]}' for u in d['turns'][:t['turn']]]
        calls = OUT / f'judge_{a.judge}_calls' / key
        if calls.exists():
            calls.rename(calls.with_name(f'{key}.retry_{int(time.time())}'))
        try:
            decision, usage = model.decide('\n'.join(lines), [], calls, system=JUDGE_PROMPT, output_schema=JUDGE_SCHEMA)
            row = {'id': d['id'], 'split': part, 'turn': t['turn'], 'judge': name, **decision, 'usage': usage, 'error': None}
        except Exception as exc:   # kept as a failed judgement; rerun to retry
            print('error', key, exc, flush=True)
            return
        (out / f'{key}.json').write_text(json.dumps(row, ensure_ascii=False, indent=2))
        print(key, 'AMBIGUOUS' if row['ambiguous'] else 'ok', flush=True)
    with ThreadPoolExecutor(a.workers) as pool:
        list(pool.map(one, tasks))


def freeze(a):
    verdicts = {}
    for j in JUDGES:
        for f in (OUT / f'judge_{j}').glob('*.json'):
            r = json.loads(f.read_text())
            verdicts.setdefault(r['id'], []).append(r)
    rows = list(candidates())
    expected = {d['id']: len(d['turns']) - 1 for _, d in rows}
    missing = [i for i, n in expected.items() if len(verdicts.get(i, [])) != 2 * n]
    if missing:
        raise SystemExit(f'{len(missing)} dialogues lack a verdict from both judges; run judge first')
    flagged = {i for i, vs in verdicts.items() if any(v['ambiguous'] for v in vs)}
    FROZEN.mkdir(parents=True, exist_ok=False)
    GOLD.mkdir(parents=True, exist_ok=False)
    report = {'flagged': sorted(flagged), 'judges': list(JUDGES.values()), 'splits': {}}
    for part in ('dev', 'test'):
        pool = [d for p, d in rows if p == part]
        quota = {t: sum(1 for d in pool if d['type'] == t and not d['reserve']) for t in {d['type'] for d in pool}}
        chosen, short = [], {}
        for t, need in sorted(quota.items()):
            ok = [d for d in sorted((d for d in pool if d['type'] == t), key=lambda d: d['rank']) if d['id'] not in flagged]
            chosen += ok[:need]
            if len(ok) < need:
                short[t] = need - len(ok)
        if short:
            raise SystemExit(f'{part}: not enough unflagged dialogues {short}; extend reserves before freezing')
        (GOLD / f'{part}_gold.jsonl').write_text(''.join(json.dumps(d, ensure_ascii=False) + '\n' for d in chosen))
        (FROZEN / f'{part}_dialogues.jsonl').write_text(''.join(
            json.dumps({'id': d['id'], 'turns': [t['utterance'] for t in d['turns']]}, ensure_ascii=False) + '\n' for d in chosen))
        report['splits'][part] = {'dialogues': len(chosen), 'turns': sum(len(d['turns']) for d in chosen),
                                  'replaced': sum(1 for d in pool if not d['reserve'] and d['id'] in flagged),
                                  'from_reserve': sum(1 for d in chosen if d['reserve'])}
    report['sha256'] = {f'{part}_{kind}': hashlib.sha256((base / f'{part}_{kind}.jsonl').read_bytes()).hexdigest()
                        for part in ('dev', 'test') for base, kind in ((FROZEN, 'dialogues'), (GOLD, 'gold'))}
    (FROZEN / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    spot_check(chosen_test=[json.loads(l) for l in (GOLD / 'test_gold.jsonl').read_text().splitlines()])
    print(json.dumps(report, ensure_ascii=False, indent=1))


def spot_check(chosen_test, n=10, seed=20261016):
    """Local page (contains Test answers, never committed) with n Test dialogues picked by a fixed seed."""
    import random
    rng = random.Random(seed)
    picked = rng.sample(chosen_test, n)
    lines = ['# benchmark_v2 Test 抽查（本地文件，含答案，不进 git）', '',
             '请逐段检查：每一轮的说法是否自然、指代是否只有一种理解、答案是否正确。有问题的在对应轮次后注明。', '']
    for d in picked:
        lines += [f"## {d['id']}（{d['type']}）", '']
        for t in d['turns']:
            a = t['answer']
            lines += [f"**第 {t['turn']} 轮**：{t['utterance']}", '',
                      f"- 完整问题：{t['standalone']}",
                      f"- 答案：{a['value']} {a['unit']}" + (f"（选定 {a['selected_company']}）" if a['selected_company'] else ''),
                      f"- 推导：`{t['derivation']}`", '']
    (OUT / 'SPOT_CHECK.md').write_text('\n'.join(lines))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    j = sub.add_parser('judge')
    j.add_argument('--judge', choices=list(JUDGES), required=True)
    j.add_argument('--workers', type=int, default=4)
    sub.add_parser('freeze')
    a = p.parse_args()
    {'judge': judge, 'freeze': freeze}[a.cmd](a)


if __name__ == '__main__':
    main()
