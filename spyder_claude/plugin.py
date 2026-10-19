# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Claude Code plugin.

A pane that runs the Claude Code CLI in Spyder's terminal, connected to the
user's Spyder session (consoles, variables, plots and editor) through
Spyder's MCP server.
"""

# Standard library imports
from __future__ import annotations

import logging

# Third-party imports
from spyder.api.plugin_registration.decorators import (
    on_plugin_available,
    on_plugin_teardown,
)
from spyder.api.plugins import Plugins
from spyder.api.translations import _
from spyder_terminal.config import CONF_DEFAULTS as TERMINAL_CONF_DEFAULTS
from spyder_terminal.terminalplugin import TerminalPlugin

# Local imports
from spyder_claude.terminal import MCPConfigFile, claude_code_command
from spyder_claude.widgets.main_widget import ClaudeCodeWidget

logger = logging.getLogger(__name__)

# Terminal options (shell, cursor, buffer, etc.) used by the inherited
# terminal widgets, which read them from this plugin's section
TERMINAL_OPTIONS = dict(TERMINAL_CONF_DEFAULTS[0][1])


class ClaudeCode(TerminalPlugin):
    """Claude Code plugin."""

    NAME = "claude_code"
    REQUIRES = TerminalPlugin.REQUIRES + [Plugins.MCPServer]
    OPTIONAL = [
        Plugins.Editor,
        Plugins.Projects,
        Plugins.WorkingDirectory,
    ]
    TABIFY = [Plugins.Help]
    WIDGET_CLASS = ClaudeCodeWidget
    CONF_SECTION = NAME
    CONF_FILE = True
    CONF_VERSION = "0.1.0"
    CONF_DEFAULTS = [
        (CONF_SECTION, {
            **TERMINAL_OPTIONS,
            # Claude Code executable
            "claude_command": "claude",
        }),
    ]
    CONF_WIDGET_CLASS = None
    CAN_BE_DISABLED = True

    # ---- SpyderDockablePlugin API
    # -------------------------------------------------------------------------
    @staticmethod
    def get_name():
        return _("Claude Code")

    @staticmethod
    def get_description():
        return _(
            "Run Claude Code in Spyder, with access to your consoles, "
            "variables, plots and editor."
        )

    @classmethod
    def get_icon(cls):
        return cls.create_icon("DollarFileIcon")

    def on_initialize(self):
        super().on_initialize()
        self._mcp_config = MCPConfigFile()
        self._pending_sessions: list[tuple[str, list[str]]] = []
        self.get_widget().sig_session_requested.connect(
            self._on_session_requested
        )

    @on_plugin_available(plugin=Plugins.MCPServer)
    def on_mcp_server_available(self):
        mcp_server = self.get_plugin(Plugins.MCPServer)
        mcp_server.sig_server_started.connect(self._on_mcp_server_started)
        mcp_server.sig_server_stopped.connect(self._on_mcp_server_stopped)

    @on_plugin_teardown(plugin=Plugins.MCPServer)
    def on_mcp_server_teardown(self):
        mcp_server = self.get_plugin(Plugins.MCPServer)
        mcp_server.sig_server_started.disconnect(self._on_mcp_server_started)
        mcp_server.sig_server_stopped.disconnect(self._on_mcp_server_stopped)

    # These override (and disable) the terminal plugin's handlers: this
    # plugin has no preferences page yet, and remote servers can't reach
    # Spyder's MCP server.
    def on_preferences_available(self):
        pass

    def on_remoteclient_available(self):
        pass

    def on_remoteclient_teardown(self):
        pass

    def on_close(self, cancelable=False):
        result = super().on_close(cancelable)
        self._mcp_config.remove()
        return result

    # ---- Public API
    # -------------------------------------------------------------------------
    def new_session(
        self, path: str | None = None, args: list[str] | None = None
    ) -> None:
        """
        Start a new Claude Code session.

        Parameters
        ----------
        path: str, optional
            Directory to start the session in. By default, the current
            project or working directory.
        args: list[str], optional
            Extra arguments for Claude Code.
        """
        # Request it before showing the pane, which otherwise starts a
        # session of its own if there are none
        self.get_widget().request_session(path, args)
        self.switch_to_plugin()

    # ---- Private API
    # -------------------------------------------------------------------------
    def _on_session_requested(self, path: str, args: list[str]) -> None:
        mcp_server = self.get_plugin(Plugins.MCPServer)
        if mcp_server.is_server_running():
            self._start_session(path, args)
        else:
            # Sessions start when the server is ready
            self._pending_sessions.append((path, args))
            mcp_server.start_server()

    def _on_mcp_server_started(self, url: str) -> None:
        while self._pending_sessions:
            self._start_session(*self._pending_sessions.pop(0))

    def _on_mcp_server_stopped(self) -> None:
        if self._pending_sessions:
            logger.warning(
                "The MCP server stopped before Claude Code could start"
            )
            self._pending_sessions = []

    def _start_session(self, path: str, args: list[str]) -> None:
        mcp_server = self.get_plugin(Plugins.MCPServer)
        config_path = self._mcp_config.write(
            mcp_server.get_connection_info()
        )
        command = claude_code_command(
            self.get_conf("claude_command"), config_path, args
        )
        self.get_widget().start_session(command, path or None)
