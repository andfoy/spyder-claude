# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Dialog to choose a Claude Code conversation to resume."""

# Standard library imports
from __future__ import annotations

import os
import os.path as osp

# Third-party imports
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from spyder.api.translations import _

# Local imports
from spyder_claude.conversations import Conversation


class ConversationsDialog(QDialog):
    """
    List of saved conversations from every directory, the most recent
    first, to resume one in the directory where it started.
    """

    def __init__(
        self,
        conversations: list[Conversation],
        open_ids: set[str] | None = None,
        project: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Resume conversation"))
        self.resize(760, 420)
        self._conversations = {c.id: c for c in conversations}

        self.search = QLineEdit(self)
        self.search.setPlaceholderText(_("Search by title or folder"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)

        # Show the conversations of the open project by default
        self._project = project
        self.project_only = QCheckBox(_("Only this project"), self)
        self.project_only.setChecked(project is not None)
        self.project_only.setVisible(project is not None)
        self.project_only.toggled.connect(lambda: self._filter())

        self.tree = QTreeWidget(self)
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(
            [_("Conversation"), _("Folder"), _("Last activity")]
        )
        self.tree.itemDoubleClicked.connect(lambda *args: self.accept())
        self.tree.currentItemChanged.connect(self._update_buttons)

        open_ids = open_ids or set()
        for conversation in conversations:
            title = conversation.title or _("(untitled)")
            if conversation.id in open_ids:
                title = _("{} (open)").format(title)
            item = QTreeWidgetItem(
                [
                    title,
                    conversation.cwd or "",
                    conversation.updated.strftime("%Y-%m-%d %H:%M"),
                ]
            )
            item.setData(0, Qt.UserRole, conversation.id)
            item.setToolTip(0, conversation.title)
            item.setToolTip(1, conversation.cwd or "")
            if not conversation.can_resume():
                item.setDisabled(True)
                item.setToolTip(
                    1, _("This folder doesn't exist anymore")
                )
            self.tree.addTopLevelItem(item)
        self.tree.setColumnWidth(0, 380)
        self.tree.setColumnWidth(1, 230)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        self.buttons.button(QDialogButtonBox.Ok).setText(_("Resume"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.project_only)
        layout.addWidget(self.tree)
        layout.addWidget(self.buttons)

        # Select the most recent one that can be resumed
        self._filter()
        self.search.setFocus()

    def get_conversation(self) -> Conversation | None:
        """The selected conversation, if it can be resumed."""
        item = self.tree.currentItem()
        if item is None or item.isDisabled() or item.isHidden():
            return None
        return self._conversations.get(item.data(0, Qt.UserRole))

    def accept(self) -> None:
        if self.get_conversation() is not None:
            super().accept()

    def _in_project(self, folder: str) -> bool:
        if not self.project_only.isChecked() or not self._project:
            return True
        project = osp.normcase(osp.realpath(self._project))
        folder = osp.normcase(osp.realpath(folder)) if folder else ""
        return folder == project or folder.startswith(project + os.sep)

    def _filter(self, *_args) -> None:
        words = self.search.text().lower().split()
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            haystack = (item.text(0) + " " + item.text(1)).lower()
            item.setHidden(
                not all(word in haystack for word in words)
                or not self._in_project(item.text(1))
            )

        current = self.tree.currentItem()
        if current is None or current.isHidden():
            visible = [
                self.tree.topLevelItem(index)
                for index in range(self.tree.topLevelItemCount())
                if not self.tree.topLevelItem(index).isHidden()
                and not self.tree.topLevelItem(index).isDisabled()
            ]
            self.tree.setCurrentItem(visible[0] if visible else None)
        self._update_buttons()

    def _update_buttons(self, *_args) -> None:
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            self.get_conversation() is not None
        )
