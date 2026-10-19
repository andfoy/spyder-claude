# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Claude Code sessions and the console each one runs code in.

Each session (a tab of the pane) identifies itself to Spyder's MCP server, so
Spyder can tell sessions apart. The console a session uses is chosen per
session:

* Ask on first run (default): the first time the session runs code, the pane
  asks the user, and the answer becomes the session's setting.
* Selected console: the console selected in Spyder at the time of each call.
* New console: a console of its own, opened the first time it runs code.
* A specific console.

Claude can still pass a console explicitly, which always wins.
"""

# Standard library imports
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
import uuid


class ConsoleMode:
    Ask = "ask"
    Selected = "selected"
    New = "new"
    Specific = "specific"


@dataclass(eq=False)
class Session:
    """A Claude Code session."""

    name: str
    """Name shown to the user and in Spyder's tools, e.g. ``Claude 2``."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """
    Id of the Claude Code conversation (its session id), also sent to
    Spyder's MCP server with every tool call.
    """

    cwd: str | None = None
    """Directory where the conversation started."""

    title: str = ""
    """Title of the conversation, if it was resumed."""

    config_path: str | None = None
    """MCP config file of the session."""

    console_mode: str = ConsoleMode.Ask
    """How the session's console is chosen (see :class:`ConsoleMode`)."""

    console_id: str | None = None
    """
    Console the session uses, for the ``Specific`` mode and the ``New`` one
    once its console is open.
    """

    question: Future | None = None
    """
    Pending question to the user about which console to use. Resolved with
    the ``(mode, console_id)`` they choose.
    """

    console_opening: Future | None = None
    """Console being opened for the ``New`` mode, resolved with its id."""

    asking_in_client: bool = False
    """
    Whether the pending question is asked in Claude Code (an MCP form),
    instead of in a bar above the session's terminal.
    """

    def is_asking(self) -> bool:
        return self.question is not None and not self.question.done()
