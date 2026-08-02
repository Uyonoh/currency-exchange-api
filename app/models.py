"""Pydantic models for request validation and response serialization.

All outgoing rate payloads share a common shape so clients and future
PostgreSQL persistence have a single, stable contract.
"""

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field


class RateResponse(BaseModel):
    """Full snapshot of rates relative to a single base currency."""

    base_currency: str = Field(..., description="The base currency the rates are relative to.")
    last_updated: datetime = Field(..., description="Timestamp of the last successful data fetch.")
    source: str = Field(..., description="Name of the data source that produced the rates.")
    rates: Dict[str, float] = Field(..., description="Mapping of currency code -> rate vs base currency.")


class RatePairResponse(BaseModel):
    """A single base -> target conversion rate."""

    base_currency: str
    target_currency: str
    rate: float
    last_updated: datetime
    source: str


class HealthResponse(BaseModel):
    """Status payload returned by ``GET /health``."""

    status: str = "ok"
    source: Optional[str] = None
    rates_count: int = 0
    last_updated: Optional[datetime] = None


class ErrorResponse(BaseModel):
    """Uniform error body returned by every registered exception handler."""

    error: str = Field(..., description="Stable, machine-readable error code.")
    detail: str = Field(..., description="Human-readable description of the failure.")
