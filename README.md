# TuRecibo

TuRecibo is a Telegram bot + Azure Functions app that extracts structured data from restaurant receipt photos and stores receipts in JSON (local filesystem or Azure Blob Storage).

## What it does

- Receives receipt photos from Telegram (webhook mode)
- Extracts receipt fields with vision AI
- Validates key fields (RUC, DNI, electronic receipt number)
- Enforces emission date extraction before saving
- Stores JSON and photo using local or Azure backend
- Exposes query/export commands in Telegram

## Tech stack

- Python 3.10+
- Azure Functions (Python v2 programming model)
- python-telegram-bot 21.x
- OpenAI API (or GitHub Models token)
- openpyxl (Excel export)
- Pydantic v2

## Prerequisites

- Python 3.10 or later
- Azure Functions Core Tools (for local runtime)
- ngrok (for local Telegram webhook testing)
- Telegram bot token from BotFather

## Project setup

1. Create and activate virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
pip install pytest
```

3. Create local runtime settings

```bash
cp local.settings.json.template local.settings.json
```

Then edit local.settings.json with real values.

4. Create .env for webhook registration script

The webhook script scripts/set_webhook.py reads values from .env.
Create a .env in the project root with at least:

```dotenv
TELEGRAM_BOT_TOKEN=<your-bot-token>
TELEGRAM_WEBHOOK_SECRET=<your-random-secret>
FUNCTION_BASE_URL=https://<your-ngrok-or-deployed-url>
```

## Local run

1. Start Azure Functions host

```bash
func start --port 7071
```

2. Start ngrok in another terminal

```bash
ngrok http 7071
```

3. Update .env and local.settings.json FUNCTION_BASE_URL with your ngrok HTTPS URL

Example:

```text
https://abc123.ngrok-free.app
```

## Important: register webhook before Telegram testing

Before sending photos or commands to the bot, run the webhook registration script:

```bash
python scripts/set_webhook.py
```

Why: Telegram will only deliver updates to your webhook URL after registration.

Expected output includes webhook URL and pending updates count.

## Build and test

Python projects in this repo do not require a compile step. Build validation is dependency install + runtime startup.

### Validate runtime startup

```bash
func start --port 7071
```

You should see the HTTP route:

```text
/api/telegram/webhook
```

### Run automated tests

Run full test suite:

```bash
python -m pytest
```

Run receipt-focused tests only:

```bash
python -m pytest tests/test_receipt.py -q
```

## Storage backends

Controlled by STORAGE_BACKEND in local.settings.json.

- local: saves under data/receipts
- azure: saves to Azure Blob Storage
- google_drive: saves to Google Drive folders

For Azure backend, configure:

- AZURE_STORAGE_CONNECTION_STRING
- AZURE_STORAGE_CONTAINER
- AZURE_RECEIPTS_PREFIX
- AZURE_PHOTOS_PREFIX

### Google Drive OAuth setup (recommended for personal Drive)

1. Create OAuth client JSON in Google Cloud:
   - Open `console.cloud.google.com`
   - Go to **APIs & Services → Credentials**
   - Click **+ Create Credentials → OAuth client ID**
   - Choose **Desktop app**
   - Download the JSON file (example: `oauth-client.json`)

2. Configure `local.settings.json`:

```json
"STORAGE_BACKEND": "google_drive",
"GOOGLE_DRIVE_CREDENTIALS_FILE": "/absolute/path/to/oauth-client.json",
"GOOGLE_DRIVE_TOKEN_FILE": "google_drive_token.json",
"GOOGLE_DRIVE_ROOT_FOLDER_ID": "<google-drive-folder-id>",
"GOOGLE_DRIVE_RECEIPTS_FOLDER": "receipts",
"GOOGLE_DRIVE_PHOTOS_FOLDER": "photos"
```

3. Generate the OAuth token (one-time):

```bash
python scripts/generate_google_drive_token.py
```

This opens a browser for Google consent and creates `google_drive_token.json`.
After token generation, the bot can upload files to your Drive folder.

## Secret scanning (prevent leaked credentials)

**GitHub CI (mandatory enforcement):** Secrets are scanned on every push via `.github/workflows/secret-scan.yml` (Gitleaks). PRs with detected secrets **cannot merge to main** — this is the enforcement boundary and cannot be bypassed locally.

**Local detection (convenience):** Pre-commit hooks catch mistakes before they are staged, and a manual scan script is available:

Run local scan before committing:

```bash
scripts/scan_secrets.sh
```

Enable pre-commit secret scanning for staged files:

```bash
scripts/install_git_hooks.sh
```

Note: Pre-commit hooks can be bypassed (`git commit --no-verify`), so the CI workflow on GitHub's servers is the actual blocker. The hook is convenience only.

**Deployment safety:** The Azure Functions deployment pipeline also excludes common credential/token files (`.env`, `credentials.json`, etc.) even if they exist locally.

## Bot commands

- /start
- /help
- /mes [MM|YYYY-MM]
- /global [YYYY]
- /excel [YYYY]
- /restaurante <RUC>
- /recibo <id>
- /sync <id>
- /eliminar <id>

## Data rules currently enforced

- Receipt is not saved if emission date is missing
- Receipt is not saved if RUC is missing/invalid
- Receipt is not saved if DNI is missing
- Duplicate receipts are blocked by fingerprint

## Troubleshooting

1. Bot does not respond
- Ensure func start is running
- Ensure ngrok URL is active
- Re-run python scripts/set_webhook.py
- Confirm TELEGRAM_WEBHOOK_SECRET matches both registration and function settings

2. Webhook registration fails
- Verify TELEGRAM_BOT_TOKEN and FUNCTION_BASE_URL in .env
- Ensure FUNCTION_BASE_URL is HTTPS and reachable

3. 401 in webhook endpoint
- Secret mismatch between Telegram webhook and TELEGRAM_WEBHOOK_SECRET

4. No receipts saved
- Check logs for missing emission date, missing DNI, or invalid RUC validation messages

## Useful files

- function_app.py: Azure Functions entrypoint and Telegram handler wiring
- handlers/receipt_handler.py: photo processing workflow and validations
- handlers/telegram_handler.py: command handlers and Excel export
- services/vision.py: AI extraction and normalization
- storage/receipt_store.py: storage facade
- storage/local_backend.py: local filesystem storage
- storage/azure_backend.py: Azure Blob storage
- storage/google_drive_backend.py: Google Drive storage
- scripts/set_webhook.py: webhook registration script
- scripts/generate_google_drive_token.py: Google Drive OAuth token generation
