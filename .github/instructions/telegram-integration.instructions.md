---
description: "Use when building Telegram bot integrations with Azure Functions, handling webhook updates, parsing Telegram messages, sending replies, verifying bot tokens, or setting up the Telegram webhook endpoint. Covers python-telegram-bot library patterns with the v2 HTTP trigger model."
applyTo: "**/*.py"
---

# Telegram Bot Integration — Azure Functions v2 + python-telegram-bot

## Dependencies

```txt
# requirements.txt
python-telegram-bot==20.*
azure-functions
```

## Webhook Function Structure

```python
import azure.functions as func
import logging
import json
import os
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters

app = func.FunctionApp()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# Build the Application once (module-level) to reuse across warm invocations
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

@app.route(route="telegram/webhook", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def telegram_webhook(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        body = None
    if not body:
        return func.HttpResponse("Bad request", status_code=400)

    update = Update.de_json(body, application.bot)
    await application.process_update(update)
    return func.HttpResponse(status_code=200)
```

## Handler Patterns

```python
from telegram import Update
from telegram.ext import ContextTypes

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hello! I'm your bot.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    logging.info("Received message: %s", text)
    await update.message.reply_text(f"You said: {text}")
```

## Webhook Registration

Register the webhook once after deploying (run this script locally or in CI):

```python
import asyncio
from telegram import Bot
import os

async def set_webhook():
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    url = os.environ["FUNCTION_BASE_URL"] + "/api/telegram/webhook"
    await bot.set_webhook(url=url)
    info = await bot.get_webhook_info()
    print(info)

asyncio.run(set_webhook())
```

## Security — Validate the Secret Token

Telegram supports a `secret_token` header to verify requests come from Telegram, not the public internet:

```python
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]

@app.route(route="telegram/webhook", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def telegram_webhook(req: func.HttpRequest) -> func.HttpResponse:
    if req.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return func.HttpResponse("Unauthorized", status_code=401)
    ...
```

Set it during webhook registration:
```python
await bot.set_webhook(url=url, secret_token=os.environ["TELEGRAM_WEBHOOK_SECRET"])
```

## Required Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Random secret to authenticate Telegram requests |
| `FUNCTION_BASE_URL` | Base URL of the deployed Function App |

Store all values in **App Settings** (Azure portal or `local.settings.json` locally). Never hardcode.

## local.settings.json (local dev only — never commit)

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "TELEGRAM_BOT_TOKEN": "<your-token>",
    "TELEGRAM_WEBHOOK_SECRET": "<random-secret>",
    "FUNCTION_BASE_URL": "https://<ngrok-or-deployed>.azurewebsites.net"
  }
}
```

## Local Testing with ngrok

Telegram requires a public HTTPS URL for webhooks. Use ngrok during local dev:

```bash
func start          # start Azure Functions locally on port 7071
ngrok http 7071     # expose it publicly
# then re-register the webhook with the ngrok URL
```

## Anti-patterns

- Never use `bot.polling()` inside a Function — it blocks indefinitely.
- Don't create a new `Application` instance per request — build it at module level.
- Don't log full `Update` objects — they may contain user PII.
