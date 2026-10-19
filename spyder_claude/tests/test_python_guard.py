# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Tests for the hook that keeps Python in Spyder's consoles."""

# Standard library imports
import json
import subprocess
import sys

# Third-party imports
import pytest

# Local imports
from spyder_claude import python_guard
from spyder_claude.python_guard import check


@pytest.mark.parametrize(
    "command",
    [
        "python script.py",
        "python3 -c 'print(1)'",
        "python3.12 -m pip install numpy",
        "/opt/conda/envs/x/bin/python train.py --epochs 3",
        ".venv/bin/python -m pytest",
        "ipython -c 'import os'",
        "pytest -q tests",
        "pip install pandas",
        "uv run python main.py",
        "uv pip install requests",
        "uvx ruff check .",
        "conda run -n base python -V2",
        "poetry run pytest",
        "./analysis.py",
        "cd src && python setup.py build",
        "make clean; python -m build",
        "ls | python -c 'import sys; print(sys.stdin.read())'",
        "VAR=1 python run.py",
        "env -i PATH=/usr/bin python run.py",
        "timeout 10 python run.py",
        "sudo -u me python run.py",
        "bash -c 'cd x && python run.py'",
        "echo $(python -c 'print(1)')",
        "echo `python -V2`",
        "python <<EOF\nprint(1)\nEOF",
        "git status\npython run.py",
    ],
)
def test_runs_python(command):
    assert check(command)


@pytest.mark.parametrize(
    "command",
    [
        "ls *.py",
        "cat script.py",
        "grep -rn python src",
        'echo "run python later"',
        "git commit -m 'Port to python 3.14'",
        "which python",
        "python --version",
        "python3 -V",
        "npm run build",
        "conda env list",
        "uv --version",
        "ruff check .",
        "sed -i 's/python/py/' notes.txt",
        "",
    ],
)
def test_doesnt_run_python(command):
    assert not check(command)


def test_allowed_commands():
    assert not check("pytest -q tests", {"pytest"})
    assert check("python -m pytest", {"pytest"})
    assert not check("cd x && pytest", {"pytest"})


def run_hook(event, *args):
    result = subprocess.run(
        [sys.executable, "-I", python_guard.__file__, *args],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout else None


def test_hook():
    """The hook denies Python commands with a reason for Claude."""
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "python train.py"},
    }
    output = run_hook(event)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "execute_code" in output["permissionDecisionReason"]

    # Other commands, other tools and allowed commands go through
    event["tool_input"]["command"] = "ls"
    assert run_hook(event) is None
    assert run_hook({**event, "tool_name": "Read"}) is None
    event["tool_input"]["command"] = "pytest"
    assert run_hook(event, "--allow", "pytest") is None
