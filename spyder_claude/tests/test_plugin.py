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
import json
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


def terminal_size(term, qtbot, fit=False):
    """Columns and rows of the terminal, optionally after fitting it."""
    result = []
    script = "[window.term.cols, window.term.rows]"
    if fit:
        script = (
            "window.dispatchEvent(new Event('resize')); " + script
        )
    term.view.document.runJavaScript(script, result.append)
    qtbot.waitUntil(lambda: bool(result))
    return result[0]


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

    # The first time it runs code, the pane asks which console to use
    bar = widget.question_bar
    qtbot.waitUntil(bar.isVisible, timeout=60000)
    assert "Claude 1" in bar.label.text()
    assert bar.selected_button.text() == "In Console 1/A"
    save_screenshot(main_window, "claude_code_console_question.png")
    bar.selected_button.click()

    # The CLI found Spyder's tools and ran code in the console
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    assert not bar.isVisible()

    # The answer is the session's setting
    session = widget.get_session(term)
    assert (session.console_mode, session.console_id) == ("specific", "1/A")
    assert widget.console_button.text() == "Console 1/A"

    # The session identified itself to Spyder
    mcp_server = main_window.get_plugin("mcp_server")
    assert [(c.id, c.name) for c in mcp_server.get_clients()] == [
        (session.id, "Claude 1")
    ]

    # The terminal was fitted to the pane after its font was applied, so
    # fitting it again doesn't change its size
    assert terminal_size(term, qtbot) == terminal_size(term, qtbot, fit=True)
    text = read_terminal(term, qtbot)
    assert "FAKE CLAUDE ARGS: continue=False" in text
    assert "execute_code" in text and "list_variables" in text

    # Tools come from every plugin that exposes methods
    for tool in ["get_figure", "get_recent_files", "get_workdir"]:
        assert tool in text
    assert "FAKE CLAUDE EXECUTED, error: False" in text
    assert shell.get_value("fake_claude_value") == 42
    save_screenshot(main_window, "claude_code_pane.png")

    # The terminal and its actions find their shortcuts
    for name in ["copy", "paste", "clear", "zoom_in", "zoom_out"]:
        assert term.view.get_shortcut(name)
    copy_action = widget.get_action("copy")
    assert copy_action.shortcut_context == "claude_code"
    assert copy_action.shortcut().toString()

    # The config file with the token is private
    config_path = session.config_path
    assert stat.S_IMODE(os.stat(config_path).st_mode) == 0o600

    # Showing the pane didn't start a second session
    qtbot.wait(1000)
    assert len(widget.get_terms()) == 1

    # Claude Code replaced the shell, so its tab closes when it exits, and
    # the session's config and settings are removed
    send_keys(term, "\r")
    qtbot.waitUntil(lambda: not widget.get_terms(), timeout=20000)
    assert not osp.exists(config_path)
    assert mcp_server.get_client_argument(session.id, "console_id") is None

    # and no other session starts
    qtbot.wait(2000)
    assert not widget.get_terms()

    # Resuming passes --continue
    widget.get_action("resume_claude_code_session_action").trigger()
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 1, timeout=60000)
    term = widget.get_terms()[0]
    qtbot.waitUntil(bar.isVisible, timeout=60000)
    bar.selected_button.click()
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    assert "FAKE CLAUDE ARGS: continue=True" in read_terminal(term, qtbot)
    assert widget.tabwidget.tabText(0) == "Claude 2"


def call_as_session(qtbot, mcp_server, session, name, arguments):
    """Call a tool like the session's Claude Code would, and wait."""
    results = []
    mcp_server._dispatcher.call(
        name,
        arguments,
        {"id": session.id, "name": session.name},
        lambda content, is_error: results.append((content, is_error)),
    )
    qtbot.waitUntil(lambda: bool(results), timeout=SHELL_TIMEOUT)
    content, is_error = results[0]
    assert not is_error, content
    return content[0]["text"]


def test_session_consoles(main_window, qtbot, tmp_path, fake_claude):
    """Sessions can get a console of their own, chosen per session."""
    ipyconsole = main_window.ipyconsole
    shell = ipyconsole.get_current_shellwidget()
    qtbot.waitUntil(
        lambda: shell._prompt_html is not None, timeout=SHELL_TIMEOUT
    )

    plugin = main_window.get_plugin("claude_code")
    plugin.set_conf("claude_command", fake_claude)
    widget = plugin.get_widget()
    mcp_server = main_window.get_plugin("mcp_server")
    qtbot.waitUntil(widget.server_is_ready, timeout=60000)

    plugin.new_session(path=str(tmp_path))
    qtbot.waitUntil(widget.question_bar.isVisible, timeout=60000)
    session = widget.get_session()

    # Answering "new console" opens one named after the session, without
    # changing the console the user has selected
    widget.question_bar.new_button.click()
    term = widget.get_terms()[0]
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    assert session.console_mode == "new"
    assert session.console_id != "1/A"
    assert ipyconsole.get_current_shellwidget() is shell
    assert widget.console_button.text().startswith("Claude 1")
    assert not shell.is_defined("fake_claude_value")

    # Later calls use it too, and Spyder shows the session created it
    consoles = json.loads(
        call_as_session(qtbot, mcp_server, session, "list_consoles", {})
    )
    own = [c for c in consoles if c["id"] == session.console_id][0]
    assert own["created_by"] == "you"
    output = call_as_session(
        qtbot,
        mcp_server,
        session,
        "execute_code",
        {"code": "print(fake_claude_value)"},
    )
    assert json.loads(output)["output"] == "42\n"

    # Following the selected console
    plugin.set_session_console(session.id, "selected")
    assert widget.console_button.text() == "Selected console"
    call_as_session(
        qtbot, mcp_server, session, "execute_code", {"code": "here = 1"}
    )
    assert shell.get_value("here") == 1

    # If the session's console closes, it asks again
    plugin.set_session_console(session.id, "specific", own["id"])
    own_client = [
        client
        for client in ipyconsole.get_clients()
        if client.get_name() == own["name"]
    ][0]
    ipyconsole.get_widget().close_client(
        client=own_client, ask_recursive=False
    )
    qtbot.waitUntil(lambda: session.console_mode == "ask", timeout=10000)
    assert session.console_id is None

    send_keys(term, "\r")
    qtbot.waitUntil(lambda: not widget.get_terms(), timeout=20000)
