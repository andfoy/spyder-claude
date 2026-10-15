# spyder-claude

A Spyder plugin to chat with Claude in a pane next to your editor, consoles
and plots. Claude can run code in your IPython consoles, inspect your
variables, look at your figures and see which file and selection you're
working on, in addition to the usual Claude Code tools (reading and editing
files, running shell commands, searching your project).

**Status:** prototype.

## Requirements

- Spyder with the MCP server plugin (`spyder/plugins/mcpserver`, currently on
  the `mcp-server-prototype` branch).
- The [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk), which
  bundles the Claude Code CLI.
- Claude credentials: an `ANTHROPIC_API_KEY`, or a logged-in Claude Code
  installation for local development.

## Installation

```bash
pip install -e .
```

Then restart Spyder and open the pane from **Window > Panes > Claude**.

## How it works

```
Claude pane (Qt) ─── AgentClient ── JSON-RPC over stdio ──► worker subprocess
     ▲                                                      Claude Agent SDK
     │ approvals, events                                          │
     │                                                            │ MCP over HTTP
     └── Spyder MCP server plugin ◄───────────────────────────────┘
         (consoles, variables, plots, editor tools)
```

- The Agent SDK runs in a worker subprocess (`spyder_claude/worker.py`), like
  Spyder's LSP and MCP servers, so its dependencies and event loop stay out
  of the GUI process.
- The agent reaches Spyder through its MCP server, the same way external
  agents do. The pane starts the server if it's not running.
- Tool calls that need permission are approved in the pane. Read-only Spyder
  tools run without asking; running code, editing files and shell commands
  ask first, with an option to always allow a tool for the session.
- The agent only uses Spyder's MCP server and project settings (including
  `CLAUDE.md` files). Your global Claude Code settings aren't loaded, so their
  allow rules can't skip the pane's approvals.

## Tests

```bash
pytest spyder_claude/tests
```

`test_live.py` starts a full Spyder window and chats with the real model, so
it only runs when `SPYDER_CLAUDE_LIVE=1` is set (it uses your credentials).
Set `SPYDER_CLAUDE_SCREENSHOTS=<dir>` to save screenshots of the pane.
