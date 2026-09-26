"""Estimate tokens for a planned batch from past runs of the same config (REGISTRY.json); optional hard limit.

Exit 1 when --limit-tokens is given and the estimate exceeds it. Remaining provider quota is not exposed by the
API, so the limit is the user's own number (e.g. what is left on the OpenCode console).
"""
import argparse
import json
from pathlib import Path
import statistics


def estimate(registry, config, items):
    rows = [r for r in registry if r['category'] == 'valid' and r.get('tokens_per_item')]
    same = [r['tokens_per_item'] for r in rows if r.get('config') == config]
    arm = json.loads(Path(config).read_text())['arm']
    basis = same or [r['tokens_per_item'] for r in rows if r.get('arm') == arm]
    if not basis:
        raise SystemExit(f'no past runs to estimate {config} from')
    per_item = statistics.mean(basis)
    return {'config': config, 'basis': 'same config' if same else f'same arm ({arm})', 'basis_runs': len(basis),
            'tokens_per_item': round(per_item), 'max_tokens_per_item_run': max(basis), 'items': items, 'estimate_tokens': round(per_item * items)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--items', type=int, required=True, help='total item-runs planned, e.g. 3 x 44 fault + 3 x 50 clean = 282')
    p.add_argument('--registry', type=Path, default=Path('data/bench/runs/REGISTRY.json'))
    p.add_argument('--limit-tokens', type=int)
    a = p.parse_args()
    e = estimate(json.loads(a.registry.read_text()), a.config, a.items)
    print(json.dumps(e, ensure_ascii=False))
    if a.limit_tokens is not None and e['estimate_tokens'] > a.limit_tokens:
        print(f"refused: estimate {e['estimate_tokens']:,} tokens exceeds limit {a.limit_tokens:,}")
        raise SystemExit(1)


if __name__ == '__main__':
    main()
