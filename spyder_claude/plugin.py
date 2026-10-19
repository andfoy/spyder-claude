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
import os
import os.path as osp
import time

# Third-party imports
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QMessageBox
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
from spyder_claude.conversations import (
    find_conversation,
    last_conversation_in,
    list_conversations,
)
from spyder_claude.session_context import make_guidance
from spyder_claude.sessions import ConsoleMode, Session
from spyder_claude.terminal import (
    MCPConfigFile,
    claude_code_command,
    claude_settings,
)
from spyder_claude.widgets.conversations_dialog import ConversationsDialog
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
    CONF_VERSION = "0.4.0"
    CONF_DEFAULTS = [
        (CONF_SECTION, {
            **TERMINAL_OPTIONS,
            # Claude Code executable
            "claude_command": "claude",
            # Resume the conversations open when Spyder closed
            "restore_sessions": True,
            # Conversations open when Spyder closed, as dicts with their
            # "id" and "cwd"
            "open_sessions": [],
            # Conversations open when each project closed, by project path
            "project_sessions": {},
            # Deny shell commands that run Python, so Claude runs it in
            # Spyder's consoles
            "keep_python_in_spyder": True,
            # Commands to allow anyway, e.g. ["pytest"]
            "allowed_python_commands": [],
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

        # When each console started, to tell resumed conversations if the
        # consoles they used were restarted
        self._console_started: dict[int, float] = {}

        widget = self.get_widget()
        widget.sig_session_requested.connect(self._on_session_requested)
        widget.sig_restore_requested.connect(self.restore_sessions)
        widget.sig_conversations_requested.connect(self.show_conversations)
        widget.sig_session_closed.connect(self._on_session_closed)
        widget.sig_console_setting_changed.connect(self._on_console_setting)
        widget.sig_working_directory_requested.connect(
            self._set_working_directory
        )
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
        ipyconsole.sig_shellwidget_created.connect(self._on_console_created)
        ipyconsole.sig_shellwidget_deleted.connect(self._on_console_closed)
        for client in ipyconsole.get_clients():
            self._on_console_created(client.shellwidget)

    @on_plugin_teardown(plugin=Plugins.IPythonConsole)
    def on_ipython_console_teardown(self):
        ipyconsole = self.get_plugin(Plugins.IPythonConsole)
        ipyconsole.sig_shellwidget_created.disconnect(self._on_console_created)
        ipyconsole.sig_shellwidget_deleted.disconnect(self._on_console_closed)

    @on_plugin_available(plugin=Plugins.Projects)
    def on_projects_available(self):
        projects = self.get_plugin(Plugins.Projects)
        projects.sig_project_loaded.connect(self._on_project_loaded)
        projects.sig_project_closed.connect(self._on_project_closed)

    @on_plugin_teardown(plugin=Plugins.Projects)
    def on_projects_teardown(self):
        projects = self.get_plugin(Plugins.Projects)
        projects.sig_project_loaded.disconnect(self._on_project_loaded)
        projects.sig_project_closed.disconnect(self._on_project_closed)

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
        self.save_open_sessions()
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

    def resume_conversation(self, conversation_id: str) -> None:
        """
        Resume a Claude Code conversation, in the directory where it
        started, or show its tab if it's open.
        """
        widget = self.get_widget()
        for term in widget.get_terms():
            session = widget.get_session(term)
            if session is not None and session.id == conversation_id:
                widget.tabwidget.setCurrentWidget(term)
                self.switch_to_plugin()
                return
        self.new_session(args=["--resume", conversation_id])

    def show_conversations(self) -> None:
        """Let the user choose a conversation to resume."""
        widget = self.get_widget()
        dialog = ConversationsDialog(
            list_conversations(),
            {session.id for session in widget.get_sessions()},
            project=self._get_project_path(),
            parent=widget,
        )
        if dialog.exec_():
            conversation = dialog.get_conversation()
            if conversation is not None:
                self.resume_conversation(conversation.id)

    def restore_sessions(self) -> None:
        """
        Resume the conversations open when Spyder closed and the ones of the
        current project, or start a new session if there are none.
        """
        saved = []
        if self.get_conf("restore_sessions"):
            saved = list(self.get_conf("open_sessions"))
            project = self._get_project_path()
            if project is not None:
                saved += self._get_saved_project_sessions(project)
        if not self._resume_saved(saved):
            self.get_widget().request_session()

    def save_open_sessions(self) -> None:
        """
        Remember the open conversations to resume them next time, saving
        the ones of the open project with the project.
        """
        sessions = self.get_widget().get_sessions()
        project = self._get_project_path()
        if project is not None:
            project_sessions = self._sessions_in(project, sessions)
            self._save_project_sessions(project, project_sessions)
            sessions = [s for s in sessions if s not in project_sessions]
        self.set_conf("open_sessions", self._describe_sessions(sessions))

    # ---- Projects
    # -------------------------------------------------------------------------
    def _get_project_path(self) -> str | None:
        projects = self.get_plugin(Plugins.Projects, error=False)
        if projects is None:
            return None
        return projects.get_active_project_path()

    @staticmethod
    def _project_key(path: str) -> str:
        return osp.normcase(osp.realpath(path))

    def _sessions_in(self, folder: str, sessions=None) -> list[Session]:
        """Sessions whose conversations started in ``folder``."""
        if sessions is None:
            sessions = self.get_widget().get_sessions()
        folder = self._project_key(folder)
        return [
            session
            for session in sessions
            if session.cwd
            and (
                self._project_key(session.cwd) == folder
                or self._project_key(session.cwd).startswith(folder + os.sep)
            )
        ]

    @staticmethod
    def _describe_sessions(sessions: list[Session]) -> list[dict]:
        return [{"id": session.id, "cwd": session.cwd} for session in sessions]

    def _get_saved_project_sessions(self, project: str) -> list[dict]:
        return self.get_conf("project_sessions").get(
            self._project_key(project), []
        )

    def _save_project_sessions(
        self, project: str, sessions: list[Session]
    ) -> None:
        saved = dict(self.get_conf("project_sessions"))
        saved[self._project_key(project)] = self._describe_sessions(sessions)
        self.set_conf("project_sessions", saved)

    def _on_project_closed(self, path: str) -> None:
        """
        Close the sessions of the project that closed, remembering them to
        resume them when it's opened again.
        """
        if not isinstance(path, str) or not path:
            return
        sessions = self._sessions_in(path)
        self._save_project_sessions(path, sessions)

        # Ask before stopping sessions that are using Spyder
        mcp_server = self.get_plugin(Plugins.MCPServer)
        busy = mcp_server.get_busy_clients()
        working = [session for session in sessions if session.id in busy]
        if working and not self._confirm_close(working):
            sessions = [s for s in sessions if s not in working]

        widget = self.get_widget()
        for session in sessions:
            term = widget.get_term_for_session(session.id)
            if term is not None:
                widget.close_term(term=term)

    def _confirm_close(self, sessions: list[Session]) -> bool:
        names = ", ".join(session.name for session in sessions)
        answer = QMessageBox.question(
            self.get_widget(),
            _("Claude Code"),
            _(
                "{} of the project being closed is running code or another "
                "task in Spyder. Stop it and close its session?<br><br>"
                "It can be resumed when you open the project again."
            ).format(names),
            QMessageBox.Yes | QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _on_project_loaded(self, _path: str) -> None:
        # After the consoles restart, which happens right after this signal
        QTimer.singleShot(0, self._restore_project_sessions)

    def _restore_project_sessions(self) -> None:
        """Resume the conversations open when the project was closed."""
        project = self._get_project_path()
        widget = self.get_widget()
        if project is None or not widget.sessions_restored():
            # The pane restores them the first time it's shown
            return
        if self.get_conf("restore_sessions"):
            self._resume_saved(self._get_saved_project_sessions(project))

    def _resume_saved(self, saved: list[dict]) -> bool:
        """
        Resume the saved conversations that can be resumed and aren't open.
        Returns whether any was resumed.
        """
        widget = self.get_widget()
        open_ids = {session.id for session in widget.get_sessions()}
        resumed = False
        for item in saved:
            conversation_id = item.get("id", "")
            if conversation_id in open_ids:
                continue
            conversation = find_conversation(conversation_id)
            # Sessions closed before using them have nothing to resume
            if conversation is not None and conversation.can_resume():
                open_ids.add(conversation_id)
                widget.request_session(args=["--resume", conversation_id])
                resumed = True
        return resumed

    def _resume_note(self, conversation) -> str:
        """
        Instructions for a resumed conversation about the state of Spyder's
        consoles, which it can't see from its history.
        """
        # When the consoles open now started (closed ones are only
        # forgotten later, once their kernels shut down)
        ipyconsole = self.get_plugin(Plugins.IPythonConsole, error=False)
        clients = ipyconsole.get_clients() if ipyconsole is not None else []
        started = [
            self._console_started.get(id(client.shellwidget), time.time())
            for client in clients
        ]
        if started and conversation.updated.timestamp() < min(started):
            return (
                "This conversation was resumed in Spyder. All of Spyder's "
                "IPython consoles were started after it last ran (e.g. "
                "because its project was opened again), so the variables, "
                "imports and figures it created there are gone. Check with "
                "list_consoles and list_variables, and re-run the code you "
                "need, before relying on earlier results."
            )
        return (
            "This conversation was resumed in Spyder. Its IPython consoles "
            "may have changed since it last ran; check with list_consoles "
            "and list_variables before relying on earlier results."
        )

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
        args = list(args)
        path = path or widget.get_default_path()

        # Continue the last conversation of the directory by its id, so the
        # session knows which conversation it is
        if "--continue" in args:
            last = last_conversation_in(path)
            if last is not None:
                args.remove("--continue")
                args += ["--resume", last.id]

        resume_note = ""
        if "--resume" in args:
            # Conversations can only be resumed where they started
            conversation_id = args[args.index("--resume") + 1]
            conversation = find_conversation(conversation_id)
            session = Session(widget.new_session_name(), id=conversation_id)
            if conversation is not None:
                path = conversation.cwd if conversation.can_resume() else path
                session.title = conversation.title
                resume_note = self._resume_note(conversation)
        else:
            session = Session(widget.new_session_name())
            args = ["--session-id", session.id] + args
        session.cwd = path
        self._sessions[session.id] = session

        # Each session identifies itself, so tools can tell sessions apart
        mcp_server = self.get_plugin(Plugins.MCPServer)
        session.config_path = self._mcp_config.write(
            session.id,
            mcp_server.get_connection_info(session.id, session.name),
        )

        # Hooks that tell Claude how to work in Spyder (also when resuming a
        # conversation, unlike --append-system-prompt) and keep its Python in
        # Spyder's consoles
        keep_python = self.get_conf("keep_python_in_spyder")
        context_path = self._mcp_config.write_json(
            session.id,
            "context",
            {
                "guidance": make_guidance(keep_python),
                "resume_note": resume_note,
            },
        )
        settings_path = self._mcp_config.write_json(
            session.id,
            "settings",
            claude_settings(
                context_path,
                keep_python,
                self.get_conf("allowed_python_commands"),
            ),
        )
        command = claude_code_command(
            self.get_conf("claude_command"),
            session.config_path,
            args,
            settings_path=settings_path,
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
        # It starts in the session's folder, so relative paths in its code
        # are resolved like in Claude's shell
        wdir = session.cwd if session.cwd and osp.isdir(session.cwd) else None
        with calling_as(caller):
            future = ipyconsole.new_console(name=session.name, wdir=wdir)
        future.add_done_callback(on_ready)
        return result

    def _set_working_directory(self, path: str) -> None:
        workingdirectory = self.get_plugin(
            Plugins.WorkingDirectory, error=False
        )
        if workingdirectory is not None and osp.isdir(path):
            workingdirectory.chdir(path, sender_plugin=self.NAME)

    def _list_consoles(self) -> list:
        ipyconsole = self.get_plugin(Plugins.IPythonConsole, error=False)
        if ipyconsole is None:
            return []
        return ipyconsole.list_consoles()

    def _on_console_created(self, shellwidget) -> None:
        self._console_started[id(shellwidget)] = time.time()

    def _on_console_closed(self, shellwidget) -> None:
        self._console_started.pop(id(shellwidget), None)
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
