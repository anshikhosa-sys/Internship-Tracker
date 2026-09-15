"""
Run the whole Python test suite.

    python3 tests.py            everything
    python3 tests.py -k resume  pytest selection (legacy checks skipped)

`tests/legacy_checks.py` holds the v1 check-style tests for subsystems not yet
ported to pytest; it shrinks as each subsystem is rebuilt. Both must pass.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    args = sys.argv[1:]
    env = {**os.environ, "PYTHONPATH": ROOT}
    legacy = 0
    if not args and os.path.exists(os.path.join(ROOT, "tests", "legacy_checks.py")):
        print("== legacy checks ==")
        legacy = subprocess.call([sys.executable, "-m", "tests.legacy_checks"], cwd=ROOT, env=env)
    print("== pytest ==")
    result = subprocess.call([sys.executable, "-m", "pytest", "-q", "tests", *args], cwd=ROOT, env=env)
    return legacy or result


if __name__ == "__main__":
    sys.exit(main())
