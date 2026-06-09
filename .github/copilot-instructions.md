# Project Guidelines

## What This App Does

**TuRecibo** is a restaurant receipt manager. Users send receipt photos to a Telegram bot; the app asynchronously extracts structured data using OpenAI vision (via GitHub Copilot API) and stores the result as JSON locally (database integration is a future stage).

**Key flow:**
```
User sends photo(s) to Telegram bot
  → Webhook hits Azure Function (HTTP trigger)
  → Immediately ACK to user ("Recibido ✅")
  → Background task: download photo → call OpenAI vision → validate DNI → save JSON + photo
```

## Architecture

- **Runtime**: Azure Functions v2, Python 3.10+
- **Telegram**: `python-telegram-bot` v20, webhook mode (no polling)
- **Vision AI**: OpenAI API (GitHub Copilot-provided) for receipt data extraction
- **Storage (current)**: Local filesystem under `./data/receipts/`
- **Storage (future)**: Azure Cosmos DB or PostgreSQL — storage layer is abstracted behind a `storage/` module

### Project Structure

```
turecibo/
├── function_app.py          # Azure Functions entrypoint, route definitions
├── handlers/
│   ├── telegram_handler.py  # Telegram update routing (commands, photos)
│   └── receipt_handler.py   # Orchestrates download → extract → validate → save
├── services/
│   ├── vision.py            # OpenAI vision calls, prompt templates
│   ├── dni_validator.py     # DNI format + existence validation
│   └── telegram_client.py   # Thin wrapper around python-telegram-bot bot instance
├── storage/
│   └── local_store.py       # Save/read receipt JSON and photo files locally
├── models/
│   └── receipt.py           # Pydantic models for Receipt, ExtractionResult
├── data/
│   └── receipts/            # Local JSON + photo files (gitignored)
├── requirements.txt
├── local.settings.json      # Local env vars (gitignored)
└── tests/
```

## Receipt JSON Schema

Every processed receipt is stored as a single JSON file. Follow this structure:

```json
{
  "id": "<uuid4>",
  "created_at": "<ISO 8601>",
  "status": "pending | processed | failed",
  "source": {
    "telegram_user_id": 123456,
    "telegram_chat_id": 123456,
    "telegram_message_id": 789,
    "telegram_file_id": "<telegram_file_id>"
  },
  "photo": {
    "local_path": "data/receipts/2024-01-15/abc123.jpg",
    "size_bytes": 204800
  },
  "extraction": {
    "status": "pending | success | failed",
    "processed_at": "<ISO 8601 or null>",
    "error": null,
    "data": {
      "restaurant_name": "Restaurante El Buen Sabor",
      "ruc": "20123456789",
      "total_amount": 59.00,
      "igv_amount": 9.00,
      "igv_rate": 0.18,
      "currency": "PEN",
      "dni": "12345678",
      "dni_valid": true
    }
  }
}
```

- `status` at root reflects the overall record state.
- `extraction.data` is `null` if extraction failed or is still pending.
- `dni_valid` is `true` (format + existence OK), `false` (invalid), or `null` (not found in receipt).

## Domain Rules

### DNI (Documento Nacional de Identidad — Perú)
- Must be exactly **8 numeric digits**.
- Must be validated for **existence** via RENIEC API (or a mock stub for local testing).
- If DNI is missing or invalid, set `dni_valid: false` — the receipt is still saved but flagged.
- DNI validation is required for tax reduction (`reducción de impuesto`) eligibility.

### RUC (Registro Único de Contribuyentes — Perú)
- Must be exactly **11 numeric digits**.
- Validate format only (existence check is a future enhancement).

### IGV (Impuesto General a las Ventas)
- Standard rate: **18%**. Extract from receipt; if not visible, compute as `total * (0.18 / 1.18)`.
- Always store both `igv_amount` and `total_amount` as floats rounded to 2 decimal places.

## Async Processing Pattern

The Telegram webhook handler must respond within **~2 seconds** or Telegram will retry. Use Python's `asyncio` for background tasks:

```python
import asyncio

async def telegram_webhook(req: func.HttpRequest) -> func.HttpResponse:
    update = Update.de_json(req.get_json(silent=True), bot)
    # Fire-and-forget — do not await
    asyncio.ensure_future(process_receipt(update))
    return func.HttpResponse(status_code=200)
```

- Acknowledge immediately; processing happens in the background.
- After processing, send the user a follow-up message with extraction results or an error notice.
- Support **multiple photos** per message: iterate over `message.photo` list; each photo is a separate receipt task.

## Code Style

- Python 3.10+, PEP 8, type hints everywhere.
- Use **Pydantic** models (`models/receipt.py`) for all data structures — never raw dicts.
- Business logic lives in `services/` and `handlers/` — not in `function_app.py`.
- `function_app.py` only defines routes and wires up handlers.
- Structured logging via `logging.getLogger(__name__)`, never `print()`.
- All secrets via `os.environ["VAR_NAME"]`. Never hardcode.

## Build and Test

Always use a virtual environment. Never install packages globally.

```bash
# Create and activate venv (run once)
python -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Run locally (requires Azure Functions Core Tools)
func start

# Run tests
pytest
```

- The venv lives at `.venv/` (gitignored).
- Always activate the venv before running `func start`, `pytest`, or any `pip` command.
- If adding a new package: `pip install <pkg> && pip freeze > requirements.txt`

For local webhook testing, expose port 7071 with ngrok and register the ngrok URL with Telegram.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Secret header to verify Telegram requests |
| `OPENAI_API_KEY` | OpenAI key (GitHub Copilot-provided) |
| `FUNCTION_BASE_URL` | Public base URL of the Function App |
| `LOCAL_STORAGE_PATH` | Base path for local receipt storage (default: `./data/receipts`) |
| `RENIEC_API_URL` | RENIEC DNI validation endpoint (use mock for local dev) |
| `RENIEC_API_KEY` | API key for RENIEC service |
