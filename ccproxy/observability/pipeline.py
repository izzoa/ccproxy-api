"""
ObservabilityPipeline for coordinating event handling across the system.

The pipeline manages event observers and provides methods for emitting events
to all registered observers. This allows for centralized coordination of
logging, metrics collection, and other observability concerns.
"""

from typing import Protocol, runtime_checkable

import structlog

from .events import (
    ClientRequestEvent,
    ClientResponseEvent,
    ProviderRequestEvent,
    ProviderResponseEvent,
)


logger = structlog.get_logger(__name__)


@runtime_checkable
class RequestObserver(Protocol):
    """Protocol for objects that observe request events."""

    async def on_client_request(self, event: ClientRequestEvent) -> None:
        """Handle client request event."""
        ...

    async def on_client_response(self, event: ClientResponseEvent) -> None:
        """Handle client response event."""
        ...

    async def on_provider_request(self, event: ProviderRequestEvent) -> None:
        """Handle provider request event."""
        ...

    async def on_provider_response(self, event: ProviderResponseEvent) -> None:
        """Handle provider response event."""
        ...


class ObservabilityPipeline:
    """Pipeline for coordinating observability events across the system."""

    def __init__(self) -> None:
        self.observers: list[RequestObserver] = []

    def register_observer(self, observer: RequestObserver) -> None:
        """Register an observer to receive events."""
        if observer not in self.observers:
            self.observers.append(observer)
            logger.info(
                "observer_registered",
                observer_type=type(observer).__name__,
                total_observers=len(self.observers),
            )

    def unregister_observer(self, observer: RequestObserver) -> None:
        """Unregister an observer."""
        if observer in self.observers:
            self.observers.remove(observer)
            logger.info(
                "observer_unregistered",
                observer_type=type(observer).__name__,
                total_observers=len(self.observers),
            )

    async def notify_client_request(self, event: ClientRequestEvent) -> None:
        """Notify all observers of a client request event."""
        for observer in self.observers:
            try:
                await observer.on_client_request(event)
            except Exception as e:
                logger.error(
                    "observer_error",
                    observer_type=type(observer).__name__,
                    event_type="client_request",
                    error=str(e),
                    request_id=event.request_id,
                )

    async def notify_client_response(self, event: ClientResponseEvent) -> None:
        """Notify all observers of a client response event."""
        for observer in self.observers:
            try:
                await observer.on_client_response(event)
            except Exception as e:
                logger.error(
                    "observer_error",
                    observer_type=type(observer).__name__,
                    event_type="client_response",
                    error=str(e),
                    request_id=event.request_id,
                )

    async def notify_provider_request(self, event: ProviderRequestEvent) -> None:
        """Notify all observers of a provider request event."""
        for observer in self.observers:
            try:
                await observer.on_provider_request(event)
            except Exception as e:
                logger.error(
                    "observer_error",
                    observer_type=type(observer).__name__,
                    event_type="provider_request",
                    error=str(e),
                    request_id=event.request_id,
                )

    async def notify_provider_response(self, event: ProviderResponseEvent) -> None:
        """Notify all observers of a provider response event."""
        for observer in self.observers:
            try:
                await observer.on_provider_response(event)
            except Exception as e:
                logger.error(
                    "observer_error",
                    observer_type=type(observer).__name__,
                    event_type="provider_response",
                    error=str(e),
                    request_id=event.request_id,
                )

    def get_observer_count(self) -> int:
        """Get the number of registered observers."""
        return len(self.observers)


# Global pipeline instance
_pipeline: ObservabilityPipeline | None = None


def get_observability_pipeline() -> ObservabilityPipeline:
    """Get the global observability pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = ObservabilityPipeline()
        logger.info("observability_pipeline_created")
    return _pipeline


def reset_observability_pipeline() -> None:
    """Reset the global pipeline instance (mainly for testing)."""
    global _pipeline
    _pipeline = None
