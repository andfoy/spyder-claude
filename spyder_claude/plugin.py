# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Claude plugin.

A chat pane with Claude, running on the Claude Agent SDK. Claude works with
the user's Spyder session (consoles, variables, plots and editor) through
Spyder's MCP server.
"""

# Standard library imports
from __future__ import annotations

from collections.abc import Callable
import logging

# Third-party imports
from qtpy.QtCore import QTimer
from spyder.api.plugin_registration.decorators import (
    on_plugin_available,
    on_plugin_teardown,
)
from spyder.api.plugins import Plugins, SpyderDockablePlugin
from spyder.api.translations import _
from spyder.utils.misc import getcwd_or_home

# Local imports
from spyder_claude.client import AgentClient
from spyder_claude.protocol import MCP_TOOL_PREFIX
from spyder_claude.terminal import MCPConfigFile, claude_code_command
from spyder_claude.widgets.main_widget import ClaudeWidget

logger = logging.getLogger(__name__)

# Name of the spyder-terminal plugin, which is optional.
TERMINAL = "terminal"


class ClaudeAgent(SpyderDockablePlugin):
    """Claude plugin."""

    NAME = "claude_agent"
    REQUIRES = [Plugins.MCPServer]
    OPTIONAL = [Plugins.WorkingDirectory, TERMINAL]
    TABIFY = [Plugins.Help]
    WIDGET_CLASS = ClaudeWidget
    CONF_SECTION = NAME
    CONF_FILE = True
    CONF_VERSION = "0.1.0"
    CONF_DEFAULTS = [
        (CONF_SECTION, {
            # Model to use, or empty for the Claude Code default
            "model": "",
            # Claude Code executable used in the terminal
            "claude_command": "claude",
        }),
    ]

    # ---- SpyderDockablePlugin API
    # -------------------------------------------------------------------------
    @staticmethod
    def get_name():
        return _("Claude")

    @staticmethod
    def get_description():
        return _(
            "Chat with Claude about your code, data and plots. Claude can "
            "run code in your consoles and see your variables, figures and "
            "editor."
        )

    @classmethod
    def get_icon(cls):
        return cls.create_icon("help")

    def on_initialize(self):
        self._client = AgentClient(self, self._handle_approval)
        self._pending_messages: list[str] = []
        self._starting = False
        self._session_allowed: set[str] = set()
        self._mcp_config = MCPConfigFile()
        self._terminal_pending = False

        widget = self.get_widget()
        widget.sig_send_requested.connect(self._send)
        widget.sig_interrupt_requested.connect(self._client.interrupt)
        widget.sig_new_chat_requested.connect(self._new_chat)
        widget.sig_open_terminal_requested.connect(self.open_in_terminal)

        self._client.sig_event.connect(widget.handle_event)
        self._client.sig_started.connect(self._on_started)
        self._client.sig_start_failed.connect(self._on_start_failed)

    @on_plugin_available(plugin=Plugins.MCPServer)
    def on_mcp_server_available(self):
        mcp_server = self.get_plugin(Plugins.MCPServer)
        mcp_server.sig_server_started.connect(self._on_mcp_server_started)

    @on_plugin_teardown(plugin=Plugins.MCPServer)
    def on_mcp_server_teardown(self):
        mcp_server = self.get_plugin(Plugins.MCPServer)
        mcp_server.sig_server_started.disconnect(self._on_mcp_server_started)

    def on_close(self, cancelable=False):
        self._client.stop()
        self._mcp_config.remove()

    # ---- Public API
    # -------------------------------------------------------------------------
    def open_in_terminal(self) -> None:
        """Start Claude Code in a new terminal, connected to Spyder."""
        terminal = self.get_plugin(TERMINAL, error=False)
        if terminal is None:
            self.get_widget().add_notice(
                _(
                    "Install the spyder-terminal plugin to run Claude Code "
                    "in a terminal."
                ),
                error=True,
            )
            return

        mcp_server = self.get_plugin(Plugins.MCPServer)
        if mcp_server.is_server_running():
            self._launch_in_terminal()
        else:
            # Claude Code starts when the server is ready
            self._terminal_pending = True
            mcp_server.start_server()

    # ---- Private API
    # -------------------------------------------------------------------------
    def _send(self, text: str) -> None:
        if self._client.is_running and not self._starting:
            self._client.send(text)
            return

        # Start a session first and send the message once it's ready
        self._pending_messages.append(text)
        if not self._starting:
            self._start_session()

    def _start_session(self) -> None:
        self._starting = True
        self.get_widget().set_status(_("Connecting…"))

        mcp_server = self.get_plugin(Plugins.MCPServer)
        if mcp_server.is_server_running():
            self._on_mcp_server_started()
        else:
            # The session starts when the server is ready
            mcp_server.start_server()

    def _launch_in_terminal(self) -> None:
        terminal = self.get_plugin(TERMINAL)
        terminal_widget = terminal.get_widget()
        if not terminal_widget.server_is_ready():
            terminal.sig_server_is_ready.connect(self._on_terminal_ready)
            return

        mcp_server = self.get_plugin(Plugins.MCPServer)
        config_path = self._mcp_config.write(
            mcp_server.get_connection_info()
        )
        command = claude_code_command(
            self.get_conf("claude_command"), config_path
        )

        terminal_widget.create_new_term(give_focus=True)
        term = terminal_widget.get_terms()[-1]

        # Wait until the terminal has finished its own setup (which sources
        # the shell's rc files), so the command isn't typed into Claude Code
        term.terminal_ready.connect(
            lambda: QTimer.singleShot(0, lambda: term.exec_cmd(command))
        )
        terminal.switch_to_plugin()

    def _on_terminal_ready(self) -> None:
        terminal = self.get_plugin(TERMINAL)
        terminal.sig_server_is_ready.disconnect(self._on_terminal_ready)
        self._launch_in_terminal()

    def _on_mcp_server_started(self, url: str | None = None) -> None:
        if self._terminal_pending:
            self._terminal_pending = False
            self._launch_in_terminal()

        if not self._starting or self._client.is_running:
            return

        mcp_server = self.get_plugin(Plugins.MCPServer)
        working_directory = self.get_plugin(
            Plugins.WorkingDirectory, error=False
        )
        cwd = (
            working_directory.get_workdir()
            if working_directory is not None
            else getcwd_or_home()
        )
        self._client.start(
            cwd=cwd,
            mcp=mcp_server.get_connection_info(),
            model=self.get_conf("model") or None,
        )

    def _on_started(self) -> None:
        self._starting = False
        while self._pending_messages:
            self._client.send(self._pending_messages.pop(0))

    def _on_start_failed(self, message: str) -> None:
        self._starting = False
        self._pending_messages = []
        widget = self.get_widget()
        widget.set_busy(False)
        widget.add_notice(
            _("Couldn't start Claude: {}").format(message), error=True
        )

    def _new_chat(self) -> None:
        self._client.stop()
        self._pending_messages = []
        self._starting = False
        self._session_allowed = set()

    def _handle_approval(
        self, request: dict, reply: Callable[[dict], None]
    ) -> None:
        """Decide whether a tool call can run, asking the user if needed."""
        name = request["toolName"]
        if name in self._session_allowed or self._is_read_only(name):
            reply({"allow": True})
            return

        def decide(decision: dict) -> None:
            if decision["allow"] and decision["session"]:
                self._session_allowed.add(name)
            reply(
                {
                    "allow": decision["allow"],
                    "message": "" if decision["allow"] else (
                        "The user denied this action."
                    ),
                }
            )

        self.switch_to_plugin()
        self.get_widget().ask_approval(request, decide)

    def _is_read_only(self, name: str) -> bool:
        """Whether ``name`` is a read-only tool of Spyder's MCP server."""
        if not name.startswith(MCP_TOOL_PREFIX):
            return False
        mcp_server = self.get_plugin(Plugins.MCPServer)
        tool_name = name[len(MCP_TOOL_PREFIX):]
        return any(
            tool.name == tool_name and tool.read_only
            for tool in mcp_server.get_tools()
        )
