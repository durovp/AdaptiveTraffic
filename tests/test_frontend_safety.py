from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def test_shipped_frontend_fails_closed():
    root=Path(__file__).parents[1]
    node=shutil.which('node')
    if not node:
        # Optional Playwright test installation includes its own Node runtime.
        candidates=list(Path(sys.prefix).glob('lib/python*/site-packages/playwright/driver/node'))
        node=str(candidates[0]) if candidates else None
    if not node: pytest.skip('Node or optional Playwright runtime needed for frontend safety checks')
    subprocess.run([node,'tests/frontend_safety.cjs'],cwd=root,check=True,capture_output=True,text=True,timeout=10)
