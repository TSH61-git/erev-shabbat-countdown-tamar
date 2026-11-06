import shutil
import subprocess

import pytest

from shared.config import PROJECT_ROOT

NODE = shutil.which("node")


# The page's list rules live in frontend/rows.js; running Node's own test runner from here
# keeps "pytest -q" the one command that checks everything.
@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_task_list_rules_in_rows_js():
    r = subprocess.run([NODE, "--test", "tests/frontend/rows.test.mjs"], cwd=PROJECT_ROOT,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
