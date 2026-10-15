# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Widgets to show the conversation with the agent."""

# Standard library imports
from __future__ import annotations

import base64
import json
import re

# Third-party imports
from qtpy.QtCore import QRect, QSize, Qt, QTimer, Signal
from qtpy.QtGui import QPainter, QPixmap
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from spyder.api.fonts import SpyderFontsMixin, SpyderFontType
from spyder.api.translations import _
from spyder.utils.palette import SpyderPalette

# Local imports
from spyder_claude.protocol import MCP_TOOL_PREFIX

# Maximum characters of tool results shown in the chat.
MAX_RESULT_CHARS = 3000

# Lines the CLI adds to tool results to reference images it saved to disk.
CLI_IMAGE_REFERENCE = re.compile(r"^\[Image: source: .*\]$", re.MULTILINE)


def tool_display_name(name: str) -> str:
    """Name of a tool as shown to users."""
    if name.startswith(MCP_TOOL_PREFIX):
        return f"Spyder · {name[len(MCP_TOOL_PREFIX):]}"
    return name


def tool_input_preview(name: str, tool_input: dict) -> str:
    """Text shown to describe the input of a tool call."""
    if name == f"{MCP_TOOL_PREFIX}execute_code":
        return tool_input.get("code", "")
    if name == "Bash":
        return tool_input.get("command", "")
    if name == "Edit":
        old = tool_input.get("old_string", "").split("\n")
        new = tool_input.get("new_string", "").split("\n")
        diff = [f"- {line}" for line in old] + [f"+ {line}" for line in new]
        return "\n".join([tool_input.get("file_path", "")] + diff)
    if name == "Write":
        return (
            f"{tool_input.get('file_path', '')}\n"
            f"{tool_input.get('content', '')}"
        )
    if not tool_input:
        return ""
    return json.dumps(tool_input, indent=2)


# ---- Messages
# -----------------------------------------------------------------------------
class UserMessageWidget(QLabel):
    """A message sent by the user."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setStyleSheet(
            f"background-color: {SpyderPalette.COLOR_BACKGROUND_4};"
            f"border-radius: 6px; padding: 8px;"
        )


class AssistantMessageWidget(QLabel):
    """Text written by Claude, rendered as Markdown while it streams."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.text_ = ""
        self.setWordWrap(True)
        self.setTextFormat(Qt.MarkdownText)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse
        )
        self.setStyleSheet("padding: 2px 4px;")

        # Rendering Markdown on every delta is expensive, so throttle it
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._render)

    def append_text(self, delta: str) -> None:
        self.text_ += delta
        if not self._render_timer.isActive():
            self._render_timer.start()

    def set_full_text(self, text: str) -> None:
        self.text_ = text
        self._render_timer.stop()
        self._render()

    def _render(self) -> None:
        self.setText(self.text_)


