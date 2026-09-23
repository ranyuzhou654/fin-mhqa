"""Model-only Judge comparison on the frozen calibration packet; no human reference, no accuracy claims."""
import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path
from finmhqa.qa.calibration import DIMENSIONS, BAD, validate_reviews
from score_quality_calibration import load_packet

# Dimension each controlled edit was designed to break; design intent only, not a verified label.
EDIT_TARGET = {'premature_rounding': 'precision', 'wrong_scope': 'scope', 'wrong_period': 'period',
               'unseen_citation': 'citation_coverage', 'missing_header_citation': 'citation_coverage',
               'unsupported_number': 'numeric_grounding'}


def unseen_citations(case):
    seen = {c['chunk_id'] for o in case['observations'] for c in o['response'].get('results', [])}
    return sorted(set(case['final'].get('citations', [])) - seen)


def compare(cases, meta, judges):
    by_case = {c['id']: c for c in cases}
    labels = {name: {r['id']: r['labels'] for r in reviews if all(v is not None for v in r['labels'].values())}
              for name, reviews in judges.items()}
    complete = sorted(set.intersection(*(set(v) for v in labels.values())))
    pairs = {}
    for a, b in combinations(judges, 2):
        n = agree = 0
        for cid in complete:
            for d in DIMENSIONS:
                x, y = labels[a][cid][d], labels[b][cid][d]
                if x == 'not_applicable':
                    continue
                n += 1; agree += x == y
        pairs[f'{a}~{b}'] = {'agree': agree, 'total': n, 'rate': round(agree / n, 4) if n else None}
    disputed, unanimous = [], 0
    for cid in complete:
        for d in DIMENSIONS:
            votes = {name: labels[name][cid][d] for name in judges}
            if 'not_applicable' in votes.values():
                continue
            if len(set(votes.values())) == 1:
                unanimous += 1
            else:
                disputed.append({'id': cid, 'dimension': d, **votes})
    answered = [cid for cid in complete if by_case[cid]['final']['status'] == 'answered']
    per_judge = {name: {'labels': dict(Counter(l[d] for l in labels[name].values() for d in DIMENSIONS if l[d] != 'not_applicable')),
                        'answered_all_supported': sum(all(labels[name][cid][d] == 'supported' for d in DIMENSIONS[:6]) for cid in answered),
                        'answered_flagged_any': sum(any(labels[name][cid][d] in BAD for d in DIMENSIONS[:6]) for cid in answered),
                        'completed': len(labels[name])} for name in judges}
    edits = []
    for cid, m in meta.items():
        target = EDIT_TARGET.get(m['variant'])
        if target and cid in complete:
            edits.append({'id': cid, 'edit': m['variant'], 'target_dimension': target,
                          **{name: labels[name][cid][target] for name in judges}})
    flagged = {name: sum(e[name] in BAD for e in edits) for name in judges}
    vote = []
    for cid in answered:
        for d in DIMENSIONS[:6]:
            bad = sum(labels[name][cid][d] in BAD for name in judges)
            if bad and bad < len(judges):
                vote.append({'id': cid, 'dimension': d, 'flagged_by': [n for n in judges if labels[n][cid][d] in BAD]})
    rules = [{'id': c['id'], 'unseen_citations': unseen_citations(c)} for c in cases if unseen_citations(c)]
    return {'scope': 'model-only comparison on 18 frozen Dev cases; no human reference, no accuracy or false-pass rate',
            'judges': list(judges), 'complete_in_all': len(complete), 'pairwise_agreement': pairs,
            'unanimous_dimensions': unanimous, 'disputed_dimensions': disputed, 'per_judge': per_judge,
            'controlled_edits': {'note': 'edit type is construction intent, not a verified label', 'items': edits,
                                 'target_flagged_missing_or_contradicted': flagged, 'total': len(edits)},
            'partial_flags_on_answered': vote,
            'deterministic_rule_hits': rules}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--packet', type=Path, default=Path('docs/evidence/quality_calibration_v1'))
    p.add_argument('--judge', nargs=2, action='append', metavar=('NAME', 'PREDICTIONS'), required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    cases, _ = load_packet(a.packet)
    manifest = json.loads((a.packet / 'manifest.json').read_text())
    meta = {c['id']: c for c in manifest['cases']}
    judges = {}
    for name, path in a.judge:
        reviews = json.loads(Path(path).read_text())
        validate_reviews(cases, reviews)
        judges[name] = reviews
    report = compare(cases, meta, judges)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('complete_in_all', 'pairwise_agreement', 'unanimous_dimensions')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
