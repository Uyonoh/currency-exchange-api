"""FastAPI application entry point for the currency exchange API.

Endpoints
---------
- GET /health                       -> liveness / status probe
- GET /rates                        -> all rates relative to a base currency
- GET /rates/{base}/{target}        -> single conversion rate
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import models
from app.currency_data import SUPPORTED_CURRENCIES, RateManager, Settings
from app.exceptions import (
    CurrencyExchangeError,
    RateFetchError,
    RatesNotAvailableError,
    UnsupportedCurrencyError,
)

#: Process-wide rate manager (cached rates + background scheduler).
manager = RateManager(Settings())


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Load rates on startup and keep them fresh from a background thread."""
    await manager.initialize()
    manager.start_scheduler()
    yield
    manager.stop_scheduler()


app = FastAPI(
    title="Currency Exchange API",
    version="0.1.0",
    description=(
        "Self-hosted, free currency exchange rates. "
        f"Supported currencies: {', '.join(sorted(SUPPORTED_CURRENCIES))}."
    ),
    lifespan=lifespan,
)

origins = [
    "http://localhost:3000",          # Next.js local development
    "http://10.78.105.27:3000",   # remote dev
    "https://prompts.uyonoh.com",
]

# 2. Add the CORS middleware to the app instance
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,            # Allows requests from specified origins
    allow_credentials=True,
    allow_methods=["GET"],              # Allows all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],              # Allows all headers
)


def _error_response(error: CurrencyExchangeError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=models.ErrorResponse(error=error.code, detail=error.detail).model_dump(),
    )


@app.exception_handler(UnsupportedCurrencyError)
async def unsupported_currency_handler(_: Request, exc: UnsupportedCurrencyError) -> JSONResponse:
    return _error_response(exc)


@app.exception_handler(RatesNotAvailableError)
async def rates_unavailable_handler(_: Request, exc: RatesNotAvailableError) -> JSONResponse:
    return _error_response(exc)


@app.exception_handler(RateFetchError)
async def rate_fetch_error_handler(_: Request, exc: RateFetchError) -> JSONResponse:
    return _error_response(exc)


@app.exception_handler(CurrencyExchangeError)
async def generic_currency_error_handler(_: Request, exc: CurrencyExchangeError) -> JSONResponse:
    return _error_response(exc)


@app.get("/health", response_model=models.HealthResponse, tags=["system"])
async def health() -> models.HealthResponse:
    """Return 200 OK when the API is operational."""
    return models.HealthResponse(
        status="ok",
        source=manager.active_source,
        rates_count=len(manager.rates),
        last_updated=manager.last_updated,
    )


@app.get("/rates", response_model=models.RateResponse, tags=["rates"])
async def get_rates(
    base: str = Query(default="USD", description="Base currency for the rate snapshot."),
) -> models.RateResponse:
    """Return all supported rates relative to ``base``."""
    return models.RateResponse(
        base_currency=base.upper(),
        last_updated=manager.last_updated or datetime.now(timezone.utc),
        source=manager.active_source or "unknown",
        rates=manager.get_rates(base),
    )


@app.get("/rates/{base_currency}/{target_currency}", response_model=models.RatePairResponse, tags=["rates"])
async def get_pair_rate(base_currency: str, target_currency: str) -> models.RatePairResponse:
    """Return the conversion rate from ``base_currency`` to ``target_currency``."""
    return models.RatePairResponse(
        base_currency=base_currency.upper(),
        target_currency=target_currency.upper(),
        rate=manager.get_rate(base_currency, target_currency),
        last_updated=manager.last_updated or datetime.now(timezone.utc),
        source=manager.active_source or "unknown",
    )

if __name__ == "__main__":
