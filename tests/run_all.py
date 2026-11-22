"""Run every test:  python tests/run_all.py

1. Python tests: scoring rules, API, error handling, stages, CSV import (fake AI, offline, instant)
2. Browser-level tests in jsdom (needs Node.js; installs jsdom into tests/frontend on first run)
Neither part calls the real AI or touches a real database. For a real end-to-end check see live_check.py.
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def run(cmd, cwd):
    print("\n$", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd).returncode


def main():
    results = {"python tests": run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], ROOT)}

    node, npm = shutil.which("node"), shutil.which("npm")
    if not (node and npm):
        print("\nNode.js not found: skipping browser-level tests (install Node 20+ to run them).")
    else:
        front = HERE / "frontend"
        if not (front / "node_modules").exists():
            run([npm, "install", "--silent"], front)
        results["browser tests"] = run([node, "--test"], front)

    print("\n" + "=" * 40)
    for name, code in results.items():
        print(f"{'PASS' if code == 0 else 'FAIL'}  {name}")
    sys.exit(1 if any(results.values()) else 0)


if __name__ == "__main__":
    main()
