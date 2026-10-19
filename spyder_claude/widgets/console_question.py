# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Bar that asks which console a Claude Code session should run code in."""

# Standard library imports
from __future__ import annotations

# Third-party imports
from qtpy.QtCore import Signal
from qtpy.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from spyder.api.translations import _

# Local imports
from spyder_claude.sessions import ConsoleMode


class ConsoleQuestionBar(QFrame):
    """
    Non-modal bar shown above a session's terminal the first time it runs
    code. The tool call waits until the user answers, without blocking
    Spyder.
    """

    sig_answered = Signal(str, str, str)
    """
    The user chose a console.

    Parameters
    ----------
    session_id: str
        Id of the session.
    mode: str
        A :class:`ConsoleMode`, or an empty string if the user cancelled.
    console_id: str
        Id of the console for the ``Specific`` mode, or an empty string.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("claude-console-question")
        self._session_id: str | None = None
        self._selected_id = ""

        self.label = QLabel(self)
        self.label.setWordWrap(True)

        self.selected_button = QPushButton(self)
        self.selected_button.clicked.connect(
            lambda: self._answer(ConsoleMode.Specific, self._selected_id)
        )
        self.new_button = QPushButton(_("In a new console"), self)
        self.new_button.clicked.connect(
            lambda: self._answer(ConsoleMode.New)
        )
        self.cancel_button = QPushButton(_("Don't run"), self)
        self.cancel_button.clicked.connect(lambda: self._answer(""))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(self.label, stretch=1)
        for button in [
            self.selected_button,
            self.new_button,
            self.cancel_button,
        ]:
            layout.addWidget(button)

        self.hide()

    def ask(
        self,
        session_id: str,
        session_name: str,
        selected_console: dict | None,
    ) -> None:
        """
        Ask which console session ``session_id`` should use.

        Parameters
        ----------
        selected_console: dict or None
            The ``id`` and ``name`` of the console selected in Spyder, if
            there's one.
        """
        self._session_id = session_id
        self.label.setText(
            _(
                "<b>{}</b> wants to run code. Which console should it use? "
                "It will keep using it; you can change it with the Console "
                "button."
            ).format(session_name)
        )
        if selected_console is None:
            self._selected_id = ""
            self.selected_button.hide()
        else:
            self._selected_id = selected_console["id"]
            self.selected_button.setText(
                _("In {}").format(selected_console["name"])
            )
            self.selected_button.show()
        self.show()

    def get_session_id(self) -> str | None:
        """Session the bar is asking about, if it's shown."""
        return self._session_id if self.isVisible() else None

    def dismiss(self) -> None:
        self._session_id = None
        self.hide()

    def _answer(self, mode: str, console_id: str = "") -> None:
        session_id = self._session_id
        self.dismiss()
        if session_id is not None:
            self.sig_answered.emit(session_id, mode, console_id)
