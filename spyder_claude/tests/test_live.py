# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
End-to-end test with the real model, inside a full Spyder window.

It uses your Claude credentials, so it only runs when SPYDER_CLAUDE_LIVE is
set. Set SPYDER_CLAUDE_SCREENSHOTS to a directory to save screenshots.
"""

# Standard library imports
import os

# Third-party imports
import pytest
from qtpy.QtCore import Qt

from spyder.api.plugin_registration.registry import PLUGIN_REGISTRY
from spyder.config.manager import CONF
from spyder.plugins.ipythonconsole.tests.conftest import SHELL_TIMEOUT

# Local imports
from spyder_claude.widgets.chat import (
    AssistantMessageWidget,
    ImageView,
    ToolCallWidget,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("SPYDER_CLAUDE_LIVE"),
    reason="Set SPYDER_CLAUDE_LIVE to run tests against the real model",
)

PROMPT = (
    "In my IPython console, create a numpy array called x with 100 points "
    "between 0 and 2*pi and plot sin(x) with matplotlib. Then describe the "
    "plot in one short sentence."
)


@pytest.fixture
def main_window(qtbot):
    """Start a full Spyder window."""
    # Same setup as Spyder's own main_window fixture, which can't be
    # imported without QtWebEngine
    CONF.set("tours", "show_tour_message", False)
    CONF.set("main", "single_instance", False)
    CONF.set("ipython_console", "pylab/backend", "inline")
    CONF.set("ipython_console", "pylab/inline/figure_format", "png")
    CONF.set("plots", "mute_inline_plotting", False)
    for provider in ["lsp", "fallback", "snippets"]:
        CONF.set("completions", ("enabled_providers", provider), False)

    from spyder.app import start
    window = start.main()
    yield window

    window.closing(close_immediately=True)
    window.close()
    CONF.reset_to_defaults(notification=False)
    CONF.reset_manager()
    PLUGIN_REGISTRY.reset()


def save_screenshot(widget, name):
    directory = os.environ.get("SPYDER_CLAUDE_SCREENSHOTS")
    if directory:
        widget.grab().save(os.path.join(directory, name))


def test_plot_from_chat(main_window, qtbot):
    main_window.resize(1500, 950)
    shell = main_window.ipyconsole.get_current_shellwidget()
    qtbot.waitUntil(
        lambda: shell._prompt_html is not None, timeout=SHELL_TIMEOUT
    )

    plugin = main_window.get_plugin("claude_agent")
    widget = plugin.get_widget()
    plugin.switch_to_plugin()

    widget.input.setPlainText(PROMPT)
    qtbot.keyClick(widget.input, Qt.Key_Return)

    # Approve tool calls as they come until the turn finishes
    approved = []
    screenshot_taken = []

    def turn_finished():
        for item in widget.chat_view.items():
            if (
                isinstance(item, ToolCallWidget)
                and not item.approval_widget.isHidden()
            ):
                if not screenshot_taken:
                    save_screenshot(widget, "claude_pane_approval.png")
                    screenshot_taken.append(True)
                approved.append(item.name)
                item.allow_button.click()
        return not widget._busy

    qtbot.waitUntil(turn_finished, timeout=300000)
    qtbot.wait(500)
    save_screenshot(widget, "claude_pane.png")
    save_screenshot(main_window, "spyder_window.png")

    # Running code needed approval, and it ran in the console
    assert "mcp__spyder__execute_code" in approved
    assert shell.get_value("x").shape == (100,)

    # The figure came back to the chat and is in the Plots pane
    tool_calls = [
        item for item in widget.chat_view.items()
        if isinstance(item, ToolCallWidget)
        and item.name == "mcp__spyder__execute_code"
    ]
    images = [
        tool_call.result_layout.itemAt(i).widget()
        for tool_call in tool_calls
        for i in range(tool_call.result_layout.count())
        if isinstance(tool_call.result_layout.itemAt(i).widget(), ImageView)
    ]
    assert images and not images[0].pixmap.isNull()

    # Nothing in the conversation is wider than the pane
    viewport_width = widget.chat_view.viewport().width()
    assert widget.chat_view.widget().width() <= viewport_width

    plots = main_window.get_plugin("plots").get_widget()
    figure_browser = plots.get_widget_for_shellwidget(shell)
    assert figure_browser.thumbnails_sb._thumbnails

    # Claude answered with text at the end
    messages = [
        item for item in widget.chat_view.items()
        if isinstance(item, AssistantMessageWidget)
    ]
    assert messages and messages[-1].text_.strip()
