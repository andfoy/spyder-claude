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

from concurrent.futures import Future
import logging

# Third-party imports
from qtpy.QtCore import QTimer
from spyder.api.plugin_registration.decorators import (
    on_plugin_available,
    on_plugin_teardown,
)
from spyder.api.plugins import ExposedCallError, Plugins
from spyder.api.plugins.exposed import calling_as
from spyder.api.translations import _
from spyder_terminal.config import CONF_DEFAULTS as TERMINAL_CONF_DEFAULTS
from spyder_terminal.terminalplugin import TerminalPlugin

# Local imports
from spyder_claude.sessions import ConsoleMode, Session
from spyder_claude.terminal import MCPConfigFile, claude_code_command
from spyder_claude.widgets.main_widget import ClaudeCodeWidget

logger = logging.getLogger(__name__)

PLUGIN_NAME = "claude_code"

# Terminal options (shell, cursor, buffer, etc.) used by the inherited
# terminal widgets, which read them from this plugin's section
TERMINAL_OPTIONS = dict(TERMINAL_CONF_DEFAULTS[0][1])

# Terminal shortcuts (copy, paste, zoom, etc.). The inherited widgets look
# them up with this plugin's name as context, so they're defined again here
# with the same keys.
SHORTCUTS = {
    f"{PLUGIN_NAME}/{option.split('/', 1)[1]}": keys
    for option, keys in dict(TERMINAL_CONF_DEFAULTS)["shortcuts"].items()
}


