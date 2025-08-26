"""Shared cost calculation helper for pricing service integration."""

from typing import Any


def safe_calculate_cost(
    pricing_service: Any,
    model: str | None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    logger: Any = None,
    log_ctx: dict[str, Any] | None = None,
) -> float | None:
    """
    Wrapper around pricing_service.calculate_cost_sync that handles
    all pricing exceptions uniformly.
    Returns float cost or None when cost cannot be calculated.
    """
    if not (pricing_service and model):
        if logger:
            logger.warning(
                "cost_calculation_skipped",
                **(log_ctx or {}),
                reason="no_pricing_or_model",
            )
        return None

    from plugins.pricing.exceptions import (
        ModelPricingNotFoundError,
        PricingDataNotLoadedError,
        PricingServiceDisabledError,
    )

    try:
        cost_decimal = pricing_service.calculate_cost_sync(
            model_name=model,
            input_tokens=tokens_input,
            output_tokens=tokens_output,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )
        cost = float(cost_decimal)
        if logger:
            logger.debug("cost_calculated", **(log_ctx or {}), cost_usd=cost)
        return cost
    except ModelPricingNotFoundError as e:
        if logger:
            logger.warning("model_pricing_not_found", **(log_ctx or {}), message=str(e))
    except PricingDataNotLoadedError as e:
        if logger:
            logger.warning("pricing_data_not_loaded", **(log_ctx or {}), message=str(e))
    except PricingServiceDisabledError as e:
        if logger:
            logger.debug("pricing_service_disabled", **(log_ctx or {}), message=str(e))
    except Exception as e:
        if logger:
            logger.debug("cost_calculation_failed", **(log_ctx or {}), error=str(e))
    return None
