# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Messages exchanged between Spyder and the agent worker subprocess.

This module must stay light: it's imported by Spyder, so it can't import the
Agent SDK.
"""


class AgentMethods:
    """JSON-RPC methods of the bridge with the agent worker."""

    # Spyder -> worker (requests)
    Start = "agent/start"
    Send = "agent/send"
    Interrupt = "agent/interrupt"

    # Worker -> Spyder (requests)
    CanUseTool = "agent/canUseTool"

    # Worker -> Spyder (notifications)
    Event = "agent/event"


class EventTypes:
    """Types of the events sent by the worker through ``agent/event``."""

    # Session metadata (model, MCP servers status)
    Init = "init"

    # A piece of the text Claude is writing
    TextDelta = "text_delta"

    # A complete text block, replacing the deltas received for it
    Text = "text"

    # Claude is calling a tool
    ToolUse = "tool_use"

    # The result of a tool call
    ToolResult = "tool_result"

    # The turn finished (successfully, with an error or interrupted)
    Result = "result"

    # An error not tied to a turn
    Error = "error"

    # The session ended
    Stopped = "stopped"


# Name of the Spyder MCP server in the agent configuration. Its tools are
# exposed to Claude as mcp__spyder__<tool>.
MCP_SERVER_NAME = "spyder"
MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
