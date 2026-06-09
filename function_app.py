import asyncio
import logging
import os

import azure.functions as func
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from handlers.receipt_handler import handle_photo_message
from handlers.telegram_handler import (
    cmd_help,
    cmd_mes,
    cmd_recibo,
    cmd_restaurante,
    cmd_start,
    handle_text_message,
)

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

# ──────────────────────────────────────────
# Telegram Application (module-level singleton)
# ──────────────────────────────────────────

_TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

_application: Application = (
    Application.builder()
    .token(_TELEGRAM_TOKEN)
    .updater(None)          # webhook mode — no built-in polling
    .build()
)

logger.info("Function app module initialized; INFO logging enabled")


async def _handle_photo_update(update: Update) -> None:
    logger.info("_handle_photo_update: start")
    if update.message is None:
        logger.info("_handle_photo_update: skipped because update has no message")
        return
    await handle_photo_message(update.message)

# Register command handlers
_application.add_handler(CommandHandler("start", cmd_start))
_application.add_handler(CommandHandler("help", cmd_help))
_application.add_handler(CommandHandler("mes", cmd_mes))
_application.add_handler(CommandHandler("restaurante", cmd_restaurante))
_application.add_handler(CommandHandler("recibo", cmd_recibo))

# Register photo handler — fires for messages that contain photos
_application.add_handler(
    MessageHandler(filters.PHOTO, lambda update, ctx: _handle_photo_update(update))
)
_application.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
)

# ──────────────────────────────────────────
# Azure Functions app
# ──────────────────────────────────────────

app = func.FunctionApp()


@app.route(route="telegram/webhook", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def telegram_webhook(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("telegram_webhook: received request")

    # Validate Telegram secret token
    if _WEBHOOK_SECRET and req.headers.get("X-Telegram-Bot-Api-Secret-Token") != _WEBHOOK_SECRET:
        logger.warning("Unauthorized webhook request — secret mismatch")
        return func.HttpResponse("Unauthorized", status_code=401)

    try:
        body = req.get_json()
    except ValueError:
        body = None
    if not body:
        logger.info("telegram_webhook: invalid or empty body")
        return func.HttpResponse("Bad request", status_code=400)

    update = Update.de_json(body, _application.bot)
    logger.info("telegram_webhook: parsed update")

    # Initialize application if not already done (first warm start)
    if not _application.running:
        logger.info("telegram_webhook: initializing telegram application")
        await _application.initialize()
        logger.info("telegram_webhook: telegram application initialized")

    # Fire-and-forget: process update in background so we ACK Telegram immediately
    asyncio.ensure_future(_application.process_update(update))
    logger.info("telegram_webhook: update scheduled for background processing")

    return func.HttpResponse(status_code=200)
