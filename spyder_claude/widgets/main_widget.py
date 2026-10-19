# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Claude Code pane main widget."""

# Standard library imports
from __future__ import annotations

from collections.abc import Callable

# Third-party imports
from qtpy.QtCore import Qt, QTimer, Signal
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import QMenu, QToolButton
from spyder.api.translations import _
from spyder_terminal.api import TerminalMainWidgetActions
from spyder_terminal.widgets.main_widget import (
    TerminalMainWidget,
    TerminalMainWidgetMenuSections,
)

# Local imports
from spyder_claude.sessions import ConsoleMode, Session
from spyder_claude.widgets.console_question import ConsoleQuestionBar


class ClaudeCodeWidgetActions:
    ResumeSession = "resume_claude_code_session_action"
    ResumeConversation = "resume_claude_code_conversation_action"
    SetWorkingDirectory = "set_working_directory_to_session_folder_action"
    KeepPythonInSpyder = "keep_python_in_spyder_consoles_action"


CONSOLE_MODE_TEXTS = {
    ConsoleMode.Ask: _("Ask on first run"),
    ConsoleMode.Selected: _("Selected console"),
    ConsoleMode.New: _("New console for this session"),
}


class ClaudeCodeWidget(TerminalMainWidget):
    """
    Terminals that run Claude Code.

    It's spyder-terminal's main widget, so it has the same tabs, themes,
    fonts and shortcuts, but each tab runs a Claude Code session instead of a
    shell.
    """

    sig_session_requested = Signal(str, list)
    """
    This signal is emitted to start a new Claude Code session.

    The plugin prepares the connection to Spyder's MCP server and then calls
    :meth:`start_session`.

    Parameters
    ----------
    path: str
        Directory to start the session in, or an empty string for the
        default one.
    args: list
        Extra arguments for Claude Code.
    """

    sig_console_setting_changed = Signal(str, str, str)
    """
    The user changed the console a session uses, or answered which one to
    use (an empty mode means they cancelled the run).

    Parameters
    ----------
    session_id: str
        Id of the session.
    mode: str
        A :class:`spyder_claude.sessions.ConsoleMode`.
    console_id: str
        Id of the console for the ``Specific`` mode, or an empty string.
    """

    sig_conversations_requested = Signal()
    """The user wants to choose a conversation to resume."""

    sig_restore_requested = Signal()
    """
    The pane is shown for the first time, so the sessions open when Spyder
    closed can be restored (or a new one started).
    """

    sig_working_directory_requested = Signal(str)
    """
    The user wants Spyder's working directory to be a session's folder.

    Parameters
    ----------
    path: str
        Folder of the session.
    """

    sig_session_closed = Signal(str)
    """
    The tab of a session was closed.

    Parameters
    ----------
    session_id: str
        Id of the session.
    """

    def __init__(self, name, plugin, parent):
        self._sessions_started = 0
        self._session_requested = False
        self._closing_term = False
        self._pending_session: Session | None = None
        self._restored = False
        self._list_consoles: Callable[[], list] = list
        super().__init__(name, plugin, parent)

    # ---- PluginMainWidget API
    # -------------------------------------------------------------------------
    def get_title(self):
        return _("Claude Code")

    def setup(self):
        super().setup()

        # Remote servers can't reach Spyder's MCP server yet
        self.remote_menu.menuAction().setVisible(False)

        # Use this pane's own shortcuts instead of the terminal ones, which
        # aren't in this plugin's config
        for action in self.get_actions().values():
            if action.shortcut_context == "terminal":
                action.shortcut_context = self.CONF_SECTION

        # Hidden terminals can't measure their size, so do it when shown
        self.tabwidget.currentChanged.connect(lambda _: self.refresh_layout())

        # Console used by the current session
        self.question_bar = ConsoleQuestionBar(self)
        self.question_bar.sig_answered.connect(
            self.sig_console_setting_changed
        )
        self.layout().insertWidget(0, self.question_bar)

        self.console_menu = QMenu(self)
        self.console_menu.aboutToShow.connect(self._populate_console_menu)
        self.console_button = QToolButton(self)
        self.console_button.name = "claude_code_console_button"
        self.console_button.setIcon(self.create_icon("ipython_console"))
        self.console_button.setToolButtonStyle(
            Qt.ToolButtonTextBesideIcon
        )
        self.console_button.setPopupMode(QToolButton.InstantPopup)
        self.console_button.setMenu(self.console_menu)
        self.console_button.setToolTip(
            _("Console where this session runs code")
        )
        self.add_corner_widget(self.console_button)
        self.tabwidget.currentChanged.connect(
            lambda _: self.update_session_state()
        )

        texts = {
            TerminalMainWidgetActions.NewTerminalForCWD: _(
                "New session in current working directory"
            ),
            TerminalMainWidgetActions.NewTerminalForProject: _(
                "New session in current project"
            ),
            TerminalMainWidgetActions.NewTerminalForFile: _(
                "New session in current Editor file directory"
            ),
            TerminalMainWidgetActions.RenameTab: _("Rename session"),
        }
        for action_id, text in texts.items():
            self.get_action(action_id).setText(text)

        resume_action = self.create_action(
            ClaudeCodeWidgetActions.ResumeSession,
            text=_("Resume last conversation"),
            tip=_(
                "Start a session that continues the most recent conversation "
                "in the current working directory"
            ),
            triggered=lambda: self.request_session(args=["--continue"]),
        )
        resume_conversation_action = self.create_action(
            ClaudeCodeWidgetActions.ResumeConversation,
            text=_("Resume conversation..."),
            tip=_(
                "Choose a previous conversation to resume, in the folder "
                "where it started"
            ),
            triggered=lambda: self.sig_conversations_requested.emit(),
        )
        for action in [resume_action, resume_conversation_action]:
            self.add_item_to_menu(
                action,
                menu=self.get_options_menu(),
                section=TerminalMainWidgetMenuSections.New,
            )

        self.set_workdir_action = self.create_action(
            ClaudeCodeWidgetActions.SetWorkingDirectory,
            text=_("Set working directory to this session's folder"),
            tip=_(
                "Change Spyder's working directory to the folder where the "
                "current session started"
            ),
            triggered=self._request_working_directory,
        )
        keep_python_action = self.create_action(
            ClaudeCodeWidgetActions.KeepPythonInSpyder,
            text=_("Run Python only in Spyder's consoles"),
            tip=_(
                "Don't let Claude run Python from its shell, so the code it "
                "runs and its results are in Spyder's consoles. Applies to "
                "new sessions."
            ),
            toggled=True,
            option="keep_python_in_spyder",
        )
        for action in [self.set_workdir_action, keep_python_action]:
            self.add_item_to_menu(action, menu=self.get_options_menu())

        self.update_session_state()

    def change_visibility(self, enable, force_focus=None):
        super().change_visibility(enable, force_focus)
        if enable:
            self.refresh_layout()

        # Restore the sessions open when Spyder closed (or start one) the
        # first time the pane is shown, and start one if it's shown again
        # without sessions
        if (
            enable
            and self.server_ready
            and not self.get_terms()
            and not self._session_requested
        ):
            if self._restored:
                self.request_session()
            else:
                self._restored = True
                self.sig_restore_requested.emit()

    # ---- TerminalMainWidget API
    # -------------------------------------------------------------------------
    def create_new_term(
        self, name=None, give_focus=True, path=None, config_id=None
    ):
        """Start a Claude Code session instead of a shell."""
        # The terminal server creates a first terminal when it starts.
        # Sessions only start when the pane is shown, so Claude Code doesn't
        # run (or ask to trust the folder) if it isn't used.
        if not give_focus and not self.is_visible:
            return

        # Closing the last terminal opens a new one, but a session that
        # ended (e.g. with /exit) shouldn't start another one
        if self._closing_term:
            return

        self.request_session(path=path)

    def close_term(self, index=None, term=None):
        if term is None:
            term = self.tabwidget.widget(
                self.tabwidget.currentIndex() if index is None else index
            )
        session = self.get_session(term)

        self._closing_term = True
        try:
            super().close_term(index=index, term=term)
        finally:
            self._closing_term = False

        if session is not None:
            self.sig_session_closed.emit(session.id)
        self.update_session_state()

    def add_tab(self, widget):
        super().add_tab(widget)
        session, self._pending_session = self._pending_session, None
        widget.claude_session = session
        if session is not None:
            index = self.tabwidget.indexOf(widget)
            self.tabwidget.setTabText(index, session.name)
        self.update_session_state()

    # ---- Public API
    # -------------------------------------------------------------------------
    def request_session(
        self, path: str | None = None, args: list[str] | None = None
    ) -> None:
        """Ask the plugin to start a new Claude Code session."""
        self._session_requested = True

        # Once the pane is in use, sessions are restored when needed (e.g.
        # when a project opens) instead of the first time it's shown
        self._restored = True
        self.sig_session_requested.emit(path or "", args or [])

    def get_default_path(self) -> str:
        """Directory where sessions start by default."""
        if self.project_path is not None:
            return self.project_path
        return self.current_cwd

    def new_session_name(self) -> str:
        """Name for a new session, e.g. ``Claude 2``."""
        self._sessions_started += 1
        return _("Claude {}").format(self._sessions_started)

    def start_session(
        self, command: str, session: Session, path: str | None = None
    ) -> None:
        """Open a new terminal and run ``command`` (Claude Code) in it."""
        self._session_requested = False
        self._pending_session = session
        super().create_new_term(path=path or None)
        term = self.get_terms()[-1]

        def run_command():
            # The terminal applies its font after computing its size, so fit
            # it again before Claude Code starts and draws its interface
            self.refresh_layout(term)
            term.exec_cmd(command)

        # Wait until the terminal has finished its own setup (which sources
        # the shell's rc files), so that isn't typed into Claude Code
        term.terminal_ready.connect(lambda: QTimer.singleShot(0, run_command))

    def refresh_layout(self, term=None) -> None:
        """
        Measure the terminal's font again and fit the terminal to its size,
        which resizes the pseudo-terminal so Claude Code redraws.
        """
        if term is None:
            term = self.get_current_term()
        if term is None or not getattr(term, "view_ready", False):
            return
        if not term.isVisible():
            return
        term.set_font(term.font)
        term.view.document.runJavaScript(
            "window.dispatchEvent(new Event('resize'))"
        )

    # ---- Sessions and their consoles
    # -------------------------------------------------------------------------
    def set_console_lister(self, list_consoles: Callable[[], list]) -> None:
        """
        Set the function that lists the open consoles, as dicts with their
        ``id``, ``name`` and whether they're the ``current`` one.
        """
        self._list_consoles = list_consoles

    def get_session(self, term=None) -> Session | None:
        """Session of a terminal, by default the current one."""
        if term is None:
            term = self.get_current_term()
        return getattr(term, "claude_session", None)

    def get_term_for_session(self, session_id: str):
        """Terminal of a session, if it's open."""
        for term in self.get_terms():
            session = self.get_session(term)
            if session is not None and session.id == session_id:
                return term
        return None

    def sessions_restored(self) -> bool:
        """
        Whether the pane is in use: it restored the sessions open when
        Spyder closed (the first time it was shown) or started one.
        """
        return self._restored

    def get_sessions(self) -> list[Session]:
        return [
            session
            for session in map(self.get_session, self.get_terms())
            if session is not None
        ]

    def ask_console(self, session: Session) -> None:
        """
        Ask the user which console ``session`` should use, in the bar above
        its terminal. If it's not the current tab, its tab is marked until
        the user goes there.
        """
        self.update_session_state()

    def update_session_state(self) -> None:
        """
        Show the console of the current session, its pending question, and
        mark the other tabs whose sessions are asking.
        """
        for term in self.get_terms():
            session = self.get_session(term)
            index = self.tabwidget.indexOf(term)
            asking = session is not None and session.is_asking()
            self.tabwidget.setTabIcon(
                index, self.create_icon("help") if asking else QIcon()
            )
            if session is not None:
                tooltip = [session.name]
                if asking:
                    tooltip.append(
                        _("Waiting for you to choose a console in its "
                          "terminal")
                        if session.asking_in_client
                        else _("Waiting for you to choose a console")
                    )
                if session.title:
                    tooltip.append(session.title)
                if session.cwd:
                    tooltip.append(session.cwd)
                self.tabwidget.setTabToolTip(index, "\n".join(tooltip))

        session = self.get_session()
        self.console_button.setEnabled(session is not None)
        self.set_workdir_action.setEnabled(
            session is not None and bool(session.cwd)
        )
        self.console_button.setText(
            self._describe_console(session) if session else _("Console")
        )

        # Questions asked in Claude Code are answered there
        if (
            session is not None
            and session.is_asking()
            and not session.asking_in_client
        ):
            if self.question_bar.get_session_id() != session.id:
                selected = [c for c in self._list_consoles() if c["current"]]
                self.question_bar.ask(
                    session.id, session.name, selected[0] if selected else None
                )
        else:
            self.question_bar.dismiss()

    def _request_working_directory(self) -> None:
        session = self.get_session()
        if session is not None and session.cwd:
            self.sig_working_directory_requested.emit(session.cwd)

    def _describe_console(self, session: Session) -> str:
        if session.console_id is not None:
            for console in self._list_consoles():
                if console["id"] == session.console_id:
                    return console["name"]
            return session.console_id
        return CONSOLE_MODE_TEXTS.get(session.console_mode, "")

    def _populate_console_menu(self) -> None:
        self.console_menu.clear()
        session = self.get_session()
        if session is None:
            return

        def add(text, mode, console_id=""):
            action = self.console_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(
                session.console_mode == mode
                and (
                    mode != ConsoleMode.Specific
                    or session.console_id == console_id
                )
            )
            action.triggered.connect(
                lambda: self.sig_console_setting_changed.emit(
                    session.id, mode, console_id
                )
            )

        for mode in [ConsoleMode.Ask, ConsoleMode.Selected, ConsoleMode.New]:
            add(CONSOLE_MODE_TEXTS[mode], mode)

        consoles = self._list_consoles()
        if consoles:
            self.console_menu.addSection(_("Consoles"))
        for console in consoles:
            add(console["name"], ConsoleMode.Specific, console["id"])
