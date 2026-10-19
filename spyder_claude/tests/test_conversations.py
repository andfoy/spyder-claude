# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Tests for reading saved Claude Code conversations."""

# Standard library imports
import json
import os
import uuid

# Local imports
from spyder_claude import conversations
from spyder_claude.conversations import (
    find_conversation,
    last_conversation_in,
    list_conversations,
)


def save_conversation(config_dir, cwd, entries=(), mtime=None, filler=0):
    """
    Save a conversation like Claude Code does and return its id.

    ``filler`` adds that many bytes of messages, to test large files.
    """
    session_id = str(uuid.uuid4())
    folder = config_dir / "projects" / str(cwd).replace(os.sep, "-")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"

    lines = [{"type": "mode", "mode": "default", "sessionId": session_id}]
    lines.append(
        {"type": "user", "cwd": str(cwd), "sessionId": session_id,
         "message": {"role": "user", "content": "hello"}}
    )
    if filler:
        message = {"type": "assistant", "cwd": str(cwd),
                   "message": {"content": "x" * 1000}}
        lines += [message] * (filler // 1000)
    lines += list(entries)

    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
        # Partial lines (e.g. while it's being written) are skipped
        f.write('{"type": "ai-ti')

    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return session_id


def test_list_conversations(tmp_path, monkeypatch):
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    project = tmp_path / "project"
    project.mkdir()

    old = save_conversation(
        config_dir,
        project,
        [{"type": "last-prompt", "lastPrompt": "Fix the\n  tests"}],
        mtime=1_000_000,
    )
    titled = save_conversation(
        config_dir,
        project,
        [
            {"type": "ai-title", "aiTitle": "First title"},
            {"type": "ai-title", "aiTitle": "Plot the results"},
            {"type": "last-prompt", "lastPrompt": "and the legend"},
        ],
        mtime=3_000_000,
    )
    gone = save_conversation(
        config_dir, tmp_path / "removed", mtime=2_000_000
    )

    found = list_conversations()
    assert [c.id for c in found] == [titled, gone, old]

    # Titles by Claude are preferred, then the last prompt
    by_id = {c.id: c for c in found}
    assert by_id[titled].title == "Plot the results"
    assert by_id[old].title == "Fix the tests"
    assert by_id[gone].title == ""

    # They can only be resumed where they started
    assert by_id[titled].cwd == str(project)
    assert by_id[titled].can_resume()
    assert not by_id[gone].can_resume()

    assert find_conversation(old).title == "Fix the tests"
    assert find_conversation(str(uuid.uuid4())) is None
    assert last_conversation_in(str(project)).id == titled
    assert last_conversation_in(str(tmp_path)) is None


def test_large_conversation(tmp_path, monkeypatch):
    """Only the start and end of large files are read."""
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    session_id = save_conversation(
        config_dir,
        tmp_path,
        [{"type": "ai-title", "aiTitle": "Long one"}],
        filler=2 * conversations.TAIL_BYTES,
    )
    conversation = find_conversation(session_id)
    assert conversation.title == "Long one"
    assert conversation.cwd == str(tmp_path)


def test_unusable_files(tmp_path, monkeypatch):
    """Files without messages or that aren't JSON are skipped."""
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    folder = config_dir / "projects" / "folder"
    folder.mkdir(parents=True)
    (folder / "empty.jsonl").write_text('{"type": "mode"}\n')
    (folder / "binary.jsonl").write_bytes(b"\xff\xfe\x00garbage")
    assert list_conversations() == []
