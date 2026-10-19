# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Tests for the Claude Code pane in a full Spyder window.

Claude Code is replaced by ``fake_claude.py``, which connects to Spyder's MCP
server the same way, so these tests don't need Claude credentials.
"""

# Standard library imports
import os
import os.path as osp
import stat
import sys

# Third-party imports
import pytest

from spyder.plugins.ipythonconsole.tests.conftest import SHELL_TIMEOUT

# Local imports
from spyder_claude.tests.conftest import (
    read_terminal,
    save_screenshot,
    send_keys,
)

pytest.importorskip("spyder_terminal")
pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="The fake CLI is a POSIX script"
)

FAKE_CLAUDE = osp.join(osp.dirname(__file__), "fake_claude.py")


@pytest.fixture
def fake_claude(tmp_path):
    """Executable that runs fake_claude.py with this Python."""
    path = tmp_path / "claude"
    path.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_CLAUDE} \"$@\"\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def wait_for_text(term, qtbot, text, timeout=60000):
    qtbot.waitUntil(
        lambda: text in read_terminal(term, qtbot), timeout=timeout
    )


def test_session_connects_to_spyder(
    main_window, qtbot, tmp_path, fake_claude
):
    shell = main_window.ipyconsole.get_current_shellwidget()
    qtbot.waitUntil(
        lambda: shell._prompt_html is not None, timeout=SHELL_TIMEOUT
    )

    plugin = main_window.get_plugin("claude_code")
    plugin.set_conf("claude_command", fake_claude)
    widget = plugin.get_widget()
    qtbot.waitUntil(widget.server_is_ready, timeout=60000)

    # No session starts while the pane is hidden
    assert not widget.get_terms()

    # Start a session, which starts the MCP server too
    plugin.new_session(path=str(tmp_path))
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 1, timeout=60000)
    term = widget.get_terms()[0]
    assert widget.tabwidget.tabText(0) == "Claude 1"
    assert main_window.get_plugin("mcp_server").is_server_running()

    # The CLI found Spyder's tools and ran code in the console
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    text = read_terminal(term, qtbot)
    assert "FAKE CLAUDE ARGS: continue=False" in text
    assert "execute_code" in text and "list_variables" in text
    assert "FAKE CLAUDE EXECUTED, error: False" in text
    assert shell.get_value("fake_claude_value") == 42
    save_screenshot(main_window, "claude_code_pane.png")

    # The config file with the token is private
    config_path = plugin._mcp_config.path
    assert stat.S_IMODE(os.stat(config_path).st_mode) == 0o600

    # Showing the pane didn't start a second session
    qtbot.wait(1000)
    assert len(widget.get_terms()) == 1

    # Claude Code replaced the shell, so its tab closes when it exits
    send_keys(term, "\r")
    qtbot.waitUntil(lambda: not widget.get_terms(), timeout=20000)

    # and no other session starts
    qtbot.wait(2000)
    assert not widget.get_terms()

    # Resuming passes --continue
    widget.get_action("resume_claude_code_session_action").trigger()
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 1, timeout=60000)
    term = widget.get_terms()[0]
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    assert "FAKE CLAUDE ARGS: continue=True" in read_terminal(term, qtbot)
    assert widget.tabwidget.tabText(0) == "Claude 2"
