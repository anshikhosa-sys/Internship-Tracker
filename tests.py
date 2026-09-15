"""
Run the whole Python test suite.

    python3 tests.py            everything
    python3 tests.py -k resume  pytest selection

Pytest with the repo root on the path. Browser tests are separate:
`python3 browser_tests.py`.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    args = sys.argv[1:]
    env = {**os.environ, "PYTHONPATH": ROOT}
    return subprocess.call([sys.executable, "-m", "pytest", "-q", "tests", *args], cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
