"""Interactive, resumable human labelling for the frozen calibration packet; never shows Judge labels."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from finmhqa.qa.calibration import DIMENSIONS, validate_reviews

SHORT = {'s': 'supported', 'm': 'missing', 'c': 'contradicted', 'u': 'uncertain'}
HINT = {'numeric_grounding': '数值是否有原文与计算支持', 'unit_applicability': '单位是否适用于目标表',
        'period': '金额是否对应本期列', 'scope': '合并/母公司、归母/全部净利润', 'citation_coverage': '最终引用是否覆盖全部关键前提',
        'precision': '是否用原始精度、最后才舍入', 'abstention': '拒答时当前观测是否确实不足（s=确实不足 c=其实已够 u=无法裁定）'}


class Quit(Exception):
    pass


def ask(prompt, read):
    value = read(prompt).strip()
    if value == 'q':
        raise Quit
    return value


def resolve_ids(text, seen):
    ids = []
    for token in text.replace(',', ' ').split():
        match = [s for s in seen if s.startswith(token)]
        if len(match) != 1:
            raise ValueError(f'{token} 未唯一匹配已观测ID（匹配{len(match)}个）')
        ids.append(match[0])
    return ids


def label_case(case, review, reviewer, read=input, write=print, packet=Path('.')):
    seen = sorted({c['chunk_id'] for o in case['observations'] for c in o['response'].get('results', [])})
    answered = case['final']['status'] == 'answered'
    write(f"\n===== {case['id']} =====\n问题：{case['question']}")
    write('最终输出：' + json.dumps(case['final'], ensure_ascii=False, indent=2))
    write(f"可读证据：{packet / 'review_cases' / (case['id'] + '.md')}")
    write(f"已观测证据ID（可只输入前6位）：{' '.join(s[:10] for s in seen)}")
    for d in DIMENSIONS:
        if (d == 'abstention') == answered:
            review['labels'][d] = 'not_applicable'
            review['rationale'][d] = '已回答，不评拒答。' if answered else '拒答样本，按规则不评回答维度。'
            continue
        while True:
            label = ask(f"[{d}] {HINT[d]}  s/m/c/u > ", read)
            if label in SHORT:
                break
            write('请输入 s / m / c / u，或 q 保存退出')
        rationale = ''
        while not rationale:
            rationale = ask('  理由（必填）> ', read)
        while True:
            try:
                ids = resolve_ids(ask('  证据ID（可空，空格分隔）> ', read), seen)
                break
            except ValueError as exc:
                write(f'  {exc}')
        review['labels'][d], review['rationale'][d], review['evidence_ids'][d] = SHORT[label], rationale, ids
    review['notes'] = ask('备注（可空）> ', read)
    review['reviewer'], review['reviewer_type'] = reviewer, 'human'
    review['reviewed_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    return review


def save(path, reviews):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(reviews, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--packet', type=Path, default=Path('docs/evidence/quality_calibration_v1'))
    p.add_argument('--out', type=Path, default=Path('data/qa/quality_calibration_v1/reviewed.json'))
    p.add_argument('--redo', help='重新审核某个已完成ID')
    a = p.parse_args()
    cases = [json.loads(l) for l in (a.packet / 'cases.jsonl').read_text().splitlines()]
    if not a.out.exists():
        a.out.parent.mkdir(parents=True, exist_ok=True)
        save(a.out, json.loads((a.packet / 'reference.template.json').read_text()))
    reviews = json.loads(a.out.read_text())
    pending, _ = validate_reviews(cases, reviews, human=True)
    if a.redo:
        pending = [a.redo]
    by_id = {c['id']: c for c in cases}
    print(f'待审核 {len(pending)}/{len(cases)} 条；任意输入 q 保存已完成条目并退出。Judge意见不会显示。')
    if not pending:
        return
    reviewer = input('审核人姓名 > ').strip()
    if not reviewer:
        raise SystemExit('需要审核人')
    try:
        for cid in pending:
            index = next(i for i, r in enumerate(reviews) if r['id'] == cid)
            draft = json.loads(json.dumps(reviews[index]))
            reviews[index] = label_case(by_id[cid], draft, reviewer, packet=a.packet)
            validate_reviews(cases, reviews, human=True)
            save(a.out, reviews)
            print(f'已保存 {cid}')
    except Quit:
        print('已退出；未完成的当前条目不保存。')
    left, _ = validate_reviews(cases, reviews, human=True)
    print(f'剩余 {len(left)} 条。完成后运行 scripts/score_quality_calibration.py。')


if __name__ == '__main__':
    main()
