# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Agent worker subprocess.

Runs a Claude Agent SDK session connected to Spyder's MCP server and talks
to Spyder through a JSON-RPC bridge on its stdio. Keeping the SDK in its own
process keeps its dependencies and event loop out of Spyder, like it's done
for the LSP and MCP servers.

The process exits when its stdin is closed, i.e. when Spyder goes away.
"""

# Standard library imports
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

# Third-party imports
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import StreamEvent
from spyder.plugins.mcpserver.bridge import JsonRpcPeer

# Local imports
from spyder_claude.protocol import AgentMethods, EventTypes, MCP_SERVER_NAME

logger = logging.getLogger("spyder_claude.worker")

SYSTEM_PROMPT_APPEND = """\
You are running inside the Spyder IDE, chatting with the user in a pane next
to their editor, IPython consoles and plots.

Use the mcp__spyder__* tools to work with the user's live Spyder session:
run code in their IPython consoles (state persists between calls and the
user sees the code and its output), inspect their variables, look at the
figures in the Plots pane and find out which file and selection they're
looking at in the Editor. When the user says "this", "the selection" or
"my data", check the editor state or the console variables first.

Prefer the console tools over Bash for interactive and data work. Use Bash
for shell tasks such as git, pip or running test suites.
"""


# ---- Message conversion
# -----------------------------------------------------------------------------
def normalize_content(content: Any) -> list[dict]:
    """Convert tool result content to text and image dicts."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]

    blocks = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            blocks.append({"type": "text", "text": item.get("text", "")})
        elif item.get("type") == "image":
            # Images can come in the API shape or in the MCP shape
            source = item.get("source") or {}
            data = source.get("data", item.get("data"))
            mime_type = source.get("media_type", item.get("mimeType"))
            if data:
                blocks.append(
                    {"type": "image", "data": data, "mimeType": mime_type}
                )
    return blocks


