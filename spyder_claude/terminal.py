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
import subprocess
import sys
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

    # Files of each session: its MCP config, its Claude Code settings (with
    # its hooks) and the context its SessionStart hook adds
    KINDS = ("mcp", "settings", "context")

    def get_path(self, session_id: str, kind: str = "mcp") -> str | None:
        """Path of a file of a session."""
        if self._directory is None:
            return None
        return osp.join(self._directory, f"{kind}-{session_id}.json")

    def write(self, session_id: str, connection_info: dict) -> str:
        """
        Write the config of a session for ``connection_info`` and return its
        path.
        """
        config = {
            "mcpServers": {
                MCP_SERVER_NAME: {
                    "type": "http",
                    "url": connection_info["url"],
                    "headers": connection_info["headers"],
                }
            }
        }
        return self.write_json(session_id, "mcp", config)

    def write_json(self, session_id: str, kind: str, data) -> str:
        """Write a file of a session and return its path."""
        if self._directory is None:
            # mkdtemp creates the directory with mode 0700
            self._directory = tempfile.mkdtemp(prefix="spyder-claude-")

        path = self.get_path(session_id, kind)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        return path

    def remove(self, session_id: str | None = None) -> None:
        """Remove the files of a session, or all of them."""
        if self._directory is None:
            return
        if session_id is None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None
            return
        for kind in self.KINDS:
            try:
                os.remove(self.get_path(session_id, kind))
            except OSError:
                pass


def join_command(args: list[str]) -> str:
    """Command line for ``args``, quoted for the platform's shell."""
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def hook_command(script: str, *args: str) -> str:
    """Command that runs a hook script of this package with this Python."""
    path = osp.join(osp.dirname(__file__), script)
    # -I keeps the user's environment and current directory out of it
    return join_command([sys.executable, "-I", path, *args])


def claude_settings(
    context_path: str,
    keep_python_in_spyder: bool = True,
    allowed_commands: list[str] | None = None,
) -> dict:
    """
    Claude Code settings for a session, passed with ``--settings``. They add
    to the user's settings instead of replacing them.

    Parameters
    ----------
    context_path: str
        Path of the session's context file (see ``session_context.py``).
    keep_python_in_spyder: bool, optional
        Whether to deny shell commands that run Python (see
        ``python_guard.py``).
    allowed_commands: list[str], optional
        Commands to allow anyway, e.g. ``["pytest"]``.
    """
    context_hook = {
        "type": "command",
        "command": hook_command("session_context.py", context_path),
        "timeout": 10,
    }
    hooks = {
        # Guidance about Spyder, and what happened in Spyder since the
        # session last heard from it
        "SessionStart": [{"hooks": [context_hook]}],
        "UserPromptSubmit": [{"hooks": [context_hook]}],
    }
    if keep_python_in_spyder:
        allow = []
        for command in allowed_commands or []:
            allow += ["--allow", command]
        hooks["PreToolUse"] = [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_command("python_guard.py", *allow),
                        "timeout": 10,
                    }
                ],
            }
        ]
    return {"hooks": hooks}


def claude_code_command(
    executable: str,
    config_path: str,
    args: list[str] | None = None,
    replace_shell: bool = os.name != "nt",
    settings_path: str | None = None,
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
    settings_path: str, optional
        Path of the session's settings file (see :func:`claude_settings`).
    """
    parts = [executable, "--mcp-config", config_path]
    if settings_path is not None:
        parts += ["--settings", settings_path]
    parts += args or []
    command = " ".join(shlex.quote(part) for part in parts)
    return f"exec {command}" if replace_shell else command
