# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Tests for the Claude pane widgets."""

# Standard library imports
import base64

# Third-party imports
import pytest
from qtpy.QtCore import QBuffer, QByteArray, QIODevice, Qt
from qtpy.QtGui import QPixmap, QTextCursor
from spyder.api.translations import _

# Local imports
from spyder_claude.widgets.chat import (
    AssistantMessageWidget,
    ImageView,
    NoticeWidget,
    ToolCallWidget,
    UserMessageWidget,
)
from spyder_claude.widgets.main_widget import ClaudeWidget

PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChw"
    "GA60e6kgAAAABJRU5ErkJggg=="
)).decode()


class _ClaudeWidget(ClaudeWidget):
    CONF_SECTION = "claude_agent"


@pytest.fixture
def widget(qtbot):
    widget = _ClaudeWidget("claude_agent", plugin=None)
    widget._setup()
    widget.setup()
    qtbot.addWidget(widget)
    widget.show()
    return widget


def send(qtbot, widget, text):
    widget.input.setPlainText(text)
    with qtbot.waitSignal(widget.sig_send_requested) as blocker:
        qtbot.keyClick(widget.input, Qt.Key_Return)
    return blocker.args[0]


def test_send_and_interrupt(widget, qtbot):
    assert send(qtbot, widget, "Plot my data") == "Plot my data"
    assert isinstance(widget.chat_view.items()[0], UserMessageWidget)
    assert widget.input.toPlainText() == ""
    assert widget.send_button.text() == _("Stop")

    # While busy, the button interrupts the turn
    with qtbot.waitSignal(widget.sig_interrupt_requested):
        widget.send_button.click()

    widget.handle_event(
        {"type": "result", "isError": False, "durationMs": 10,
         "terminalReason": "aborted_streaming"}
    )
    assert widget.send_button.text() == _("Send")
    assert isinstance(widget.chat_view.items()[-1], NoticeWidget)


def test_shift_enter_adds_newline(widget, qtbot):
    widget.input.setPlainText("line 1")
    widget.input.moveCursor(QTextCursor.End)
    with qtbot.assertNotEmitted(widget.sig_send_requested):
        qtbot.keyClick(widget.input, Qt.Key_Return, Qt.ShiftModifier)
    assert widget.input.toPlainText() == "line 1\n"


def test_streaming_text(widget, qtbot):
    for delta in ["Hello", " **world**"]:
        widget.handle_event({"type": "text_delta", "text": delta})
    message = widget.chat_view.items()[-1]
    assert isinstance(message, AssistantMessageWidget)
    qtbot.waitUntil(lambda: message.text() == "Hello **world**")

    # The complete block replaces the deltas in the same widget
    widget.handle_event({"type": "text", "text": "Hello **world**!"})
    assert widget.chat_view.items() == [message]
    assert message.text() == "Hello **world**!"

    # Text after that starts a new message
    widget.handle_event({"type": "text_delta", "text": "Next"})
    assert len(widget.chat_view.items()) == 2


def test_tool_calls(widget, qtbot):
    widget.handle_event(
        {"type": "tool_use", "id": "t1", "name": "ToolSearch", "input": {}}
    )
    assert widget.chat_view.items() == []

    widget.handle_event(
        {"type": "tool_use", "id": "t2", "name": "mcp__spyder__execute_code",
         "input": {"code": "plt.plot(x)"}}
    )
    tool_call = widget.chat_view.items()[-1]
    assert isinstance(tool_call, ToolCallWidget)
    assert "Spyder · execute_code" in tool_call.title_label.text()
    assert tool_call.input_view.toPlainText() == "plt.plot(x)"

    widget.handle_event(
        {"type": "tool_result", "id": "t2", "isError": False,
         "content": [{"type": "text", "text": "done"},
                     {"type": "image", "data": PNG, "mimeType": "image/png"}]}
    )
    assert tool_call.status_label.text() == _("Done")
    assert tool_call.result_layout.count() == 2


@pytest.mark.parametrize(
    "button, expected",
    [
        ("allow_button", {"allow": True, "session": False}),
        ("allow_session_button", {"allow": True, "session": True}),
        ("deny_button", {"allow": False, "session": False}),
    ],
)
def test_approval(widget, qtbot, button, expected):
    widget.handle_event(
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "pip install seaborn"}}
    )
    decisions = []
    widget.ask_approval(
        {"toolName": "Bash", "toolUseId": "t1",
         "input": {"command": "pip install seaborn"}},
        decisions.append,
    )
    tool_call = widget.chat_view.items()[-1]
    assert not tool_call.approval_widget.isHidden()

    getattr(tool_call, button).click()
    assert decisions == [expected]
    assert tool_call.approval_widget.isHidden()


def test_approval_without_tool_use_event(widget):
    """Approvals create the tool call widget if it doesn't exist yet."""
    widget.ask_approval(
        {"toolName": "Write", "toolUseId": "t9",
         "input": {"file_path": "/tmp/a.py", "content": "x = 1"}},
        lambda decision: None,
    )
    tool_call = widget.chat_view.items()[-1]
    assert tool_call.input_view.toPlainText() == "/tmp/a.py\nx = 1"


def test_content_fits_pane_width(widget, qtbot):
    """Wide images and long lines don't make the chat wider than the pane."""
    widget.resize(300, 600)
    wide_png = QPixmap(800, 400)
    wide_png.fill(Qt.red)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    wide_png.save(buffer, "PNG")

    widget.handle_event(
        {"type": "tool_use", "id": "t1", "name": "mcp__spyder__execute_code",
         "input": {"code": "x = 1  # " + "a very long line " * 20}}
    )
    widget.handle_event(
        {"type": "tool_result", "id": "t1", "isError": False,
         "content": [
             {"type": "text",
              "text": "(no output)\n[Image: source: /tmp/figure.png]"},
             {"type": "image",
              "data": base64.b64encode(bytes(data)).decode(),
              "mimeType": "image/png"},
         ]}
    )
    widget.handle_event({"type": "text", "text": "word " * 200})

    qtbot.waitUntil(
        lambda: widget.chat_view.widget().width()
        <= widget.chat_view.viewport().width()
    )

    tool_call = widget.chat_view.items()[0]
    image = tool_call.result_layout.itemAt(1).widget()
    assert isinstance(image, ImageView)
    assert image.heightForWidth(400) == 200

    # The CLI's reference to the saved image is not shown
    result_text = tool_call.result_layout.itemAt(0).widget().toPlainText()
    assert result_text == "(no output)"
