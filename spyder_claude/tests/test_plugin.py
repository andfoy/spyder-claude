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
from concurrent.futures import Future
import json
import os
import os.path as osp
import shlex
import stat
import subprocess
import sys
import threading
import uuid

# Third-party imports
import pytest
from qtpy.QtWidgets import QMessageBox

from spyder.api.plugins import CallContext
from spyder.plugins.ipythonconsole.tests.conftest import SHELL_TIMEOUT

# Local imports
from spyder_claude.conversations import list_conversations
from spyder_claude.tests.conftest import (
    read_terminal,
    save_screenshot,
    send_keys,
)
from spyder_claude.tests.test_conversations import save_conversation
from spyder_claude.widgets.conversations_dialog import ConversationsDialog

pytest.importorskip("spyder_terminal")
pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="The fake CLI is a POSIX script"
)

FAKE_CLAUDE = osp.join(osp.dirname(__file__), "fake_claude.py")


@pytest.fixture(autouse=True)
def claude_config(clean_claude_env, tmp_path, monkeypatch):
    """Keep the tests away from the user's saved conversations."""
    config_dir = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


@pytest.fixture
def fake_claude(tmp_path):
    """Executable that runs fake_claude.py with this Python."""
    path = tmp_path / "claude"
    path.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_CLAUDE} \"$@\"\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def quiet_fake_claude(tmp_path):
    """Like fake_claude, but without running code in Spyder."""
    path = tmp_path / "quiet-claude"
    path.write_text(
        "#!/bin/sh\n"
        f"FAKE_CLAUDE_SKIP_CODE=1 exec {sys.executable} {FAKE_CLAUDE} \"$@\"\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def make_fake_claude(tmp_path):
    """Make fake_claude executables with extra environment variables."""

    def make(name, **env):
        path = tmp_path / name
        variables = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in env.items()
        )
        path.write_text(
            "#!/bin/sh\n"
            f"{variables} exec {sys.executable} {FAKE_CLAUDE} \"$@\"\n"
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    return make


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

    # Claude Code gets guidance about Spyder when the session starts, and
    # can't run Python from its shell
    text = read_terminal(term, qtbot)
    assert "FAKE CLAUDE CONTEXT: You're running inside Spyder" in text
    assert "FAKE CLAUDE PYTHON IN SHELL DENIED: True" in text
    assert "resumed" not in text

    # The session's id is the conversation's
    assert f"FAKE CLAUDE SESSION: {session.id}" in text
    uuid.UUID(session.id)

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

    # Resuming passes --continue. Python in the shell can be allowed.
    plugin.set_conf("keep_python_in_spyder", False)
    widget.get_action("resume_claude_code_session_action").trigger()
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 1, timeout=60000)
    term = widget.get_terms()[0]
    qtbot.waitUntil(bar.isVisible, timeout=60000)
    bar.selected_button.click()
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    text = read_terminal(term, qtbot)
    assert "FAKE CLAUDE ARGS: continue=True" in text
    assert "FAKE CLAUDE PYTHON IN SHELL DENIED: False" in text
    plugin.set_conf("keep_python_in_spyder", True)
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

    # It runs code in the session's folder, like Claude Code's shell
    assert own["cwd"] == str(tmp_path)

    # Spyder's working directory can be set to the session's folder
    workingdirectory = main_window.get_plugin("workingdir")
    assert workingdirectory.get_workdir() != str(tmp_path)
    widget.set_workdir_action.trigger()
    assert workingdirectory.get_workdir() == str(tmp_path)
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


