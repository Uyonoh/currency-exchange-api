"""Custom exception types for the currency exchange API.

Each exception exposes a stable ``code`` and an HTTP ``status_code`` so
the global exception handlers in ``main.py`` can map it to a consistent
JSON error body without exception-specific logic in the endpoints.
"""


class CurrencyExchangeError(Exception):
    """Base class for all API-specific errors."""

    status_code = 500
    code = "currency_exchange_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UnsupportedCurrencyError(CurrencyExchangeError):
    """Raised when a requested currency code is not in the supported set."""

    status_code = 400
    code = "unsupported_currency"

    def __init__(self, currency: str, supported: "set[str]") -> None:
        detail = (
            f"Currency '{currency}' is not supported. "
            f"Supported currencies: {', '.join(sorted(supported))}"
        )
        super().__init__(detail)
        self.currency = currency


class RatesNotAvailableError(CurrencyExchangeError):
    """Raised when no rates have been loaded (cache empty and fetch failed)."""

    status_code = 503
    code = "rates_unavailable"

    def __init__(self) -> None:
        super().__init__("Exchange rates are not available right now. Try again later.")


class RateFetchError(CurrencyExchangeError):
    """Raised when every configured data source failed to return rates."""

    status_code = 502
    code = "rate_fetch_failed"

    def __init__(self, details: str) -> None:
        super().__init__(f"Failed to fetch exchange rates from all data sources. {details}")
