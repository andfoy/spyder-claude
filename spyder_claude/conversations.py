# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Claude Code conversations saved on disk, to resume them.

Claude Code saves each conversation as a JSON Lines file named after its
session id, in ``<config dir>/projects/<folder>/``. Conversations can only
be resumed from the directory where they started, which is recorded in
their messages (``cwd``).

These files are internal to Claude Code, so they're only read, and
defensively: entries that can't be parsed are skipped.
"""

# Standard library imports
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import glob
import json
import os
import os.path as osp

# Bytes read from the start of a file to find where it started, and from the
# end to find its latest title. Files can be large, so they aren't read
# whole.
HEAD_BYTES = 64 * 1024
TAIL_BYTES = 256 * 1024


@dataclass
class Conversation:
    """A saved Claude Code conversation."""

    id: str
    """Session id, for ``claude --resume <id>``."""

    cwd: str | None
    """Directory where it started, where it must be resumed."""

    title: str
    """Title set by the user or by Claude, or its last prompt."""

    updated: datetime
    """Last time it changed."""

    path: str
    """File where Claude Code saves it."""

    def can_resume(self) -> bool:
        return self.cwd is not None and osp.isdir(self.cwd)


def get_config_dir() -> str:
    """Claude Code's configuration directory."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or osp.join(
        osp.expanduser("~"), ".claude"
    )


def _read_entries(path: str, start: int, size: int) -> list[dict]:
    """JSON entries in ``size`` bytes of a file from ``start``."""
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(size)

    lines = data.split(b"\n")
    if start > 0:
        # The first line is probably cut
        lines = lines[1:]

    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def read_conversation(path: str) -> Conversation | None:
    """Read the metadata of the conversation saved in ``path``."""
    try:
        size = osp.getsize(path)
        updated = datetime.fromtimestamp(osp.getmtime(path))
        head = _read_entries(path, 0, HEAD_BYTES)
        tail = (
            _read_entries(path, size - TAIL_BYTES, TAIL_BYTES)
            if size > HEAD_BYTES
            else head
        )
    except OSError:
        return None

    cwd = next(
        (
            entry["cwd"]
            for entry in head
            if isinstance(entry.get("cwd"), str)
        ),
        None,
    )
    if cwd is None:
        # E.g. a session that was started but never used
        return None

    titles = {}
    for entry in tail:
        for kind, key in [
            ("custom-title", "customTitle"),
            ("ai-title", "aiTitle"),
            ("last-prompt", "lastPrompt"),
        ]:
            if entry.get("type") == kind and isinstance(entry.get(key), str):
                titles[kind] = entry[key]
    title = (
        titles.get("custom-title")
        or titles.get("ai-title")
        or titles.get("last-prompt")
        or ""
    )

    return Conversation(
        id=osp.splitext(osp.basename(path))[0],
        cwd=cwd,
        title=" ".join(title.split()),
        updated=updated,
        path=path,
    )


def list_conversations(config_dir: str | None = None) -> list[Conversation]:
    """Saved conversations, the most recent first."""
    config_dir = config_dir or get_config_dir()
    pattern = osp.join(config_dir, "projects", "*", "*.jsonl")
    conversations = [
        conversation
        for conversation in map(read_conversation, glob.glob(pattern))
        if conversation is not None
    ]
    conversations.sort(key=lambda c: c.updated, reverse=True)
    return conversations


def find_conversation(
    session_id: str, config_dir: str | None = None
) -> Conversation | None:
    """The saved conversation with id ``session_id``, if any."""
    config_dir = config_dir or get_config_dir()
    filename = glob.escape(session_id) + ".jsonl"
    paths = glob.glob(osp.join(config_dir, "projects", "*", filename))
    return read_conversation(paths[0]) if paths else None


def last_conversation_in(
    directory: str, config_dir: str | None = None
) -> Conversation | None:
    """The most recent conversation started in ``directory``."""
    directory = osp.normcase(osp.realpath(directory))
    for conversation in list_conversations(config_dir):
        if osp.normcase(osp.realpath(conversation.cwd)) == directory:
            return conversation
    return None
