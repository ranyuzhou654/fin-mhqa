"""The demo replays the shipped example trajectories without model calls."""
import subprocess
import sys
from pathlib import Path
import pytest

EXAMPLES = sorted(Path('docs/examples').glob('*/trajectory.jsonl'))


@pytest.mark.parametrize('trajectory', EXAMPLES, ids=lambda p: p.parent.name)
def test_replay_renders_every_role_and_the_answer(trajectory):
    out = subprocess.run([sys.executable, 'scripts/demo.py', '--replay', str(trajectory.parent), '--no-color'],
                         capture_output=True, text=True, check=True, env={'PYTHONPATH': 'src'}).stdout
    assert '[规划' in out and '[核验' in out and '→ 检索' in out and '答案：' in out and '结束原因：ANSWERED' in out


def test_examples_exist():
    assert {p.parent.name for p in EXAMPLES} >= {'f2_column_swap_caught', 'clean_single_hop'}
