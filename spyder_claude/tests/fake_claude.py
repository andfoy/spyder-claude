# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Stand-in for the Claude Code CLI, used by the plugin tests.

Like Claude Code, it reads the MCP server from ``--mcp-config`` and connects
to it. Then it lists the tools, runs some code in the console and waits for
Enter before exiting.
"""

# Standard library imports
import argparse
import asyncio
import json
import os
import subprocess

# Third-party imports
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

CODE = "fake_claude_value = 6 * 7"


async def use_spyder(server: dict) -> None:
    async with httpx2.AsyncClient(
        headers=server["headers"], timeout=60
    ) as http:
        async with streamable_http_client(
            server["url"], http_client=http
        ) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = sorted(tool.name for tool in tools.tools)
                print("FAKE CLAUDE TOOLS:", " ".join(names))

                result = await session.call_tool(
                    "execute_code", {"code": CODE}
                )
                print("FAKE CLAUDE EXECUTED, error:", result.is_error)


def run_hooks(settings_path: str, event_name: str, event: dict) -> list:
    """Run the command hooks of an event like Claude Code, and get stdout."""
    with open(settings_path) as f:
        settings = json.load(f)
    outputs = []
    for group in settings.get("hooks", {}).get(event_name, []):
        for hook in group["hooks"]:
            result = subprocess.run(
                hook["command"],
                shell=True,
                input=json.dumps({"hook_event_name": event_name, **event}),
                capture_output=True,
                text=True,
                timeout=hook.get("timeout", 600),
            )
            outputs.append(result.stdout)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-config", required=True)
    parser.add_argument("--continue", action="store_true")
    parser.add_argument("--resume")
    parser.add_argument("--session-id")
    parser.add_argument("--settings")
    args = parser.parse_args()

    with open(args.mcp_config) as f:
        server = json.load(f)["mcpServers"]["spyder"]

    print("FAKE CLAUDE ARGS: continue={}".format(getattr(args, "continue")))
    print(f"FAKE CLAUDE RESUME: {args.resume}")
    print(f"FAKE CLAUDE SESSION: {args.session_id}")
    print(f"FAKE CLAUDE CWD: {os.getcwd()}")

    # The hooks Claude Code would run when the session starts and before a
    # shell command that runs Python
    if args.settings:
        source = "resume" if args.resume else "startup"
        start = {"source": source}
        for output in run_hooks(args.settings, "SessionStart", start):
            print(f"FAKE CLAUDE CONTEXT: {output}")
        bash = {"tool_name": "Bash", "tool_input": {"command": "python x.py"}}
        decisions = run_hooks(args.settings, "PreToolUse", bash)
        denied = any("deny" in output for output in decisions)
        print(f"FAKE CLAUDE PYTHON IN SHELL DENIED: {denied}")
    if not os.environ.get("FAKE_CLAUDE_SKIP_CODE"):
        asyncio.run(use_spyder(server))
    print("FAKE CLAUDE DONE")
    input()


if __name__ == "__main__":
    main()
