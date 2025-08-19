"""Metrics hook implementation for Prometheus metrics collection."""

from __future__ import annotations

import time

import structlog

from ccproxy.hooks.base import HookContext
from ccproxy.hooks.events import HookEvent
from ccproxy.observability.metrics import PrometheusMetrics


logger = structlog.get_logger(__name__)


class MetricsHook:
    """
    Collects Prometheus metrics based on hook events.

    This hook listens to request lifecycle and provider events to track:
    - Request counts by provider and status
    - Request duration histograms
    - Active request gauge
    - Error rates by provider and type
    """

    def __init__(self, metrics: PrometheusMetrics):
        """
        Initialize the metrics hook.

        Args:
            metrics: PrometheusMetrics instance for recording metrics
        """
        self.metrics = metrics
        self._active_request_timestamps: dict[str, float] = {}

    @property
    def name(self) -> str:
        """Hook name for debugging."""
        return "metrics_hook"

    @property
    def events(self) -> list[HookEvent]:
        """Events this hook listens to."""
        return [
            HookEvent.REQUEST_STARTED,
            HookEvent.REQUEST_COMPLETED,
            HookEvent.REQUEST_FAILED,
            HookEvent.PROVIDER_ERROR,
        ]

    async def __call__(self, context: HookContext) -> None:
        """
        Update metrics based on event.

        Args:
            context: Hook context containing event data
        """
        try:
            if context.event == HookEvent.REQUEST_STARTED:
                await self._handle_request_started(context)
            elif context.event == HookEvent.REQUEST_COMPLETED:
                await self._handle_request_completed(context)
            elif context.event == HookEvent.REQUEST_FAILED:
                await self._handle_request_failed(context)
            elif context.event == HookEvent.PROVIDER_ERROR:
                await self._handle_provider_error(context)
        except Exception as e:
            # Log errors but don't let them propagate to avoid breaking requests
            logger.error(
                "metrics_hook_error", event=context.event, error=str(e), exc_info=e
            )

    async def _handle_request_started(self, context: HookContext) -> None:
        """Handle REQUEST_STARTED event."""
        # Increment active requests counter
        self.metrics.inc_active_requests()

        # Store start timestamp for duration calculation
        request_id = self._get_request_id(context)
        if request_id:
            self._active_request_timestamps[request_id] = time.time()

    async def _handle_request_completed(self, context: HookContext) -> None:
        """Handle REQUEST_COMPLETED event."""
        # Decrement active requests counter
        self.metrics.dec_active_requests()

        # Extract request details
        method = self._get_method(context)
        endpoint = self._get_endpoint(context)
        model = self._get_model(context)
        status = self._get_status(context)
        service_type = self._get_service_type(context)

        # Record request count
        self.metrics.record_request(
            method=method,
            endpoint=endpoint,
            model=model,
            status=status,
            service_type=service_type,
        )

        # Record response time if we have start timestamp
        request_id = self._get_request_id(context)
        if request_id and request_id in self._active_request_timestamps:
            start_time = self._active_request_timestamps.pop(request_id)
            duration = time.time() - start_time

            self.metrics.record_response_time(
                duration_seconds=duration,
                model=model,
                endpoint=endpoint,
                service_type=service_type,
            )

        # Record token usage if available
        self._record_token_metrics(context, model, service_type)

        # Record cost if available
        self._record_cost_metrics(context, model, service_type)

    async def _handle_request_failed(self, context: HookContext) -> None:
        """Handle REQUEST_FAILED event."""
        # Decrement active requests counter
        self.metrics.dec_active_requests()

        # Clean up timestamp tracking
        request_id = self._get_request_id(context)
        if request_id and request_id in self._active_request_timestamps:
            start_time = self._active_request_timestamps.pop(request_id)
            # Still record duration for failed requests
            duration = time.time() - start_time

            self.metrics.record_response_time(
                duration_seconds=duration,
                model=self._get_model(context),
                endpoint=self._get_endpoint(context),
                service_type=self._get_service_type(context),
            )

        # Record error
        error_type = self._get_error_type(context)
        endpoint = self._get_endpoint(context)
        model = self._get_model(context)
        service_type = self._get_service_type(context)

        self.metrics.record_error(
            error_type=error_type,
            endpoint=endpoint,
            model=model,
            service_type=service_type,
        )

        # Also record the failed request count
        method = self._get_method(context)
        status = "error"

        self.metrics.record_request(
            method=method,
            endpoint=endpoint,
            model=model,
            status=status,
            service_type=service_type,
        )

    async def _handle_provider_error(self, context: HookContext) -> None:
        """Handle PROVIDER_ERROR event."""
        error_type = f"provider_{self._get_error_type(context)}"
        endpoint = self._get_endpoint(context)
        model = self._get_model(context)
        service_type = self._get_service_type(context)

        self.metrics.record_error(
            error_type=error_type,
            endpoint=endpoint,
            model=model,
            service_type=service_type,
        )

    def _get_request_id(self, context: HookContext) -> str | None:
        """Extract request ID for tracking duration."""
        if context.request and hasattr(context.request, "headers"):
            return context.request.headers.get("x-request-id")
        return context.data.get("request_id")

    def _get_method(self, context: HookContext) -> str:
        """Extract HTTP method from context."""
        if context.request:
            return context.request.method
        return str(context.data.get("method", "unknown"))

    def _get_endpoint(self, context: HookContext) -> str:
        """Extract endpoint from context."""
        if context.request and hasattr(context.request, "url"):
            return str(context.request.url.path)
        return str(context.data.get("endpoint", "unknown"))

    def _get_model(self, context: HookContext) -> str | None:
        """Extract model name from context."""
        # Check data first for explicit model info
        model = context.data.get("model")
        if model:
            return str(model)

        # Try to extract from request body if available
        if context.request and hasattr(context.request, "_body"):
            try:
                import json

                body = getattr(context.request, "_body", None)
                if body and isinstance(body, str | bytes):
                    if isinstance(body, bytes):
                        body = body.decode("utf-8")
                    data = json.loads(body)
                    model_value = data.get("model")
                    return str(model_value) if model_value else None
            except (json.JSONDecodeError, AttributeError):
                pass

        return None

    def _get_status(self, context: HookContext) -> str:
        """Extract status from context."""
        # Check data first
        status = context.data.get("status")
        if status is not None:
            return str(status)

        # Check response status
        if context.response and hasattr(context.response, "status_code"):
            return str(context.response.status_code)

        return "unknown"

    def _get_service_type(self, context: HookContext) -> str | None:
        """Extract service type from context."""
        service_type = context.data.get("service_type")
        if service_type:
            return str(service_type)
        return context.provider or "unknown"

    def _get_error_type(self, context: HookContext) -> str:
        """Extract error type from context."""
        if context.error:
            return context.error.__class__.__name__
        error_type = context.data.get("error_type", "unknown_error")
        return str(error_type)

    def _record_token_metrics(
        self, context: HookContext, model: str | None, service_type: str | None
    ) -> None:
        """Record token usage metrics if available."""
        token_data = context.data.get("tokens")
        if not token_data:
            return

        if isinstance(token_data, dict):
            # Handle structured token data
            for token_type, count in token_data.items():
                if isinstance(count, int) and count > 0:
                    self.metrics.record_tokens(
                        token_count=count,
                        token_type=token_type,
                        model=model,
                        service_type=service_type,
                    )
        elif isinstance(token_data, int) and token_data > 0:
            # Handle simple token count
            self.metrics.record_tokens(
                token_count=token_data,
                token_type="total",
                model=model,
                service_type=service_type,
            )

    def _record_cost_metrics(
        self, context: HookContext, model: str | None, service_type: str | None
    ) -> None:
        """Record cost metrics if available."""
        cost_data = context.data.get("cost")
        if not cost_data:
            return

        if isinstance(cost_data, dict):
            # Handle structured cost data
            for cost_type, amount in cost_data.items():
                if isinstance(amount, int | float) and amount > 0:
                    self.metrics.record_cost(
                        cost_usd=float(amount),
                        model=model,
                        cost_type=cost_type,
                        service_type=service_type,
                    )
        elif isinstance(cost_data, int | float) and cost_data > 0:
            # Handle simple cost amount
            self.metrics.record_cost(
                cost_usd=float(cost_data),
                model=model,
                cost_type="total",
                service_type=service_type,
            )
