"""Shared helpers for SimCast's tests: the repo's path, and pass/fail bookkeeping."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

_failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        _failures.append(what)
    return ok


def done():
    """Exit 0 when every check passed, 1 otherwise."""
    print(f"{'PASSED' if not _failures else f'FAILED ({len(_failures)})'}", flush=True)
    sys.exit(1 if _failures else 0)
