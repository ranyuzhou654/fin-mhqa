"""Provenance and shared-resource helpers for runs: git state and a cross-process retrieval lock."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import subprocess

RETRIEVAL_LOCK = Path(os.environ.get('FINMHQA_RETRIEVAL_LOCK', 'data/.retrieval.lock'))


def git_state(cwd='.'):
    """Commit and whether tracked files differ from it (untracked data files are ignored)."""
    def git(*args):
        return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
    try:
        changed = [l[3:] for l in git('status', '--porcelain', '--untracked-files=no').splitlines()]
        return {'commit': git('rev-parse', 'HEAD'), 'dirty': bool(changed), 'changed_files': changed[:50]}
    except (OSError, subprocess.CalledProcessError) as exc:
        return {'commit': None, 'error': str(exc)[:200]}


@contextmanager
def retrieval_lock(path=RETRIEVAL_LOCK):
    """Serialize calls to the single-slot retrieval server across processes (threads use their own lock too)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