def test_resume_conversations(
    main_window, qtbot, tmp_path, fake_claude, claude_config
):
    """Conversations are resumed in the folder where they started."""
    shell = main_window.ipyconsole.get_current_shellwidget()
    qtbot.waitUntil(
        lambda: shell._prompt_html is not None, timeout=SHELL_TIMEOUT
    )
    project = tmp_path / "project"
    project.mkdir()
    conversation_id = save_conversation(
        claude_config,
        project,
        [{"type": "ai-title", "aiTitle": "Plot the results"}],
    )

    plugin = main_window.get_plugin("claude_code")
    plugin.set_conf("claude_command", fake_claude)
    widget = plugin.get_widget()
    mcp_server = main_window.get_plugin("mcp_server")
    bar = widget.question_bar
    qtbot.waitUntil(widget.server_is_ready, timeout=60000)

    # The dialog lists saved conversations from every folder
    dialog = ConversationsDialog(list_conversations(), parent=widget)
    assert dialog.get_conversation().id == conversation_id
    assert dialog.tree.topLevelItem(0).text(1) == str(project)
    dialog.search.setText("no such thing")
    assert dialog.get_conversation() is None
    dialog.search.setText("plot")
    assert dialog.get_conversation().id == conversation_id

    # Resuming it starts Claude Code in its folder, even if it's not
    # Spyder's working directory and another project is open
    other_project = tmp_path / "other"
    other_project.mkdir()
    widget.set_project_path(str(other_project))
    plugin.resume_conversation(conversation_id)
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 1, timeout=60000)
    term = widget.get_terms()[0]
    qtbot.waitUntil(bar.isVisible, timeout=60000)
    bar.selected_button.click()
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    text = read_terminal(term, qtbot)
    assert f"FAKE CLAUDE RESUME: {conversation_id}" in text
    assert f"FAKE CLAUDE CWD: {project}" in text

    # The session is the conversation, also for Spyder's tools
    session = widget.get_session(term)
    assert session.id == conversation_id
    assert "Plot the results" in widget.tabwidget.tabToolTip(0)
    assert str(project) in widget.tabwidget.tabToolTip(0)
    assert [c.id for c in mcp_server.get_clients()] == [conversation_id]

    # Resuming it again shows its tab instead of a second session
    plugin.resume_conversation(conversation_id)
    qtbot.wait(1000)
    assert len(widget.get_terms()) == 1
    dialog = ConversationsDialog(
        list_conversations(), {conversation_id}, parent=widget
    )
    assert "(open)" in dialog.tree.topLevelItem(0).text(0)

    send_keys(term, "\r")
    qtbot.waitUntil(lambda: not widget.get_terms(), timeout=20000)
    widget.unset_project_path()

    # Sessions open when Spyder closed are restored, skipping the ones that
    # have nothing to resume
    plugin.set_conf(
        "open_sessions",
        [
            {"id": str(uuid.uuid4()), "cwd": str(tmp_path)},
            {"id": conversation_id, "cwd": str(project)},
        ],
    )
    plugin.restore_sessions()
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 1, timeout=60000)
    term = widget.get_terms()[0]
    qtbot.waitUntil(bar.isVisible, timeout=60000)
    bar.selected_button.click()
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    text = read_terminal(term, qtbot)
    assert f"FAKE CLAUDE RESUME: {conversation_id}" in text
    assert f"FAKE CLAUDE CWD: {project}" in text

    send_keys(term, "\r")
    qtbot.waitUntil(lambda: not widget.get_terms(), timeout=20000)


