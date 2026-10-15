# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Tests for converting Agent SDK messages to bridge events."""

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import StreamEvent

from spyder_claude.worker import message_to_events, normalize_content


def test_text_deltas():
    event = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Hel"},
    }
    assert message_to_events(StreamEvent("u", "s", event)) == [
        {"type": "text_delta", "text": "Hel"}
    ]

    # Subagents' text is not streamed
    assert message_to_events(StreamEvent("u", "s", event, "toolu_1")) == []

    # Other stream events are ignored
    assert message_to_events(
        StreamEvent("u", "s", {"type": "message_start"})
    ) == []


def test_assistant_message():
    message = AssistantMessage(
        content=[
            TextBlock(text="Let me check."),
            ToolUseBlock(
                id="toolu_1",
                name="mcp__spyder__execute_code",
                input={"code": "1 + 1"},
            ),
        ],
        model="claude-opus-5-5",
    )
    assert message_to_events(message) == [
        {"type": "text", "text": "Let me check."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "mcp__spyder__execute_code",
            "input": {"code": "1 + 1"},
        },
    ]


def test_tool_results():
    message = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="toolu_1",
                content=[
                    {"type": "text", "text": "Out[1]: 2"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "abc",
                        },
                    },
                ],
                is_error=False,
            )
        ]
    )
    assert message_to_events(message) == [
        {
            "type": "tool_result",
            "id": "toolu_1",
            "isError": False,
            "content": [
                {"type": "text", "text": "Out[1]: 2"},
                {"type": "image", "data": "abc", "mimeType": "image/png"},
            ],
        }
    ]

    # Plain user prompts are not events
    assert message_to_events(UserMessage(content="Hello")) == []


def test_normalize_content():
    assert normalize_content(None) == []
    assert normalize_content("error") == [{"type": "text", "text": "error"}]

    # MCP-style images
    assert normalize_content(
        [{"type": "image", "data": "abc", "mimeType": "image/jpeg"}]
    ) == [{"type": "image", "data": "abc", "mimeType": "image/jpeg"}]


def test_init_and_result():
    init = SystemMessage(
        subtype="init",
        data={
            "model": "claude-opus-5-5",
            "session_id": "s1",
            "mcp_servers": [{"name": "spyder", "status": "connected"}],
        },
    )
    assert message_to_events(init) == [
        {
            "type": "init",
            "model": "claude-opus-5-5",
            "sessionId": "s1",
            "mcpServers": [{"name": "spyder", "status": "connected"}],
        }
    ]

    result = ResultMessage(
        subtype="success",
        duration_ms=1500,
        duration_api_ms=1200,
        is_error=False,
        num_turns=2,
        session_id="s1",
        total_cost_usd=0.01,
        terminal_reason="completed",
    )
    assert message_to_events(result) == [
        {
            "type": "result",
            "isError": False,
            "subtype": "success",
            "terminalReason": "completed",
            "costUsd": 0.01,
            "durationMs": 1500,
            "numTurns": 2,
            "errors": [],
        }
    ]
