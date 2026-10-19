# spyder-claude

[![Tests](https://github.com/andfoy/spyder-claude/actions/workflows/tests.yml/badge.svg)](https://github.com/andfoy/spyder-claude/actions/workflows/tests.yml)

A Spyder plugin that runs [Claude Code](https://code.claude.com) in a pane
next to your editor, consoles and plots. Claude can run code in your IPython
consoles, inspect your variables, look at your figures, debug and profile
code, and edit the files open in the Editor, in addition to the usual Claude
Code tools (reading and editing files, running shell commands, searching your
project).

**Status:** prototype. It needs versions of Spyder and spyder-terminal that
aren't released yet (see [Requirements](#requirements)).

## Features

- **Claude Code in Spyder.** Each tab of the pane runs a Claude Code session
  in Spyder's terminal, connected to Spyder through its MCP server. Your
  Claude Code configuration isn't changed.
- **Your consoles.** The first time a session runs code, Claude Code asks
  which IPython console to use: the one selected in Spyder, a new console for
  the session, or another one. You can change it with the **Console** button
  of the pane. Several sessions can share a console: their code is queued,
  your own runs go first, and each session can only interrupt or debug the
  code it started.
- **Python stays in Spyder.** Claude runs Python in Spyder's consoles instead
  of its shell, so you see the code and its results stay in your session.
  You can turn this off in the pane's options, or allow some commands
  (e.g. `pytest`) with the `allowed_python_commands` option.
- **Your editor.** Claude reads the files open in the Editor with your
  unsaved changes and edits them as undoable changes. Changes it makes on
  disk show up in the Editor right away.
- **Conversations.** Sessions open when Spyder closes are restored, and you
  can resume any previous conversation in the folder where it started.
- **Projects.** Sessions started in a project close and come back with it.
  Claude can open and close projects after you confirm.
- **Aware of Spyder.** Sessions are told what happens in Spyder that affects
  them: e.g. another session opened a project, or the console they use was
  restarted or its kernel died.
- **Outside Spyder.** The options menu copies a command that connects Claude
  Code sessions started in your own terminal to Spyder.

## Requirements

- Spyder 6.2 with its MCP server plugin. Until it's released, use the
  [`mcp-server-prototype`](https://github.com/andfoy/spyder/tree/mcp-server-prototype)
  branch.
- spyder-terminal with support for Spyder 6.2 and xterm.js 6. Until it's
  released, use the
  [`spyder-6.2-xterm6`](https://github.com/andfoy/spyder-terminal/tree/spyder-6.2-xterm6)
  branch.
- The Claude Code CLI, installed and on your `PATH`. Sign in with your Claude
  account or an API key the first time it starts (or with `/login`).

## Installation

```bash
pip install -e .
```

Then restart Spyder and open the pane from **Window > Panes > Claude Code**.
A session starts the first time the pane is shown.

## How it works

```
Claude Code pane (spyder-terminal) ── runs ──► claude --mcp-config <file>
                                                      --settings <file>
                                                     │
                                                     │ MCP over HTTP
                                                     ▼
                                   Spyder MCP server plugin
                     (consoles, debugger, editor, variables, plots, ...)
```

- The pane is spyder-terminal's: the plugin and its main widget inherit from
  spyder-terminal's, so tabs, themes, fonts, find and shortcuts work the same
  way. Each tab runs a Claude Code session instead of a shell.
- Spyder starts its MCP server if needed and passes it to Claude Code with
  `--mcp-config`. Each session sends its own id, so Spyder tells sessions
  apart. The config files contain the server's token, so they're only
  readable by you, and they're removed when Spyder closes.
- `--settings` adds hooks to the session: one that tells Claude how to work
  in Spyder and what happened in Spyder since its last prompt, and one that
  keeps Python in Spyder's consoles. They add to your own settings.
- Claude Code replaces the shell, so a tab closes when its session ends
  (e.g. with `/exit`).
- Permissions follow your Claude Code settings: Claude Code asks before
  using Spyder's tools unless you allowed them.
- The executable can be changed with the `claude_command` option.

## Tests

```bash
QT_API=pyqt5 PYTEST_QT_API=pyqt5 pytest spyder_claude/tests
```

The tests need a Qt binding with QtWebEngine. `test_plugin.py` replaces
Claude Code with `tests/fake_claude.py`, which connects to Spyder's MCP server
the same way, so it doesn't need credentials.

`test_live_terminal.py` runs the real Claude Code with your credentials, so
it only runs when `SPYDER_CLAUDE_LIVE=1` is set. Set
`SPYDER_CLAUDE_SCREENSHOTS=<dir>` to save screenshots.

QtWebEngine may crash when the test process exits, which can hide pytest's
summary; use `--junitxml` to get the results.

## License

MIT, see [LICENSE.txt](LICENSE.txt).
