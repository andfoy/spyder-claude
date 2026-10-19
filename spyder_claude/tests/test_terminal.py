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

# Third-party imports
import pytest

# Local imports
from spyder_claude.terminal import MCPConfigFile, claude_code_command

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
