# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Tests for running Claude Code in Spyder's terminal."""

# Standard library imports
import json
import os
import os.path as osp
import shlex
import stat
import subprocess
import sys

# Third-party imports
import pytest

# Local imports
from spyder_claude.terminal import (
    MCPConfigFile,
    claude_code_command,
    claude_settings,
)

CONNECTION_INFO = {
    "url": "http://127.0.0.1:8768/mcp",
    "headers": {"Authorization": "Bearer secret"},
}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_config_file_is_private():
    config = MCPConfigFile()
    path = config.write("session-1", CONNECTION_INFO)
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(osp.dirname(path)).st_mode) == 0o700
    finally:
        config.remove()


def test_config_files_per_session():
    config = MCPConfigFile()
    path = config.write("session-1", CONNECTION_INFO)
    with open(path) as f:
        assert json.load(f) == {
            "mcpServers": {"spyder": {"type": "http", **CONNECTION_INFO}}
        }

    # Writing again reuses the session's file
    assert config.write("session-1", CONNECTION_INFO) == path

    # Other sessions get their own file in the same directory
    other = config.write("session-2", CONNECTION_INFO)
    assert other != path
    assert osp.dirname(other) == osp.dirname(path)

    config.remove("session-1")
    assert not osp.exists(path)
    assert osp.exists(other)

    config.remove()
    assert not osp.exists(osp.dirname(path))
    assert config.get_path("session-2") is None


def test_command():
    command = claude_code_command(
        "claude", "/tmp/a dir/mcp.json", ["--continue"], replace_shell=True
    )
    assert shlex.split(command) == [
        "exec", "claude", "--mcp-config", "/tmp/a dir/mcp.json", "--continue"
    ]

    command = claude_code_command(
        "claude", "/tmp/mcp.json", replace_shell=False
    )
    assert shlex.split(command) == ["claude", "--mcp-config", "/tmp/mcp.json"]


def test_session_files(tmp_path):
    """Each session has its settings and context files, removed with it."""
    config = MCPConfigFile()
    config.write("session-1", CONNECTION_INFO)
    context = config.write_json("session-1", "context", {"guidance": "Hi"})
    settings = config.write_json(
        "session-1", "settings", claude_settings(context)
    )
    assert osp.dirname(settings) == osp.dirname(context)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(settings).st_mode) == 0o600

    config.remove("session-1")
    for kind in MCPConfigFile.KINDS:
        assert not osp.exists(config.get_path("session-1", kind))
    config.remove()


def test_settings_hooks():
    settings = claude_settings("/tmp/a dir/context.json", True, ["pytest"])
    session_start = settings["hooks"]["SessionStart"][0]["hooks"][0]
    assert "session_context.py" in session_start["command"]
    assert "/tmp/a dir/context.json" in shlex.split(
        session_start["command"]
    )
    guard = settings["hooks"]["PreToolUse"][0]
    assert guard["matcher"] == "Bash"
    assert shlex.split(guard["hooks"][0]["command"])[-2:] == [
        "--allow", "pytest"
    ]

    # Python in the shell can be allowed
    assert "PreToolUse" not in claude_settings("/tmp/context.json", False)[
        "hooks"
    ]


def test_session_context(tmp_path):
    """The context depends on whether the conversation was resumed."""
    from spyder_claude import session_context

    path = tmp_path / "context.json"
    path.write_text(json.dumps({
        "guidance": session_context.make_guidance(True),
        "resume_note": "The consoles were restarted.",
    }))

    def run(source):
        return subprocess.run(
            [sys.executable, "-I", session_context.__file__, str(path)],
            input=json.dumps({"source": source}),
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    started = run("startup")
    assert "execute_code" in started
    assert "blocked" in started
    assert "restarted" not in started
    assert "The consoles were restarted." in run("resume")
