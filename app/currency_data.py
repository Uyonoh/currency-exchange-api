"""Data sourcing, caching, and scheduled refresh for exchange rates.

Data sources
------------
Two free, keyless, publicly accessible providers are integrated behind a
small ``RateSource`` interface so more can be added later:

1. **open.er-api.com (ExchangeRate-API free tier)** - primary source.
   Keyless, no sign-up, updated daily, and - crucially - supports NGN and
   every currency the API needs. The free endpoint returns USD-based rates
   covering the full supported set.

2. **api.frankfurter.app (Frankfurter, ECB reference rates)** - fallback.
   Built on the European Central Bank's official daily reference rates.
   Fully free and open (MIT, open-source mirror of ECB data), reliable for
   the major currencies, but does NOT publish NGN. It only kicks in if the
   primary source is unreachable and cannot serve NGN on its own.

The primary source is tried first; if it fails, the fallback is attempted.
A successful fetch populates the in-memory cache, is written to a simple
JSON file for cross-restart reuse, and is refreshed on a schedule from a
background daemon thread (default every 6 hours -> "at least daily").
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from .exceptions import RateFetchError, RatesNotAvailableError, UnsupportedCurrencyError

logger = logging.getLogger(__name__)

#: Canonical, supported currency codes. Adding a code here (plus a data
#: source that publishes it) is all that is needed to extend the API.
SUPPORTED_CURRENCIES: "set[str]" = {
    "USD", "NGN", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "INR",
}

_HTTP_HEADERS = {"User-Agent": "currency-exchange-api/0.1 (+self-hosted; contact: local)"}


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, overridable via environment variables."""

    base_currency: str = field(default_factory=lambda: os.getenv("RATE_BASE_CURRENCY", "USD"))
    #: Refresh the in-memory cache every N seconds (default 6h -> refreshed
    #: well within the "at least daily" requirement).
    refresh_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("RATE_REFRESH_INTERVAL_SECONDS", "21600"))
    )
    #: Treat a cached payload older than this as stale and force a refresh.
    max_age_seconds: int = field(
        default_factory=lambda: int(os.getenv("RATE_MAX_AGE_SECONDS", "86400"))
    )
    #: Optional JSON cache file used to survive restarts.
    cache_file: Optional[str] = field(
        default_factory=lambda: os.getenv("RATE_CACHE_FILE", "data/rates_cache.json")
    )
    request_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("RATE_REQUEST_TIMEOUT_SECONDS", "10"))
    )


class RateSource:
    """Abstract contract for a free exchange-rate provider."""

    name: str = "base"

    async def fetch(self, base: str) -> Dict[str, float]:
        """Return ``{currency_code: rate}`` relative to ``base``."""
        raise NotImplementedError


class OpenERApiSource(RateSource):
    """ExchangeRate-API free tier (https://open.er-api.com/v6/latest/{base})."""

    name = "open.er-api.com"
    url = "https://open.er-api.com/v6/latest/{base}"

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def fetch(self, base: str) -> Dict[str, float]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=_HTTP_HEADERS) as client:
            response = await client.get(self.url.format(base=base))
            response.raise_for_status()
            payload = response.json()
        if payload.get("result") != "success" or "rates" not in payload:
            raise RateFetchError(f"{self.name}: unexpected response: {payload.get('result')}")
        return payload["rates"]


class FrankfurterSource(RateSource):
    """Frankfurter (ECB reference rates) https://api.frankfurter.app/latest."""

    name = "api.frankfurter.app"
    url = "https://api.frankfurter.app/latest"

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def fetch(self, base: str) -> Dict[str, float]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=_HTTP_HEADERS) as client:
            response = await client.get(self.url, params={"from": base})
            response.raise_for_status()
            payload = response.json()
        if "rates" not in payload:
            raise RateFetchError(f"{self.name}: unexpected response")
        return payload["rates"]


