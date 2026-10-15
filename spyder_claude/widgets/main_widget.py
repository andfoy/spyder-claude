# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Claude pane main widget."""

# Standard library imports
from __future__ import annotations

from collections.abc import Callable

# Third-party imports
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)
from spyder.api.translations import _
from spyder.api.widgets.main_widget import PluginMainWidget
from spyder.utils.palette import SpyderPalette

# Local imports
from spyder_claude.protocol import EventTypes
from spyder_claude.widgets.chat import (
    AssistantMessageWidget,
    ChatView,
    NoticeWidget,
    ToolCallWidget,
    UserMessageWidget,
)

# Tools used internally by the agent, which aren't worth showing.
HIDDEN_TOOLS = {"ToolSearch"}


class ClaudeWidgetActions:
    NewChat = "new_chat_action"
    OpenTerminal = "open_terminal_action"


class ChatInput(QPlainTextEdit):
    """Message box: Enter sends, Shift+Enter adds a new line."""

    sig_submitted = Signal()

    def keyPressEvent(self, event):
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not event.modifiers() & Qt.ShiftModifier
        ):
            self.sig_submitted.emit()
            return
        super().keyPressEvent(event)


class ClaudeWidget(PluginMainWidget):
    """Chat with Claude."""

    ENABLE_SPINNER = True

    sig_send_requested = Signal(str)
    """
    This signal is emitted when the user sends a message.

    Parameters
    ----------
    text: str
        Message text.
    """

    sig_interrupt_requested = Signal()
    """This signal is emitted to stop the current turn."""

    sig_new_chat_requested = Signal()
    """This signal is emitted to start a new conversation."""

    sig_open_terminal_requested = Signal()
    """This signal is emitted to run Claude Code in a terminal."""

    def __init__(self, name=None, plugin=None, parent=None):
        super().__init__(name, plugin, parent)
        self._busy = False
        self._tool_calls: dict[str, ToolCallWidget] = {}
        self._current_text: AssistantMessageWidget | None = None

        self.chat_view = ChatView(self)

        self.input = ChatInput(self)
        self.input.setPlaceholderText(
            _("Ask Claude about your code, data or plots…")
        )
        self.input.setFixedHeight(
            self.input.fontMetrics().lineSpacing() * 4 + 12
        )
        self.input.sig_submitted.connect(self._on_send_clicked)

        self.send_button = QPushButton(_("Send"))
        self.send_button.clicked.connect(self._on_send_clicked)

        self.status_label = QLabel(_("Not connected"))
        self.status_label.setStyleSheet(
            f"color: {SpyderPalette.COLOR_TEXT_2};"
        )

        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label)
        bottom.addStretch()
        bottom.addWidget(self.send_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.chat_view, stretch=1)
        layout.addWidget(self.input)
        layout.addLayout(bottom)
        self.setLayout(layout)

    # ---- PluginMainWidget API
    # -------------------------------------------------------------------------
    def get_title(self):
        return _("Claude")

    def get_focus_widget(self):
        return self.input

    def setup(self):
        self.new_chat_action = self.create_action(
            ClaudeWidgetActions.NewChat,
            text=_("New chat"),
            icon=self.create_icon("filenew"),
            triggered=self._on_new_chat,
        )
        self.add_item_to_toolbar(
            self.new_chat_action, self.get_main_toolbar()
        )

        self.open_terminal_action = self.create_action(
            ClaudeWidgetActions.OpenTerminal,
            text=_("Open Claude Code in a terminal"),
            tip=_(
                "Run the Claude Code CLI in Spyder's terminal, connected to "
                "this Spyder session"
            ),
            icon=self.create_icon("DollarFileIcon"),
            triggered=self.sig_open_terminal_requested.emit,
        )
        self.add_item_to_menu(
            self.open_terminal_action, self.get_options_menu()
        )

    def update_actions(self):
        pass

    # ---- Public API
    # -------------------------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_button.setText(_("Stop") if busy else _("Send"))
        if busy:
            self.start_spinner()
        else:
            self.stop_spinner()

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def add_notice(self, text: str, error: bool = False) -> None:
        self._current_text = None
        self.chat_view.add_item(NoticeWidget(text, error))

    def clear(self) -> None:
        self.chat_view.clear()
        self._tool_calls = {}
        self._current_text = None
        self.set_busy(False)
        self.set_status(_("Not connected"))

    def handle_event(self, event: dict) -> None:
        """Show an event sent by the agent."""
        kind = event["type"]

        if kind == EventTypes.TextDelta:
            if self._current_text is None:
                self._current_text = self.chat_view.add_item(
                    AssistantMessageWidget()
                )
            self._current_text.append_text(event["text"])

        elif kind == EventTypes.Text:
            # The complete block replaces the streamed deltas
            if self._current_text is None:
                self._current_text = self.chat_view.add_item(
                    AssistantMessageWidget()
                )
            self._current_text.set_full_text(event["text"])
            self._current_text = None

        elif kind == EventTypes.ToolUse:
            self._current_text = None
            if event["name"] in HIDDEN_TOOLS:
                return
            self._add_tool_call(event["id"], event["name"], event["input"])

        elif kind == EventTypes.ToolResult:
            tool_call = self._tool_calls.get(event["id"])
            if tool_call is not None:
                tool_call.set_result(event["content"], event["isError"])

        elif kind == EventTypes.Init:
            self.set_status(_("Connected · {}").format(event.get("model")))

        elif kind == EventTypes.Result:
            self.set_busy(False)
            self._current_text = None
            seconds = (event.get("durationMs") or 0) / 1000
            self.set_status(_("Done in {:.1f} s").format(seconds))
            if event.get("terminalReason", "").startswith("aborted"):
                self.add_notice(_("Interrupted."))
            elif event.get("isError"):
                errors = "\n".join(event.get("errors") or []) or event.get(
                    "subtype", ""
                )
                self.add_notice(_("Error: {}").format(errors), error=True)

        elif kind == EventTypes.Error:
            self.add_notice(event.get("message", ""), error=True)

        elif kind == EventTypes.Stopped:
            self.set_busy(False)
            self.set_status(_("Not connected"))

    def ask_approval(
        self, request: dict, decide: Callable[[dict], None]
    ) -> None:
        """Show approval buttons for a tool call."""
        tool_call = self._tool_calls.get(request.get("toolUseId"))
        if tool_call is None:
            tool_call = self._add_tool_call(
                request.get("toolUseId"),
                request["toolName"],
                request.get("input") or {},
            )
        tool_call.sig_decided.connect(decide)
        tool_call.ask_approval()

    # ---- Private API
    # -------------------------------------------------------------------------
    def _add_tool_call(
        self, tool_id: str, name: str, tool_input: dict
    ) -> ToolCallWidget:
        tool_call = self.chat_view.add_item(
            ToolCallWidget(tool_id, name, tool_input)
        )
        self._tool_calls[tool_id] = tool_call
        return tool_call

    def _on_send_clicked(self) -> None:
        if self._busy:
            self.sig_interrupt_requested.emit()
            return

        text = self.input.toPlainText().strip()
        if not text:
            return

        self.input.clear()
        self._current_text = None
        self.chat_view.add_item(UserMessageWidget(text))
        self.set_busy(True)
        self.sig_send_requested.emit(text)

    def _on_new_chat(self) -> None:
        self.clear()
        self.sig_new_chat_requested.emit()
