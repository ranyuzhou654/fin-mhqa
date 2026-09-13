"""Produce deterministic review samples; manual labels are required for acceptance."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import random


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('chunks')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    groups = defaultdict(list)
    for line in Path(args.chunks).read_text(encoding='utf-8').splitlines():
        c = json.loads(line)
        groups[c['company'], c['chunk_type']].append(c)
    rng = random.Random(42)
    samples = []
    for (company, kind), chunks in sorted(groups.items()):
        print(f'{company}\t{kind}\t{len(chunks)}')
        for c in rng.sample(chunks, min(10, len(chunks))):
            samples.append(dict(c, breadcrumb_correct=None, table_correct=None, review_notes=''))
    with Path(args.out).open('x', encoding='utf-8') as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False)+'\n')
    print('Samples only; no quality pass claimed. Groups below 10 require more evidence.')

if __name__ == '__main__':
    main()