def test_project_sessions(
    main_window, qtbot, tmp_path, quiet_fake_claude, claude_config,
    monkeypatch,
):
    """Projects resume the conversations they had open when closed."""
    shell = main_window.ipyconsole.get_current_shellwidget()
    qtbot.waitUntil(
        lambda: shell._prompt_html is not None, timeout=SHELL_TIMEOUT
    )
    project = tmp_path / "project"
    (project / "analysis").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    in_project = save_conversation(
        claude_config,
        project / "analysis",
        [{"type": "ai-title", "aiTitle": "Project work"}],
    )
    outside = save_conversation(claude_config, elsewhere)

    plugin = main_window.get_plugin("claude_code")
    plugin.set_conf("claude_command", quiet_fake_claude)
    widget = plugin.get_widget()
    projects = main_window.get_plugin("project_explorer")
    qtbot.waitUntil(widget.server_is_ready, timeout=60000)

    def open_ids():
        return sorted(session.id for session in widget.get_sessions())

    plugin.resume_conversation(in_project)
    plugin.resume_conversation(outside)
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 2, timeout=60000)
    for term in widget.get_terms():
        wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    projects.open_project(path=str(project))
    qtbot.wait(1000)

    # The resume dialog shows the project's conversations by default
    dialog = ConversationsDialog(
        list_conversations(), project=str(project), parent=widget
    )
    visible = [
        dialog.tree.topLevelItem(i).text(1)
        for i in range(dialog.tree.topLevelItemCount())
        if not dialog.tree.topLevelItem(i).isHidden()
    ]
    assert visible == [str(project / "analysis")]
    dialog.project_only.setChecked(False)
    assert dialog.tree.topLevelItem(1).isHidden() is False

    # Closing the project closes its sessions, but not the others
    projects.close_project()
    qtbot.waitUntil(lambda: open_ids() == [outside], timeout=20000)

    # Opening it again resumes them, telling Claude the consoles restarted
    projects.open_project(path=str(project))
    qtbot.waitUntil(
        lambda: open_ids() == sorted([in_project, outside]), timeout=60000
    )
    term = widget.get_term_for_session(in_project)
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    text = read_terminal(term, qtbot)
    assert f"FAKE CLAUDE CWD: {project / 'analysis'}" in text
    assert "consoles were started after it last ran" in " ".join(
        text.split()
    )

    # Sessions using Spyder are only closed if the user agrees
    mcp_server = main_window.get_plugin("mcp_server")
    mcp_server._dispatcher._running[in_project] = 1
    asked = []

    def answer(*args):
        asked.append(args)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", answer)
    projects.close_project()
    qtbot.wait(1000)
    mcp_server._dispatcher._running[in_project] = 0
    assert len(asked) == 1
    assert open_ids() == sorted([in_project, outside])

    # When Spyder closes, the project's sessions are saved with it.
    # Restarting the consoles again so soon can crash Spyder's console in
    # tests (a kernel thread is destroyed while running), and it isn't
    # needed here.
    projects.open_project(path=str(project), restart_console=False)
    qtbot.wait(1000)
    plugin.save_open_sessions()
    assert [s["id"] for s in plugin.get_conf("open_sessions")] == [outside]
    project_sessions = plugin._get_saved_project_sessions(str(project))
    assert [s["id"] for s in project_sessions] == [in_project]


def test_console_question_in_claude_code(
    main_window, qtbot, tmp_path, make_fake_claude
):
    """
    When Claude Code can show forms, it asks which console to use itself,
    instead of a bar in Spyder.
    """
    shell = main_window.ipyconsole.get_current_shellwidget()
    qtbot.waitUntil(
        lambda: shell._prompt_html is not None, timeout=SHELL_TIMEOUT
    )
    plugin = main_window.get_plugin("claude_code")
    widget = plugin.get_widget()
    qtbot.waitUntil(widget.server_is_ready, timeout=60000)

    plugin.set_conf(
        "claude_command",
        make_fake_claude("asking-claude", FAKE_CLAUDE_ELICIT="new console"),
    )
    plugin.new_session(path=str(tmp_path))
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 1, timeout=60000)
    term = widget.get_terms()[0]
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    text = " ".join(read_terminal(term, qtbot).split())
    # Like Claude Code, it uses the 2026-07-28 protocol, where the question
    # comes back as the call's result and the call is made again with the
    # answer
    assert "FAKE CLAUDE PROTOCOL: 2026-07-28" in text
    assert "FAKE CLAUDE ASKED: Claude 1 wants to run code in Spyder" in text
    assert "In a new console for this session" in text
    assert "FAKE CLAUDE DEFAULT: In a new console for this session" in text
    assert "FAKE CLAUDE EXECUTED, error: False" in text
    assert not widget.question_bar.isVisible()

    # The answer is the session's setting, like in Spyder's bar
    session = widget.get_session(term)
    assert session.console_mode == "new"
    assert session.console_id is not None
    assert not shell.is_defined("fake_claude_value")
    send_keys(term, "\r")
    qtbot.waitUntil(lambda: not widget.get_terms(), timeout=20000)

    # Declining doesn't run the code
    plugin.set_conf(
        "claude_command",
        make_fake_claude("declining-claude", FAKE_CLAUDE_ELICIT="decline"),
    )
    plugin.new_session(path=str(tmp_path))
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 1, timeout=60000)
    term = widget.get_terms()[0]
    wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    assert "FAKE CLAUDE EXECUTED, error: True" in read_terminal(term, qtbot)
    assert widget.get_session(term).console_mode == "ask"
    send_keys(term, "\r")
    qtbot.waitUntil(lambda: not widget.get_terms(), timeout=20000)


