# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Claude Code hook that keeps Python in Spyder's consoles.

It runs before each shell command Claude Code runs (a ``PreToolUse`` hook for
its Bash tool) and denies the ones that run Python, telling Claude to use
Spyder's consoles instead, where the user sees the code and its results stay
in their session.

This is a guardrail for a cooperative agent, not a sandbox: it recognizes
the usual ways of running Python, not every possible one.

It only uses the standard library, since it runs as a script with Spyder's
Python for every shell command::

    python -I python_guard.py [--allow COMMAND ...] < event.json
"""

# Standard library imports
import argparse
import json
import os.path as osp
import re
import shlex
import sys

# Interpreters, e.g. python, python3.12, pythonw.exe or ipython
INTERPRETER = re.compile(r"^(i?python|pythonw|py)(\d+(\.\d+)*)?(\.exe)?$")

# Tools that run Python code or install packages for it
PYTHON_TOOLS = {
    "pytest", "py.test", "pip", "pip3", "jupyter", "jupyter-execute",
    "jupyter-nbconvert", "uvx", "pipx",
}

# Tools that run another command in an environment, e.g. ``uv run python``
RUNNERS = {
    "uv": {"run", "pip"},
    "conda": {"run"},
    "mamba": {"run"},
    "micromamba": {"run"},
    "pixi": {"run", "exec"},
    "poetry": {"run"},
    "pipenv": {"run"},
    "hatch": {"run"},
    "pdm": {"run"},
}

# Commands that run the command after their options, with the options that
# take a value
WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"},
    "doas": {"-u", "-C"},
    "time": {"-f", "-o"},
    "nice": {"-n"},
    "nohup": set(),
    "exec": {"-a"},
    "command": set(),
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"},
    "caffeinate": {"-t", "-w"},
    "taskset": set(),
}

SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh"}

OPERATORS = {";", "&", "&&", "|", "||", "(", ")", "{", "}", "!", "|&"}

REASON = (
    "Spyder: don't run Python from the shell. Run it in Spyder's IPython "
    "consoles with the spyder MCP server's tools, so the user sees the code "
    "and its results stay in their session: execute_code for code (also "
    "for commands, e.g. `!pytest -q tests`, or `%pip install package` to "
    "install packages in the console's environment), run_file for scripts, "
    "and new_console for long or risky work. The user can allow running "
    "Python from the shell in the Claude Code pane's options."
)


def simple_commands(command: str) -> list[list[str]]:
    """
    Split a shell command line into simple commands (words), including the
    ones in subshells and command substitutions.
    """
    # Command substitutions and line breaks separate commands too
    command = command.replace("$(", " ; ").replace("`", " ; ")
    command = command.replace("\n", " ; ")

    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes: fall back to splitting on whitespace
        tokens = command.split()

    commands, current = [], []
    for token in tokens:
        if token in OPERATORS or set(token) <= set(";&|(){}<>"):
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def _strip_wrappers(words: list[str]) -> list[str]:
    """Remove variable assignments and wrappers like ``env`` or ``sudo``."""
    while words:
        first = words[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", first):
            words = words[1:]
        elif osp.basename(first) in WRAPPERS:
            with_value = WRAPPERS[osp.basename(first)]
            words = words[1:]
            # Their options and arguments, e.g. ``-u root`` or ``timeout 10``
            while words and (
                words[0].startswith("-")
                or "=" in words[0]
                or re.match(r"^\d+(\.\d+)?[smhd]?$", words[0])
            ):
                option, words = words[0], words[1:]
                if option in with_value and words:
                    words = words[1:]
        else:
            break
    return words


def runs_python(words: list[str], allowed: set[str], depth: int = 0) -> bool:
    """Whether a simple command runs Python."""
    words = _strip_wrappers(words)
    if not words or depth > 5:
        return False

    name = osp.basename(words[0])
    args = words[1:]
    if name in allowed:
        return False

    if INTERPRETER.match(name):
        # Asking for the version doesn't run code
        return not (args and set(args) <= {"--version", "-V", "-VV"})

    if name in PYTHON_TOOLS or name.endswith(".py"):
        return True

    if name in RUNNERS and args and args[0] in RUNNERS[name]:
        return True

    if name in SHELLS and "-c" in args:
        index = args.index("-c")
        if index + 1 < len(args):
            return any(
                runs_python(inner, allowed, depth + 1)
                for inner in simple_commands(args[index + 1])
            )
    return False


def check(command: str, allowed: set[str] = frozenset()) -> bool:
    """Whether a shell command line runs Python."""
    return any(
        runs_python(words, set(allowed)) for words in simple_commands(command)
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="Command to allow anyway, e.g. pytest",
    )
    options = parser.parse_args(argv)

    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("tool_name") != "Bash":
        return 0

    command = (event.get("tool_input") or {}).get("command") or ""
    if check(command, set(options.allow)):
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": REASON,
                }
            },
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