class ClaudeCode(TerminalPlugin):
    """Claude Code plugin."""

    NAME = PLUGIN_NAME
    REQUIRES = TerminalPlugin.REQUIRES + [Plugins.MCPServer]
    OPTIONAL = [
        Plugins.Editor,
        Plugins.IPythonConsole,
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
        ("shortcuts", SHORTCUTS),
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
        self._sessions: dict[str, Session] = {}

        widget = self.get_widget()
        widget.sig_session_requested.connect(self._on_session_requested)
        widget.sig_session_closed.connect(self._on_session_closed)
        widget.sig_console_setting_changed.connect(self._on_console_setting)
        widget.set_console_lister(self._list_consoles)

    @on_plugin_available(plugin=Plugins.MCPServer)
    def on_mcp_server_available(self):
        mcp_server = self.get_plugin(Plugins.MCPServer)
        mcp_server.sig_server_started.connect(self._on_mcp_server_started)
        mcp_server.sig_server_stopped.connect(self._on_mcp_server_stopped)
        mcp_server.register_argument_asker("console_id", self._ask_console)

    @on_plugin_teardown(plugin=Plugins.MCPServer)
    def on_mcp_server_teardown(self):
        mcp_server = self.get_plugin(Plugins.MCPServer)
        mcp_server.sig_server_started.disconnect(self._on_mcp_server_started)
        mcp_server.sig_server_stopped.disconnect(self._on_mcp_server_stopped)
        mcp_server.register_argument_asker("console_id", None)

    @on_plugin_available(plugin=Plugins.IPythonConsole)
    def on_ipython_console_available(self):
        ipyconsole = self.get_plugin(Plugins.IPythonConsole)
        ipyconsole.sig_shellwidget_deleted.connect(self._on_console_closed)

    @on_plugin_teardown(plugin=Plugins.IPythonConsole)
    def on_ipython_console_teardown(self):
        ipyconsole = self.get_plugin(Plugins.IPythonConsole)
        ipyconsole.sig_shellwidget_deleted.disconnect(self._on_console_closed)

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
        widget = self.get_widget()
        session = Session(widget.new_session_name())
        self._sessions[session.id] = session

        # Each session identifies itself, so tools can tell sessions apart
        mcp_server = self.get_plugin(Plugins.MCPServer)
        session.config_path = self._mcp_config.write(
            session.id,
            mcp_server.get_connection_info(session.id, session.name),
        )
        command = claude_code_command(
            self.get_conf("claude_command"), session.config_path, args
        )
        widget.start_session(command, session, path or None)

    def _on_session_closed(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        self._mcp_config.remove(session_id)
        if session.is_asking():
            session.question.set_exception(
                ExposedCallError("The session was closed.")
            )
        mcp_server = self.get_plugin(Plugins.MCPServer, error=False)
        if mcp_server is not None:
            mcp_server.set_client_argument(session_id, "console_id", None)

    # ---- Consoles of sessions
    # -------------------------------------------------------------------------
    def set_session_console(
        self, session_id: str, mode: str, console_id: str | None = None
    ) -> None:
        """
        Set the console a session runs code in.

        Parameters
        ----------
        session_id: str
            Id of the session.
        mode: str
            A :class:`spyder_claude.sessions.ConsoleMode`.
        console_id: str, optional
            Id of the console, for the ``Specific`` mode.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return

        session.console_mode = mode
        session.console_id = (
            console_id if mode == ConsoleMode.Specific else None
        )

        # The console Claude uses when it doesn't pass one. Without it, the
        # asker decides (see _ask_console).
        mcp_server = self.get_plugin(Plugins.MCPServer)
        mcp_server.set_client_argument(
            session_id, "console_id", session.console_id
        )
        self.get_widget().update_session_state()

    def _on_console_setting(
        self, session_id: str, mode: str, console_id: str
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return

        if session.is_asking():
            # The user answered the question, or chose a setting instead
            question = session.question
            if mode:
                self.set_session_console(session_id, mode, console_id or None)
                question.set_result((mode, console_id or None))
            else:
                question.set_exception(
                    ExposedCallError(
                        "The user chose not to run the code. Ask them how "
                        "to proceed."
                    )
                )
            self.get_widget().update_session_state()
        elif mode:
            self.set_session_console(session_id, mode, console_id or None)

    def _ask_console(self, caller, tool):
        """
        Choose the console for a tool call of a session that has no console
        set (see :class:`spyder_claude.sessions.ConsoleMode`).
        """
        session = self._sessions.get(caller.id) if caller else None
        if session is None or session.console_mode == ConsoleMode.Selected:
            # Other agents use the selected console
            return None

        if session.console_mode == ConsoleMode.New:
            if session.console_opening is None:
                session.console_opening = self._open_session_console(session)
            return session.console_opening

        if session.console_mode == ConsoleMode.Specific:
            return session.console_id

        # Ask the user once, even if Claude makes several calls meanwhile
        if not session.is_asking():
            session.question = Future()
            self.get_widget().ask_console(session)

        result = Future()

        def on_answer(question):
            try:
                question.result()
            except Exception as error:
                result.set_exception(error)
                return
            # The answer is now the session's setting
            self._chain(self._ask_console(caller, tool), result)

        session.question.add_done_callback(on_answer)
        return result

    def _open_session_console(self, session: Session) -> Future:
        """Open a console for ``session`` and make it its console."""
        ipyconsole = self.get_plugin(Plugins.IPythonConsole, error=False)
        if ipyconsole is None:
            raise ExposedCallError("Spyder's IPython console isn't available.")

        result = Future()

        def on_ready(future):
            session.console_opening = None
            try:
                console = future.result()
            except Exception as error:
                result.set_exception(error)
                return
            if session.id in self._sessions:
                session.console_id = console["id"]
                self.get_plugin(Plugins.MCPServer).set_client_argument(
                    session.id, "console_id", console["id"]
                )
                self.get_widget().update_session_state()
            result.set_result(console["id"])

        # Opened on behalf of the session, so Spyder shows it as its creator
        mcp_server = self.get_plugin(Plugins.MCPServer)
        caller = next(
            (c for c in mcp_server.get_clients() if c.id == session.id), None
        )
        with calling_as(caller):
            future = ipyconsole.new_console(name=session.name)
        future.add_done_callback(on_ready)
        return result

    def _list_consoles(self) -> list:
        ipyconsole = self.get_plugin(Plugins.IPythonConsole, error=False)
        if ipyconsole is None:
            return []
        return ipyconsole.list_consoles()

    def _on_console_closed(self, shellwidget) -> None:
        # Wait until the console is removed from the list
        QTimer.singleShot(0, self._forget_closed_consoles)

    def _forget_closed_consoles(self) -> None:
        """
        Sessions whose console closed ask again, instead of silently using
        another console.
        """
        open_ids = {console["id"] for console in self._list_consoles()}
        for session in list(self._sessions.values()):
            if (
                session.console_id is not None
                and session.console_id not in open_ids
            ):
                self.set_session_console(session.id, ConsoleMode.Ask)

    @staticmethod
    def _chain(value, result: Future) -> None:
        """Resolve ``result`` with ``value``, or with a future's result."""
        if not isinstance(value, Future):
            result.set_result(value)
            return

        def done(future):
            try:
                result.set_result(future.result())
            except Exception as error:
                result.set_exception(error)

        value.add_done_callback(done)
