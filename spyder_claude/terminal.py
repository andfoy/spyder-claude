# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Helpers to run the Claude Code CLI connected to Spyder.

Users sign in to Claude Code themselves, so this works with Claude accounts
as well as API keys. Spyder only hosts the terminal and passes the MCP server
connection with ``--mcp-config``, without changing the user's Claude Code
configuration.
"""

# Standard library imports
from __future__ import annotations

import json
import os
import os.path as osp
import shlex
import shutil
import tempfile

# Name of Spyder's MCP server in Claude Code, which prefixes its tools
# (e.g. ``mcp__spyder__execute_code``).
MCP_SERVER_NAME = "spyder"


class MCPConfigFile:
    """
    MCP configuration files for the Claude Code CLI, one per session.

    The files contain the server token, so they're only readable by the user
    and they're removed when their session ends or Spyder closes. The token
    can't be passed on the command line instead, where other users could see
    it in the process list.
    """

    def __init__(self) -> None:
        self._directory: str | None = None

    def get_path(self, session_id: str) -> str | None:
        """Path of the config file of a session."""
        if self._directory is None:
            return None
        return osp.join(self._directory, f"mcp-{session_id}.json")

    def write(self, session_id: str, connection_info: dict) -> str:
        """
        Write the config of a session for ``connection_info`` and return its
        path.
        """
        if self._directory is None:
            # mkdtemp creates the directory with mode 0700
            self._directory = tempfile.mkdtemp(prefix="spyder-claude-")

        config = {
            "mcpServers": {
                MCP_SERVER_NAME: {
                    "type": "http",
                    "url": connection_info["url"],
                    "headers": connection_info["headers"],
                }
            }
        }
        path = self.get_path(session_id)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)
        return path

    def remove(self, session_id: str | None = None) -> None:
        """Remove the config of a session, or all of them."""
        if self._directory is None:
            return
        if session_id is None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None
            return
        try:
            os.remove(self.get_path(session_id))
        except OSError:
            pass


def claude_code_command(
    executable: str,
    config_path: str,
    args: list[str] | None = None,
    replace_shell: bool = os.name != "nt",
) -> str:
    """
    Shell command that starts Claude Code connected to Spyder.

    Parameters
    ----------
    executable: str
        Claude Code executable.
    config_path: str
        Path of the MCP config file.
    args: list[str], optional
        Extra arguments for Claude Code (e.g. ``["--continue"]``).
    replace_shell: bool, optional
        Whether Claude Code replaces the shell (with ``exec``), so the
        terminal closes when Claude Code exits. Not available on Windows.
    """
    parts = [executable, "--mcp-config", config_path] + (args or [])
    command = " ".join(shlex.quote(part) for part in parts)
    return f"exec {command}" if replace_shell else command
