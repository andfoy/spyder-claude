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
    path = config.write(CONNECTION_INFO)
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(osp.dirname(path)).st_mode) == 0o700
    finally:
        config.remove()


def test_config_file_contents_and_removal():
    config = MCPConfigFile()
    path = config.write(CONNECTION_INFO)
    with open(path) as f:
        assert json.load(f) == {
            "mcpServers": {"spyder": {"type": "http", **CONNECTION_INFO}}
        }

    # Writing again reuses the same file
    assert config.write(CONNECTION_INFO) == path

    config.remove()
    assert not osp.exists(osp.dirname(path))
    assert config.path is None


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
