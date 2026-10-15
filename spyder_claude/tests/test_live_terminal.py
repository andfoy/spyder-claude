# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
End-to-end test of Claude Code running in Spyder's terminal.

It needs spyder-terminal and a logged-in Claude Code CLI, and it uses your
Claude credentials, so it only runs when SPYDER_CLAUDE_LIVE is set. Set
SPYDER_CLAUDE_SCREENSHOTS to a directory to save screenshots and the
terminal contents.
"""

# Standard library imports
import json
import os
import os.path as osp
import shutil
import time

# Third-party imports
import pytest

from spyder.plugins.ipythonconsole.tests.conftest import SHELL_TIMEOUT

# Local imports
from spyder_claude.tests.test_live import main_window  # noqa: F401

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("SPYDER_CLAUDE_LIVE"),
        reason="Set SPYDER_CLAUDE_LIVE to run tests against the real model",
    ),
    pytest.mark.skipif(
        shutil.which("claude") is None, reason="Claude Code isn't installed"
    ),
]
pytest.importorskip("spyder_terminal")

PROMPT = (
    "Use the Spyder tools to run this in my IPython console: create a numpy "
    "array called x with 100 points between 0 and 2*pi and plot sin(x) with "
    "matplotlib. Then describe the plot in one short sentence."
)

# Texts shown by Claude Code when it asks to trust the folder and to use a
# tool
TRUST_PROMPT = "Yes, I trust this folder"
TRUST_SELECTED = "\u276f Yes, I trust this folder"
TRUST_NOT_SELECTED = "\u276f No, exit"
PERMISSION_PROMPT = "Do you want to proceed?"

BANNER = "Claude Code v"

DOWN = "\x1b[B"


def read_terminal(term, qtbot) -> str:
    """Text in the terminal buffer, including the scrollback."""
    result = []
    term.view.document.runJavaScript(
        "spyder_terminal.default.getTerminalLines()", result.append
    )
    qtbot.waitUntil(lambda: bool(result), timeout=5000)
    return result[0] or ""


def send_keys(term, data: str) -> None:
    """Send raw input to the terminal, as if typed by the user."""
    term.view.document.runJavaScript(f"window.term.input({json.dumps(data)})")


def save_artifacts(window, term, text, name):
    directory = os.environ.get("SPYDER_CLAUDE_SCREENSHOTS")
    if directory:
        window.grab().save(osp.join(directory, f"{name}.png"))
        with open(osp.join(directory, f"{name}.txt"), "w") as f:
            f.write(text)


@pytest.fixture(autouse=True)
def clean_claude_env(monkeypatch):
    """
    Remove variables set by a parent Claude Code session (e.g. when tests
    are run by Claude Code), which change how the CLI behaves.
    """
    for name in list(os.environ):
        if name.startswith("CLAUDE"):
            monkeypatch.delenv(name)


def test_claude_code_in_terminal(main_window, qtbot, tmp_path):  # noqa: F811
    main_window.resize(1500, 950)
    shell = main_window.ipyconsole.get_current_shellwidget()
    qtbot.waitUntil(
        lambda: shell._prompt_html is not None, timeout=SHELL_TIMEOUT
    )

    terminal = main_window.get_plugin("terminal")
    terminal_widget = terminal.get_widget()
    qtbot.waitUntil(terminal_widget.server_is_ready, timeout=60000)
    terminal.set_current_cwd(str(tmp_path))

    # Start Claude Code from the Claude pane
    plugin = main_window.get_plugin("claude_agent")
    terms_before = len(terminal_widget.get_terms())
    plugin.open_in_terminal()
    qtbot.waitUntil(
        lambda: len(terminal_widget.get_terms()) == terms_before + 1,
        timeout=60000,
    )
    term = terminal_widget.get_terms()[-1]
    config_path = plugin._mcp_config.path
    assert config_path is not None and osp.exists(config_path)

    # Accept the folder trust dialog, then wait for the input box
    deadline = time.monotonic() + 90
    trusted = False
    while time.monotonic() < deadline:
        qtbot.wait(1000)
        text = read_terminal(term, qtbot)
        if not trusted and TRUST_PROMPT in text:
            # The first option is "No, exit", so select the second one and
            # check it's highlighted before accepting
            if text.rfind(TRUST_SELECTED) > text.rfind(TRUST_NOT_SELECTED):
                send_keys(term, "\r")
                trusted = True
            else:
                send_keys(term, DOWN)
        elif BANNER in text and TRUST_PROMPT not in text:
            break
    save_artifacts(main_window, term, text, "claude_code_started")
    assert BANNER in text, text[-2000:]
    qtbot.wait(3000)

    # Send the prompt. Enter is sent separately so it isn't taken as part of
    # a paste.
    send_keys(term, PROMPT)
    qtbot.wait(1000)
    send_keys(term, "\r")

    # Approve tool calls, if asked, until the code ran and the figure is in
    # Plots
    plots = main_window.get_plugin("plots").get_widget()
    approvals = 0
    last_approval = 0.0
    approval_saved = False

    def has_x():
        try:
            return shell.get_value("x").shape == (100,)
        except Exception:
            return False

    def has_figure():
        browser = plots.get_widget_for_shellwidget(shell)
        return browser is not None and bool(browser.thumbnails_sb._thumbnails)

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        qtbot.wait(1000)
        text = read_terminal(term, qtbot)
        if (
            PERMISSION_PROMPT in text
            and time.monotonic() - last_approval > 3
        ):
            if not approval_saved:
                save_artifacts(main_window, term, text, "claude_code_approval")
                approval_saved = True
            send_keys(term, "\r")
            approvals += 1
            last_approval = time.monotonic()
        if has_figure() and has_x():
            break

    # Give Claude time to write its answer
    qtbot.wait(15000)
    text = read_terminal(term, qtbot)
    save_artifacts(main_window, term, text, "claude_code_done")

    # Quit Claude Code
    send_keys(term, "\x03")
    qtbot.wait(500)
    send_keys(term, "\x03")
    qtbot.wait(1000)

    # Approvals depend on the user's Claude Code permission mode (e.g. auto
    # mode approves this tool call), so only the results are checked. The
    # code can only have run in the console through Spyder's MCP server.
    assert has_x()
    assert has_figure()