class AnsweringContext(CallContext):
    """Call context of a client whose user answers with ``answer``."""

    can_ask_user = True

    def __init__(self, answer):
        self.answer = answer

    def ask_user(self, message, choices, title="", default=None):
        future = Future()
        future.set_result(
            next(choice for choice in choices if self.answer in choice)
        )
        return future


def run_session_hook(qtbot, session, event_name, **event):
    """
    Run a session's hooks for an event like Claude Code, and get stdout.

    They run in another thread, like in Claude Code's process, since they
    may need Spyder's main thread (e.g. to get notes from its MCP server).
    """
    with open(session.config_path.replace("mcp-", "settings-")) as f:
        settings = json.load(f)
    commands = [
        hook["command"]
        for group in settings["hooks"].get(event_name, [])
        for hook in group["hooks"]
    ]
    outputs = []

    def run():
        for command in commands:
            outputs.append(
                subprocess.run(
                    command,
                    shell=True,
                    input=json.dumps({"hook_event_name": event_name, **event}),
                    capture_output=True,
                    text=True,
                    timeout=30,
                ).stdout
            )

    thread = threading.Thread(target=run)
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=30000)
    return "".join(outputs)


def test_session_switches_project(
    main_window, qtbot, tmp_path, quiet_fake_claude, claude_config
):
    """
    A session that opens another project keeps working, and the other
    sessions are told.
    """
    shell = main_window.ipyconsole.get_current_shellwidget()
    qtbot.waitUntil(
        lambda: shell._prompt_html is not None, timeout=SHELL_TIMEOUT
    )
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    in_project = save_conversation(claude_config, first)
    outside = save_conversation(claude_config, elsewhere)

    plugin = main_window.get_plugin("claude_code")
    plugin.set_conf("claude_command", quiet_fake_claude)
    widget = plugin.get_widget()
    mcp_server = main_window.get_plugin("mcp_server")
    projects = main_window.get_plugin("project_explorer")
    qtbot.waitUntil(widget.server_is_ready, timeout=60000)
    projects.open_project(path=str(first), restart_console=False)

    plugin.resume_conversation(in_project)
    plugin.resume_conversation(outside)
    qtbot.waitUntil(lambda: len(widget.get_terms()) == 2, timeout=60000)
    for term in widget.get_terms():
        wait_for_text(term, qtbot, "FAKE CLAUDE DONE")
    sessions = {s.id: s for s in widget.get_sessions()}

    # Nothing happened yet
    assert run_session_hook(qtbot, sessions[outside], "UserPromptSubmit") == ""

    # The session of the first project opens the second one, after the user
    # confirms it in Claude Code
    results = []
    mcp_server._dispatcher.call(
        "open_project",
        {"path": str(second)},
        {"id": in_project, "name": sessions[in_project].name},
        lambda content, is_error: results.append((content, is_error)),
        AnsweringContext("Open"),
    )
    qtbot.waitUntil(lambda: bool(results), timeout=SHELL_TIMEOUT)
    assert not results[0][1], results
    assert projects.get_active_project_path() == str(second)

    # It keeps working, although its conversation is in the first project
    qtbot.wait(1000)
    assert sorted(s.id for s in widget.get_sessions()) == sorted(
        [in_project, outside]
    )

    # The other session is told when the user sends it a prompt, once
    output = run_session_hook(qtbot, sessions[outside], "UserPromptSubmit")
    name = sessions[in_project].name
    assert f"{name} opened the project {second}" in output
    assert f"{name} closed the project {first}" in output
    for session_id in [outside, in_project]:
        assert (
            run_session_hook(qtbot, sessions[session_id], "UserPromptSubmit")
            == ""
        )

    for term in widget.get_terms():
        send_keys(term, "\r")
    qtbot.waitUntil(lambda: not widget.get_terms(), timeout=20000)