def message_to_events(message: Any) -> list[dict]:
    """Convert an Agent SDK message to the events sent to Spyder."""
    events = []

    if isinstance(message, StreamEvent):
        # Only stream the main agent's text, not subagents'
        event = message.event
        if (
            message.parent_tool_use_id is None
            and event.get("type") == "content_block_delta"
            and event.get("delta", {}).get("type") == "text_delta"
        ):
            events.append(
                {"type": EventTypes.TextDelta, "text": event["delta"]["text"]}
            )

    elif isinstance(message, AssistantMessage):
        if message.parent_tool_use_id is not None:
            return events
        for block in message.content:
            if isinstance(block, TextBlock):
                events.append({"type": EventTypes.Text, "text": block.text})
            elif isinstance(block, ToolUseBlock):
                events.append(
                    {
                        "type": EventTypes.ToolUse,
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        if message.error:
            events.append(
                {"type": EventTypes.Error, "message": str(message.error)}
            )

    elif isinstance(message, UserMessage):
        if message.parent_tool_use_id is not None or isinstance(
            message.content, str
        ):
            return events
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                events.append(
                    {
                        "type": EventTypes.ToolResult,
                        "id": block.tool_use_id,
                        "isError": bool(block.is_error),
                        "content": normalize_content(block.content),
                    }
                )

    elif isinstance(message, SystemMessage):
        if message.subtype == "init":
            data = message.data
            events.append(
                {
                    "type": EventTypes.Init,
                    "model": data.get("model"),
                    "sessionId": data.get("session_id"),
                    "mcpServers": data.get("mcp_servers", []),
                }
            )

    elif isinstance(message, ResultMessage):
        events.append(
            {
                "type": EventTypes.Result,
                "isError": message.is_error,
                "subtype": message.subtype,
                "terminalReason": message.terminal_reason,
                "costUsd": message.total_cost_usd,
                "durationMs": message.duration_ms,
                "numTurns": message.num_turns,
                "errors": message.errors or [],
            }
        )

    return events


# ---- Worker
# -----------------------------------------------------------------------------
class AgentWorker:
    """Run an Agent SDK session driven by Spyder."""

    def __init__(self, peer: JsonRpcPeer | None = None) -> None:
        handlers = {
            AgentMethods.Start: self.start,
            AgentMethods.Send: self.send,
            AgentMethods.Interrupt: self.interrupt,
        }
        if peer is None:
            peer = JsonRpcPeer.from_stdio(handlers)
        else:
            peer._handlers.update(handlers)
        self.peer = peer

        self._commands: asyncio.Queue = asyncio.Queue()
        self._session: asyncio.Task | None = None

    # ---- Bridge handlers
    async def start(self, params: dict) -> dict:
        """Connect to Claude with the options sent by Spyder."""
        if self._session is not None:
            raise RuntimeError("The session was already started")

        connected = asyncio.get_running_loop().create_future()
        self._session = asyncio.create_task(
            self._run_session(self.build_options(params), connected)
        )
        await connected
        return {}

    async def send(self, params: dict) -> dict:
        """Send a user message. Events for the turn are sent as they come."""
        self._check_started()
        await self._commands.put(("send", params["text"]))
        return {}

    async def interrupt(self, params: dict) -> dict:
        """Interrupt the current turn."""
        self._check_started()
        await self._commands.put(("interrupt", None))
        return {}

    # ---- Session
    def build_options(self, params: dict) -> ClaudeAgentOptions:
        mcp = params["mcp"]
        return ClaudeAgentOptions(
            cwd=params.get("cwd"),
            model=params.get("model") or None,
            mcp_servers={
                MCP_SERVER_NAME: {
                    "type": "http",
                    "url": mcp["url"],
                    "headers": mcp.get("headers", {}),
                }
            },
            # Only use Spyder's MCP server, and don't load the user's global
            # settings, whose allow rules would skip Spyder's approvals.
            # Project settings are kept to load CLAUDE.md files.
            strict_mcp_config=True,
            setting_sources=["project"],
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": SYSTEM_PROMPT_APPEND,
            },
            include_partial_messages=True,
            can_use_tool=self.can_use_tool,
            stderr=lambda line: logger.debug("[cli] %s", line),
        )

    async def _run_session(
        self, options: ClaudeAgentOptions, connected: asyncio.Future
    ) -> None:
        # The SDK client must be used in the context it was connected in, so
        # this task owns it and receives commands through a queue.
        try:
            async with ClaudeSDKClient(options) as client:
                connected.set_result(None)
                reader = asyncio.create_task(self._forward_messages(client))
                try:
                    while True:
                        command, argument = await self._commands.get()
                        try:
                            if command == "send":
                                await client.query(argument)
                            elif command == "interrupt":
                                await client.interrupt()
                        except Exception as error:
                            logger.exception("Error running %s", command)
                            await self._notify(
                                {"type": EventTypes.Error,
                                 "message": str(error)}
                            )
                finally:
                    reader.cancel()
        except Exception as error:
            logger.exception("Agent session failed")
            if not connected.done():
                connected.set_exception(error)
            else:
                await self._notify(
                    {"type": EventTypes.Error, "message": str(error)}
                )
        finally:
            await self._notify({"type": EventTypes.Stopped})

    async def _forward_messages(self, client: ClaudeSDKClient) -> None:
        async for message in client.receive_messages():
            for event in message_to_events(message):
                await self._notify(event)

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ):
        """Ask Spyder whether a tool call can run."""
        reply = await self.peer.request(
            AgentMethods.CanUseTool,
            {
                "toolName": tool_name,
                "input": tool_input,
                "toolUseId": context.tool_use_id,
                "title": context.title,
                "description": context.description,
            },
        )
        if reply.get("allow"):
            return PermissionResultAllow(
                updated_input=reply.get("updatedInput")
            )
        return PermissionResultDeny(
            message=reply.get("message") or "The user denied this action."
        )

    # ---- Helpers
    def _check_started(self) -> None:
        if self._session is None or self._session.done():
            raise RuntimeError("The session is not running")

    async def _notify(self, event: dict) -> None:
        try:
            await self.peer.notify(AgentMethods.Event, event)
        except Exception:
            logger.exception("Could not send event to Spyder")

    async def run(self) -> None:
        """Serve requests until Spyder closes the bridge."""
        await self.peer.run()
        if self._session is not None:
            self._session.cancel()


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if "--debug" in sys.argv else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(AgentWorker().run())


if __name__ == "__main__":
    main()
