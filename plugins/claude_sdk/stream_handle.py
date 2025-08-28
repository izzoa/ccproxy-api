"""Simplified stream handle for managing streaming without complex worker architecture."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Set

from ccproxy.core.logging import get_plugin_logger

from .config import SessionPoolSettings
from .session_client import SessionClient


logger = get_plugin_logger()


class StreamHandle:
    """Simplified streaming handle with direct queue management."""

    def __init__(
        self,
        message_iterator: AsyncIterator[Any],
        session_id: str | None = None,
        request_id: str | None = None,
        session_client: SessionClient | None = None,
        session_config: SessionPoolSettings | None = None,
    ):
        """Initialize stream handle.

        Args:
            message_iterator: Iterator from Claude SDK
            session_id: Optional session ID
            request_id: Optional request ID
            session_client: Optional session client
            session_config: Optional session pool configuration
        """
        self.handle_id = str(uuid.uuid4())
        self.sdk_iterator = message_iterator
        self.session_id = session_id
        self.request_id = request_id
        self._session_client = session_client
        self._session_config = session_config

        # Timeout configuration
        self._interrupt_timeout = (
            session_config.stream_interrupt_timeout if session_config else 10.0
        )

        # Direct queue management
        self.listeners: set[asyncio.Queue] = set()
        self._broadcast_task: asyncio.Task | None = None
        self._completed = False
        self._error: Exception | None = None
        self._created_at = time.time()

    async def create_listener(self) -> AsyncIterator[Any]:
        """Create a new listener for this stream.

        This method starts the worker on first listener and returns
        an async iterator for consuming messages.

        Yields:
            Messages from the stream
        """
        # Start broadcast task if not already running
        await self.start()

        # Create listener queue
        queue = self.add_listener()

        logger.debug(
            "stream_handle_listener_created",
            handle_id=self.handle_id,
            listener_id=id(queue),
            total_listeners=len(self.listeners),
            session_id=self.session_id,
            category="streaming",
        )

        try:
            # Yield messages from listener
            async for message in self.create_iterator(queue):
                yield message

        except GeneratorExit:
            # Client disconnected
            logger.debug(
                "stream_handle_listener_disconnected",
                handle_id=self.handle_id,
                listener_id=id(queue),
                remaining_listeners=len(self.listeners) - 1,
            )

            # Check if this will be the last listener after removal
            remaining_listeners = len(self.listeners) - 1
            if remaining_listeners == 0 and self._session_client:
                logger.debug(
                    "stream_handle_last_listener_disconnected",
                    handle_id=self.handle_id,
                    listener_id=id(queue),
                    message="Last listener disconnected, will trigger SDK interrupt in cleanup",
                )
            raise

        finally:
            # Remove listener
            self.remove_listener(queue)

            # Check if we should trigger cleanup
            await self._check_cleanup()

    async def start(self) -> None:
        """Start the broadcast task."""
        if self._broadcast_task is None:
            self._broadcast_task = asyncio.create_task(
                self._run_and_broadcast()
            )

    async def stop(self) -> None:
        """Stop the broadcast task."""
        if self._broadcast_task:
            self._broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._broadcast_task
            self._broadcast_task = None

    def add_listener(self) -> asyncio.Queue:
        """Add a new listener queue.

        Returns:
            Queue for receiving messages
        """
        queue = asyncio.Queue()
        self.listeners.add(queue)
        return queue

    def remove_listener(self, queue: asyncio.Queue) -> None:
        """Remove a listener queue.

        Args:
            queue: Queue to remove
        """
        self.listeners.discard(queue)

    async def _run_and_broadcast(self) -> None:
        """Consume SDK iterator and broadcast to all listeners."""
        try:
            async for chunk in self.sdk_iterator:
                # Serialize chunk for transmission if needed
                if isinstance(chunk, str):
                    message = chunk
                else:
                    # For complex objects, pass them directly for compatibility
                    message = chunk

                # Broadcast to all active listeners
                await self._broadcast_message(message)

            # Send completion signal
            await self._broadcast_message(None)
            self._completed = True

        except Exception as e:
            logger.error(
                "stream_broadcast_error",
                session_id=self.session_id,
                handle_id=self.handle_id,
                error=str(e)
            )
            self._error = e
            # Send error signal
            await self._broadcast_message(None)

    async def _broadcast_message(self, message: Any) -> None:
        """Broadcast message to all listeners.

        Args:
            message: Message to broadcast, None signals completion
        """
        # Use asyncio.gather for concurrent broadcast
        if self.listeners:
            await asyncio.gather(
                *[self._send_to_queue(q, message) for q in self.listeners],
                return_exceptions=True
            )

    async def _send_to_queue(
        self,
        queue: asyncio.Queue,
        message: Any
    ) -> None:
        """Send message to a single queue.

        Args:
            queue: Target queue
            message: Message to send
        """
        try:
            await queue.put(message)
        except Exception as e:
            logger.debug(
                "queue_send_error",
                error=str(e)
            )

    async def create_iterator(self, queue: asyncio.Queue | None = None) -> AsyncIterator[Any]:
        """Create an iterator for a listener.

        Args:
            queue: Optional existing queue, creates new one if None

        Yields:
            Messages from the stream
        """
        if queue is None:
            queue = self.add_listener()

        try:
            while True:
                message = await queue.get()
                if message is None:
                    # Stream completed or error occurred
                    if self._error:
                        raise self._error
                    break
                yield message
        finally:
            if queue in self.listeners:
                self.remove_listener(queue)

    async def _check_cleanup(self) -> None:
        """Check if cleanup is needed when no listeners remain."""
        if len(self.listeners) == 0:
            # No more listeners - trigger interrupt if session client available
            if self._session_client:
                logger.debug(
                    "stream_handle_all_listeners_disconnected",
                    handle_id=self.handle_id,
                    message="All listeners disconnected, triggering SDK interrupt",
                )

                # Trigger interrupt
                try:
                    await asyncio.wait_for(
                        self._session_client.interrupt(),
                        timeout=self._interrupt_timeout,
                    )
                    logger.debug(
                        "stream_handle_interrupt_completed",
                        handle_id=self.handle_id,
                        message="SDK interrupt completed successfully",
                    )
                except TimeoutError:
                    logger.warning(
                        "stream_handle_interrupt_timeout",
                        handle_id=self.handle_id,
                        message=f"SDK interrupt timed out after {self._interrupt_timeout} seconds",
                    )
                except Exception as e:
                    logger.error(
                        "stream_handle_interrupt_failed",
                        handle_id=self.handle_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )

            # Stop the broadcast task
            await self.stop()

    async def interrupt(self) -> bool:
        """Interrupt the stream.

        Returns:
            True if interrupted successfully
        """
        logger.debug(
            "stream_handle_interrupting",
            handle_id=self.handle_id,
            active_listeners=len(self.listeners),
        )

        try:
            # Stop the broadcast task
            await self.stop()

            # Clear all listeners
            self.listeners.clear()

            logger.debug(
                "stream_handle_interrupted",
                handle_id=self.handle_id,
            )
            return True

        except Exception as e:
            logger.error(
                "stream_handle_interrupt_error",
                handle_id=self.handle_id,
                error=str(e),
            )
            return False

    async def wait_for_completion(self, timeout: float | None = None) -> bool:
        """Wait for the stream to complete.

        Args:
            timeout: Optional timeout in seconds

        Returns:
            True if completed, False if timed out
        """
        if not self._broadcast_task:
            return True

        try:
            if timeout:
                await asyncio.wait_for(self._broadcast_task, timeout=timeout)
            else:
                await self._broadcast_task
            return True
        except TimeoutError:
            return False

    def get_stats(self) -> dict[str, Any]:
        """Get stream handle statistics.

        Returns:
            Dictionary of statistics
        """
        return {
            "handle_id": self.handle_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "active_listeners": len(self.listeners),
            "lifetime_seconds": time.time() - self._created_at,
            "completed": self._completed,
            "has_error": self._error is not None,
        }

    @property
    def has_active_listeners(self) -> bool:
        """Check if there are any active listeners."""
        return len(self.listeners) > 0
