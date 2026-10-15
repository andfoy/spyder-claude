# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Client that runs the agent worker subprocess.

The communication with the worker happens in a dedicated asyncio thread
(managed by AsyncDispatcher); events and approval requests are delivered to
the Qt main thread.
"""

# Standard library imports
from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import sys

# Third-party imports
from qtpy.QtCore import QObject, Signal
from spyder.api.asyncdispatcher import AsyncDispatcher, DispatcherFuture
from spyder.config.base import get_debug_level
from spyder.plugins.mcpserver.bridge import JsonRpcPeer

# Local imports
from spyder_claude.protocol import AgentMethods, EventTypes

logger = logging.getLogger(__name__)

_CLAUDE_LOOP = "claude"


class AgentClient(QObject):
    """Start the agent worker and relay its events to the Qt thread."""

    sig_event = Signal(dict)
    """
    This signal is emitted for every event sent by the agent.

    Parameters
    ----------
    event: dict
        Event, whose ``type`` is one of the ``EventTypes``.
    """

    sig_started = Signal()
    """This signal is emitted when the session is ready for messages."""

    sig_start_failed = Signal(str)
    """
    This signal is emitted when the session can't be started.

    Parameters
    ----------
    message: str
        Error message.
    """

    def __init__(
        self,
        parent,
        approval_handler: Callable[[dict, Callable[[dict], None]], None],
    ) -> None:
        """
        Parameters
        ----------
        approval_handler: Callable
            Called in the Qt thread with ``(request, reply)`` when the agent
            wants to use a tool that needs permission. ``reply`` must be
            called with ``{"allow": bool, "message": str}``.
        """
        super().__init__(parent)
        self._approval_handler = approval_handler
        self._process: asyncio.subprocess.Process | None = None
        self._peer: JsonRpcPeer | None = None

    # ---- Public API
    # -------------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def start(self, cwd: str, mcp: dict, model: str | None = None) -> None:
        """Start the worker and connect a new session."""
        self._run({"cwd": cwd, "mcp": mcp, "model": model})

    def send(self, text: str) -> None:
        self._request(AgentMethods.Send, {"text": text})

    def interrupt(self) -> None:
        self._request(AgentMethods.Interrupt, {})

    def stop(self) -> None:
        """Stop the worker, ending the session."""
        if not self.is_running:
            return
        try:
            self._stop().result(timeout=5)
        except Exception:
            logger.exception("Error stopping the agent worker")

    # ---- Asyncio side
    # -------------------------------------------------------------------------
    @AsyncDispatcher(loop=_CLAUDE_LOOP)
    async def _run(self, params: dict) -> None:
        args = [sys.executable, "-m", "spyder_claude.worker"]
        if get_debug_level() > 0:
            args.append("--debug")

        try:
            self._process = process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            self.sig_start_failed.emit(str(error))
            return

        async def write(data: bytes) -> None:
            process.stdin.write(data)
            await process.stdin.drain()

        self._peer = JsonRpcPeer(
            process.stdout.readline,
            write,
            {
                AgentMethods.Event: self._handle_event,
                AgentMethods.CanUseTool: self._handle_can_use_tool,
            },
        )
        reader = asyncio.ensure_future(self._peer.run())
        stderr = asyncio.ensure_future(self._log_stderr(process))

        try:
            await self._peer.request(AgentMethods.Start, params)
        except Exception as error:
            self.sig_start_failed.emit(str(error))
        else:
            self.sig_started.emit()

        await asyncio.gather(reader, stderr)
        await process.wait()
        logger.info("Agent worker exited with code %s", process.returncode)

    @AsyncDispatcher(loop=_CLAUDE_LOOP)
    async def _request(self, method: str, params: dict) -> None:
        if self._peer is None:
            return
        try:
            await self._peer.request(method, params)
        except Exception as error:
            self.sig_event.emit(
                {"type": EventTypes.Error, "message": str(error)}
            )

    @AsyncDispatcher(loop=_CLAUDE_LOOP)
    async def _stop(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def _log_stderr(self, process) -> None:
        while line := await process.stderr.readline():
            logger.debug("[worker] %s", line.decode(errors="replace").rstrip())

    async def _handle_event(self, event: dict) -> None:
        # Qt queues signals emitted from other threads
        self.sig_event.emit(event)

    async def _handle_can_use_tool(self, request: dict) -> dict:
        loop = asyncio.get_running_loop()
        result = loop.create_future()

        def reply(value: dict) -> None:
            loop.call_soon_threadsafe(_set_result, result, value)

        @AsyncDispatcher.QtSlot
        def ask(_future) -> None:
            try:
                self._approval_handler(request, reply)
            except Exception:
                logger.exception("Error asking for tool approval")
                reply({"allow": False, "message": "Spyder failed to ask."})

        trigger = DispatcherFuture()
        trigger.set_result(None)
        trigger.connect(ask)
        return await result


def _set_result(future: asyncio.Future, value) -> None:
    if not future.done():
        future.set_result(value)
