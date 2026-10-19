# spyder-claude

A Spyder plugin that runs [Claude Code](https://code.claude.com) in a pane
next to your editor, consoles and plots. Claude can run code in your IPython
consoles, inspect your variables, look at your figures and see which file and
selection you're working on, in addition to the usual Claude Code tools
(reading and editing files, running shell commands, searching your project).

**Status:** prototype.

## Requirements

- Spyder with the MCP server plugin (`spyder/plugins/mcpserver`, currently on
  the `mcp-server-prototype` branch).
- [spyder-terminal](https://github.com/spyder-ide/spyder-terminal). Version
  1.3.1 needs two small fixes to work with Spyder 6.2 (the removed
  `spyder.config.gui.is_dark_interface` and `appearance/ui_theme`).
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
                                                     │
                                                     │ MCP over HTTP
                                                     ▼
                                   Spyder MCP server plugin
                                   (consoles, variables, plots, editor)
```

- The pane is spyder-terminal's: the plugin and its main widget inherit from
  spyder-terminal's, so tabs, themes, fonts, find and shortcuts work the same
  way. Each tab runs a Claude Code session instead of a shell.
- Spyder starts its MCP server if needed and passes it to Claude Code with
  `--mcp-config`. The config file contains the server's token, so it's only
  readable by you and it's removed when Spyder closes. Your Claude Code
  configuration isn't changed.
- Claude Code replaces the shell, so a tab closes when its session ends
  (e.g. with `/exit`). Use the options menu to start new sessions in the
  current working directory, project or file directory, or to resume the
  last conversation (`claude --continue`).
- Permissions follow your Claude Code settings: Claude Code asks before
  using Spyder's tools unless you allowed them (e.g. with auto mode).
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
