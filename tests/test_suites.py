"""Run each verification suite as a subprocess.

Each suite configures its environment (DATABASE_URL, secrets) before
importing the app, so suites must not share a process with each other or
with plugins that might import the app early.
"""
import os
import subprocess
import sys

import pytest

SUITES = [
    'test_critical_fixes.py',
    'test_high_fixes.py',
    'test_medium_fixes.py',
    'test_deferred_mediums.py',
    'test_low_fixes.py',
]


@pytest.mark.parametrize('suite', SUITES)
def test_suite(suite):
    path = os.path.join(os.path.dirname(__file__), suite)
    result = subprocess.run(
        [sys.executable, path],
        capture_output=True,
        text=True,
        timeout=600,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    assert result.returncode == 0, f'{suite} failed (exit {result.returncode})'
