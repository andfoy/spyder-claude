# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Claude Code hook that tells Claude how to work in Spyder.

It runs when a session starts or a conversation is resumed (a
``SessionStart`` hook), and prints context that Claude Code adds to the
conversation: how to use Spyder's consoles and Editor and, for resumed
conversations, the state of Spyder's consoles. Unlike
``--append-system-prompt``, which is fixed with the conversation's first
request, this also reaches conversations started outside Spyder and stays
current when they're resumed.

It only uses the standard library, since it runs as a script with Spyder's
Python::

    python -I session_context.py CONTEXT_FILE < event.json

where ``CONTEXT_FILE`` is a JSON file with the ``guidance`` and, for
resumed conversations, a ``resume_note``.
"""

# Standard library imports
import json
import sys

GUIDANCE = """\
You're running inside Spyder, a scientific Python IDE, connected to it \
through the "spyder" MCP server. To work with the user's session:
- Run Python in Spyder's IPython consoles with the execute_code, run_file \
and run_cell tools, not in the shell, so the user sees the code and its \
results stay in their session.{guard} Use `!command` in execute_code for \
commands like pytest, `%pip install` for packages (it installs into the \
console's environment), and new_console for long or risky work.
- Each console has its own working directory (see list_consoles), which can \
differ from yours: use absolute paths, or check it before relying on \
relative paths.
- For files open in Spyder's Editor (see get_editor_state), read them with \
read_editor_file and change them with edit_file: they include the user's \
unsaved changes, and the user can undo your edits. Use your own tools for \
other files.
- Look at the user's data and plots with list_variables, inspect_variable \
and get_figure instead of running code again."""

GUARD = " Running Python from the shell is blocked."


def make_guidance(python_blocked: bool) -> str:
    """Guidance about working in Spyder."""
    return GUIDANCE.format(guard=GUARD if python_blocked else "")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        with open(argv[0]) as f:
            context = json.load(f)
        event = json.load(sys.stdin)
    except (IndexError, OSError, ValueError):
        return 0

    parts = [context.get("guidance", "")]
    if event.get("source") in ("resume", "fork"):
        parts.append(context.get("resume_note", ""))
    print("\n\n".join(part for part in parts if part))
    return 0


if __name__ == "__main__":
    sys.exit(main())
