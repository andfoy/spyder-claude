# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Claude Code pane main widget."""

# Standard library imports
from __future__ import annotations

# Third-party imports
from qtpy.QtCore import QTimer, Signal
from spyder.api.translations import _
from spyder_terminal.api import TerminalMainWidgetActions
from spyder_terminal.widgets.main_widget import (
    TerminalMainWidget,
    TerminalMainWidgetMenuSections,
)


class ClaudeCodeWidgetActions:
    ResumeSession = "resume_claude_code_session_action"


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

    def __init__(self, name, plugin, parent):
        self._sessions_started = 0
        self._session_requested = False
        self._closing_term = False
        super().__init__(name, plugin, parent)

    # ---- PluginMainWidget API
    # -------------------------------------------------------------------------
    def get_title(self):
        return _("Claude Code")

    def setup(self):
        super().setup()

        # Remote servers can't reach Spyder's MCP server yet
        self.remote_menu.menuAction().setVisible(False)

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
        self.add_item_to_menu(
            resume_action,
            menu=self.get_options_menu(),
            section=TerminalMainWidgetMenuSections.New,
        )

    def change_visibility(self, enable, force_focus=None):
        super().change_visibility(enable, force_focus)

        # Start a session the first time the pane is shown
        if (
            enable
            and self.server_ready
            and not self.get_terms()
            and not self._session_requested
        ):
            self.request_session()

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
        self._closing_term = True
        try:
            super().close_term(index=index, term=term)
        finally:
            self._closing_term = False

    def add_tab(self, widget):
        super().add_tab(widget)
        self._sessions_started += 1
        index = self.tabwidget.indexOf(widget)
        title = _("Claude {}").format(self._sessions_started)
        self.tabwidget.setTabText(index, title)
        self.tabwidget.setTabToolTip(index, title)

    # ---- Public API
    # -------------------------------------------------------------------------
    def request_session(
        self, path: str | None = None, args: list[str] | None = None
    ) -> None:
        """Ask the plugin to start a new Claude Code session."""
        self._session_requested = True
        self.sig_session_requested.emit(path or "", args or [])

    def start_session(self, command: str, path: str | None = None) -> None:
        """Open a new terminal and run ``command`` (Claude Code) in it."""
        self._session_requested = False
        super().create_new_term(path=path or None)
        term = self.get_terms()[-1]

        # Wait until the terminal has finished its own setup (which sources
        # the shell's rc files), so that isn't typed into Claude Code
        term.terminal_ready.connect(
            lambda: QTimer.singleShot(0, lambda: term.exec_cmd(command))
        )