class RateManager:
    """Owns the rate cache, scheduled refresh, and conversion math."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or Settings()
        self.sources: List[RateSource] = [
            OpenERApiSource(self.settings.request_timeout_seconds),
            FrankfurterSource(self.settings.request_timeout_seconds),
        ]
        self._rates: Dict[str, float] = {}
        self._last_updated: Optional[datetime] = None
        self._active_source: Optional[str] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ cache

    @property
    def rates(self) -> Dict[str, float]:
        """Snapshot of the current cached rates (relative to base currency)."""
        with self._lock:
            return dict(self._rates)

    @property
    def last_updated(self) -> Optional[datetime]:
        with self._lock:
            return self._last_updated

    @property
    def active_source(self) -> Optional[str]:
        with self._lock:
            return self._active_source

    def _is_stale(self) -> bool:
        if self._last_updated is None:
            return True
        age = datetime.now(timezone.utc) - self._last_updated
        return age.total_seconds() > self.settings.max_age_seconds

    def _persist_cache(self) -> None:
        """Write the current cache to the JSON file (best effort)."""
        path = self.settings.cache_file
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            payload = {
                "base_currency": self.settings.base_currency,
                "last_updated": self._last_updated.isoformat(),
                "source": self._active_source,
                "rates": self._rates,
            }
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except OSError as exc:  # pragma: no cover - cache write must not crash
            logger.warning("Could not persist rate cache to %s: %s", path, exc)

    def load_cache(self) -> bool:
        """Load rates from the JSON file. Returns True on success."""
        path = self.settings.cache_file
        if not path or not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            rates = {
                code: float(value)
                for code, value in payload["rates"].items()
                if code in SUPPORTED_CURRENCIES
            }
            if self.settings.base_currency not in rates:
                return False
            with self._lock:
                self._rates = rates
                self._active_source = payload.get("source")
                self._last_updated = datetime.fromisoformat(payload["last_updated"])
            return True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Ignoring invalid cache file %s: %s", path, exc)
            return False

    # ---------------------------------------------------------------- fetching

    async def refresh(self) -> str:
        """Fetch from the first working source and update the cache.

        Returns the name of the source used. Raises ``RateFetchError`` if
        every configured source fails.
        """
        errors: List[str] = []
        for source in self.sources:
            try:
                raw = await source.fetch(self.settings.base_currency)
            except Exception as exc:  # noqa: BLE001 - collect all source failures
                errors.append(f"{source.name}: {exc}")
                continue

            rates = {
                code: float(value)
                for code, value in raw.items()
                if code in SUPPORTED_CURRENCIES
            }
            if self.settings.base_currency not in rates:
                errors.append(f"{source.name}: response missing base '{self.settings.base_currency}'")
                continue

            with self._lock:
                self._rates = rates
                self._last_updated = datetime.now(timezone.utc)
                self._active_source = source.name
            self._persist_cache()
            logger.info("Rates refreshed from %s (%d currencies)", source.name, len(rates))
            return source.name

        raise RateFetchError(" | ".join(errors))

    async def initialize(self) -> None:
        """Load a fresh-enough cache or perform an immediate refresh."""
        loaded = self.load_cache()
        if not loaded or self._is_stale():
            await self.refresh()

    # ---------------------------------------------------------------- scheduler

    def start_scheduler(self) -> None:
        """Start the background refresh loop (idempotent)."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._stop_event.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, name="rate-refresh-scheduler", daemon=True
        )
        self._scheduler_thread.start()
        logger.info("Rate refresh scheduler started (interval=%ss)", self.settings.refresh_interval_seconds)

    def stop_scheduler(self) -> None:
        self._stop_event.set()

    def _scheduler_loop(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._scheduler_task())
        finally:
            loop.close()

    async def _scheduler_task(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.refresh()
            except RateFetchError as exc:
                logger.error("Scheduled refresh failed: %s", exc)
            await asyncio.sleep(self.settings.refresh_interval_seconds)

    # --------------------------------------------------------------- conversions

    def _validate_currency(self, currency: str) -> str:
        code = currency.upper()
        if code not in SUPPORTED_CURRENCIES:
            raise UnsupportedCurrencyError(currency, SUPPORTED_CURRENCIES)
        return code

    def get_rates(self, base: str) -> Dict[str, float]:
        """Rates for every supported currency relative to ``base``."""
        base = self._validate_currency(base)
        cached = self.rates
        if not cached:
            raise RatesNotAvailableError()
        base_rate = cached[base]
        return {code: round(value / base_rate, 6) for code, value in cached.items()}

    def get_rate(self, base: str, target: str) -> float:
        """Single conversion rate ``base -> target``."""
        base = self._validate_currency(base)
        target = self._validate_currency(target)
        cached = self.rates
        if not cached:
            raise RatesNotAvailableError()
        return round(cached[target] / cached[base], 6)
