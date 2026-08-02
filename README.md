# Currency Exchange API

A lightweight, self-hosted, free REST API for currency exchange rates,
built with **FastAPI** and **Pydantic**. It is designed as a reliable
replacement for unstable third-party exchange-rate services, with a focus
on **USD -> NGN** conversions while supporting conversion between any of
these currencies: **USD, NGN, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR**.

Rates are fetched from free, publicly accessible sources, cached in
memory, mirrored to a JSON file for restart survival, and refreshed
automatically in the background (default every 6 hours — well within the
"at least daily" requirement).

## Chosen data sources

| Provider | Role | Free access | Coverage |
| --- | --- | --- | --- |
| [ExchangeRate-API free tier](https://open.er-api.com) (`open.er-api.com`) | Primary | Yes — keyless, no sign-up, no rate limit | Full supported set incl. **NGN** |
| [Frankfurter](https://frankfurter.app) (`api.frankfurter.app`) | Fallback | Yes — free, open (ECB data) | Major currencies, **no NGN** |

The primary source is tried first. If it is unreachable, the ECB-backed
Frankfurter fallback is used for the major currencies. Sources are
interchangeable behind a single `RateSource` interface
(`app/currency_data.py`).

## API endpoints

- `GET /health` — 200 OK status probe (includes active source, rate count, last update).
- `GET /rates?base=USD` — all rates relative to `base` (default `USD`).
- `GET /rates/{base_currency}/{target_currency}` — single conversion rate, e.g. `GET /rates/USD/NGN`.

Errors use a uniform body: `{"error": "<code>", "detail": "..."}`.
Unsupported currency codes return `400`, unavailability `503`, and total
data-source failure `502`.

## Setup and run

Requires Python 3.9+.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open the interactive docs at <http://localhost:8000/docs>.

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `RATE_BASE_CURRENCY` | `USD` | Cache base currency |
| `RATE_REFRESH_INTERVAL_SECONDS` | `21600` (6h) | Background refresh interval |
| `RATE_MAX_AGE_SECONDS` | `86400` (24h) | Treat cache as stale after this age |
| `RATE_CACHE_FILE` | `data/rates_cache.json` | JSON cache path (`""` disables) |
| `RATE_REQUEST_TIMEOUT_SECONDS` | `10` | HTTP timeout per data source |

## Extending

### Add a currency

1. Add the ISO 4217 code to `SUPPORTED_CURRENCIES` in `app/currency_data.py`.
2. Confirm at least one configured source publishes it (open.er-api.com covers most codes).

No other code changes are required.

### Add a data source

1. Subclass `RateSource` in `app/currency_data.py` and implement `async fetch(base)`.
2. Register the instance in the `RateManager.sources` list (order = priority, first success wins).

### PostgreSQL / rate limiting (roadmap)

The response models in `app/models.py` already define the stable contract
to persist: `base_currency`, `last_updated`, `source`, and `rates`. A
future PostgreSQL store can replace `_persist_cache`/`load_cache`, and
per-client rate limiting can be layered into `main.py` as FastAPI
middleware or dependencies without touching the data layer.