class NoticeWidget(QLabel):
    """Status or error notice in the conversation."""

    def __init__(self, text: str, error: bool = False, parent=None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        if error:
            color = SpyderPalette.COLOR_ERROR_2
        else:
            color = SpyderPalette.COLOR_TEXT_2
        self.setStyleSheet(f"color: {color}; padding: 2px 4px;")


class ImageView(QWidget):
    """Image that shrinks to the available width, keeping its ratio."""

    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.pixmap = pixmap
        policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def sizeHint(self) -> QSize:
        return self.pixmap.size()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        if self.pixmap.isNull():
            return 0
        width = min(width, self.pixmap.width())
        return round(self.pixmap.height() * width / self.pixmap.width())

    def paintEvent(self, event) -> None:
        width = min(self.width(), self.pixmap.width())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(
            QRect(0, 0, width, self.heightForWidth(width)), self.pixmap
        )


# ---- Tool calls
# -----------------------------------------------------------------------------
class ToolCallWidget(QFrame, SpyderFontsMixin):
    """A tool call, with its input, approval buttons and result."""

    sig_decided = Signal(dict)
    """
    This signal is emitted when the user approves or denies the call.

    Parameters
    ----------
    decision: dict
        ``{"allow": bool, "session": bool}``, where ``session`` means
        allowing the tool for the rest of the session.
    """

    def __init__(self, tool_id: str, name: str, tool_input: dict,
                 parent=None) -> None:
        super().__init__(parent)
        self.tool_id = tool_id
        self.name = name
        self.setObjectName("toolCall")
        self.setStyleSheet(
            f"#toolCall {{border: 1px solid "
            f"{SpyderPalette.COLOR_BACKGROUND_5}; border-radius: 6px;}}"
        )
        mono = self.get_font(SpyderFontType.Monospace)

        # Header
        self.title_label = QLabel(f"<b>{tool_display_name(name)}</b>")
        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"color: {SpyderPalette.COLOR_TEXT_2};"
        )
        header = QHBoxLayout()
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.status_label)

        # Input
        self.input_view = self._make_text_view(
            tool_input_preview(name, tool_input), mono, wrap=False
        )

        # Approval
        self.allow_button = QPushButton(_("Allow"))
        self.allow_session_button = QPushButton(_("Always allow"))
        self.allow_session_button.setToolTip(
            _("Allow this tool for the rest of the session")
        )
        self.deny_button = QPushButton(_("Deny"))
        self.allow_button.clicked.connect(
            lambda: self._decide(allow=True, session=False)
        )
        self.allow_session_button.clicked.connect(
            lambda: self._decide(allow=True, session=True)
        )
        self.deny_button.clicked.connect(
            lambda: self._decide(allow=False, session=False)
        )
        self.approval_widget = QWidget()
        buttons = QHBoxLayout()
        for button in [
            self.allow_button,
            self.allow_session_button,
            self.deny_button,
        ]:
            buttons.addWidget(button)
        buttons.addStretch()
        approval_layout = QVBoxLayout(self.approval_widget)
        approval_layout.setContentsMargins(0, 0, 0, 0)
        approval_layout.addWidget(QLabel(_("Claude wants to run this.")))
        approval_layout.addLayout(buttons)
        self.approval_widget.hide()

        # Result
        self.result_layout = QVBoxLayout()
        self.result_layout.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.addLayout(header)
        if self.input_view is not None:
            layout.addWidget(self.input_view)
        layout.addWidget(self.approval_widget)
        layout.addLayout(self.result_layout)

        self.set_status(_("Running…"))

    # ---- Public API
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def ask_approval(self) -> None:
        self.set_status(_("Waiting for approval"))
        self.approval_widget.show()
        self.allow_button.setFocus()

    def set_result(self, content: list[dict], is_error: bool) -> None:
        self.approval_widget.hide()
        self.set_status(_("Error") if is_error else _("Done"))

        text = "\n".join(
            item["text"] for item in content if item["type"] == "text"
        )
        text = CLI_IMAGE_REFERENCE.sub("", text).strip()
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n…"
        if text:
            view = self._make_text_view(
                text, self.get_font(SpyderFontType.Monospace), wrap=True
            )
            if is_error:
                view.setStyleSheet(
                    f"color: {SpyderPalette.COLOR_ERROR_2};"
                )
            self.result_layout.addWidget(view)

        for item in content:
            if item["type"] == "image":
                self.result_layout.addWidget(self._make_image(item))

    # ---- Private API
    def _decide(self, allow: bool, session: bool) -> None:
        self.approval_widget.hide()
        self.set_status(_("Running…") if allow else _("Denied"))
        self.sig_decided.emit({"allow": allow, "session": session})

    def _make_text_view(
        self, text: str, font, wrap: bool
    ) -> QPlainTextEdit | None:
        if not text:
            return None
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(font)
        view.setMinimumWidth(0)
        view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        # Show up to 12 lines; longer texts scroll
        lines = min(text.count("\n") + 1, 12)
        height = view.fontMetrics().lineSpacing() * lines + 16
        if wrap:
            view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        else:
            view.setLineWrapMode(QPlainTextEdit.NoWrap)
            height += view.horizontalScrollBar().sizeHint().height()
        view.setFixedHeight(height)
        return view

    def _make_image(self, item: dict) -> ImageView:
        pixmap = QPixmap()
        pixmap.loadFromData(base64.b64decode(item["data"]))
        return ImageView(pixmap)


# ---- Conversation
# -----------------------------------------------------------------------------
class ChatView(QScrollArea):
    """Scrollable list of the conversation items."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(8)
        self._layout.addStretch()
        self.setWidget(container)

        self._stick_to_bottom = True
        scrollbar = self.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scrolled)
        scrollbar.rangeChanged.connect(self._on_range_changed)

    def add_item(self, widget: QWidget) -> QWidget:
        widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._layout.insertWidget(self._layout.count() - 1, widget)
        return widget

    def clear(self) -> None:
        while self._layout.count() > 1:
            widget = self._layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._stick_to_bottom = True

    def items(self) -> list[QWidget]:
        return [
            self._layout.itemAt(i).widget()
            for i in range(self._layout.count() - 1)
        ]

    def _on_scrolled(self, value: int) -> None:
        # Keep following new content only while the user is at the bottom
        self._stick_to_bottom = value >= self.verticalScrollBar().maximum()

    def _on_range_changed(self, minimum: int, maximum: int) -> None:
        if self._stick_to_bottom:
            self.verticalScrollBar().setValue(maximum)
