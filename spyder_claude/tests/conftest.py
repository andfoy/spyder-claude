# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Test configuration."""

# Standard library imports
import json
import os

# Make Spyder use a temporary configuration directory during tests
os.environ["SPYDER_PYTEST"] = "True"

# Third-party imports
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def clean_claude_env(monkeypatch):
    """
    Remove variables set by a parent Claude Code session (e.g. when tests
    are run by Claude Code), which change how the CLI behaves.
    """
    for name in list(os.environ):
        if name.startswith("CLAUDE"):
            monkeypatch.delenv(name)


@pytest.fixture
def main_window(qtbot):
    """Start a full Spyder window."""
    from spyder.api.plugin_registration.registry import PLUGIN_REGISTRY
    from spyder.config.manager import CONF

    # Same setup as Spyder's own main_window fixture
    CONF.set("tours", "show_tour_message", False)
    CONF.set("main", "single_instance", False)
    CONF.set("ipython_console", "pylab/backend", "inline")
    CONF.set("ipython_console", "pylab/inline/figure_format", "png")
    CONF.set("plots", "mute_inline_plotting", False)
    for provider in ["lsp", "fallback", "snippets"]:
        CONF.set("completions", ("enabled_providers", provider), False)

    from spyder.app import start
    window = start.main()
    window.resize(1500, 950)
    yield window

    window.closing(close_immediately=True)
    window.close()
    CONF.reset_to_defaults(notification=False)
    CONF.reset_manager()
    PLUGIN_REGISTRY.reset()


def read_terminal(term, qtbot) -> str:
    """Text in a terminal's buffer, including the scrollback."""
    result = []
    term.view.document.runJavaScript(
        "spyder_terminal.default.getTerminalLines()", result.append
    )
    qtbot.waitUntil(lambda: bool(result), timeout=5000)
    return result[0] or ""


def send_keys(term, data: str) -> None:
    """Send raw input to a terminal, as if typed by the user."""
    term.view.document.runJavaScript(f"window.term.input({json.dumps(data)})")


def save_screenshot(widget, name: str) -> None:
    """Save a screenshot if SPYDER_CLAUDE_SCREENSHOTS is set."""
    directory = os.environ.get("SPYDER_CLAUDE_SCREENSHOTS")
    if directory:
        widget.grab().save(os.path.join(directory, name))
