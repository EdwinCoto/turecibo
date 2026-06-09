import asyncio
import logging
import os

import azure.functions as func
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from handlers.receipt_handler import handle_photo_message
from handlers.telegram_handler import (
    cmd_delete,
    cmd_excel,
    cmd_global,
    cmd_help,
    cmd_mes,
    cmd_recibo,
    cmd_restaurante,
    cmd_start,
    handle_text_message,
)
from services.telegram_client import send_message

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

# ──────────────────────────────────────────
# Telegram Application (module-level singleton)
# ──────────────────────────────────────────

_TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")


def _parse_int_set(raw_value: str | None) -> set[int]:
    if not raw_value:
        return set()

    values: set[int] = set()
    for token in raw_value.split(","):
        item = token.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError:
            logger.warning("Ignoring invalid allowlist id: %s", item)
    return values


_ALLOWED_USER_IDS = _parse_int_set(os.environ.get("TELEGRAM_ALLOWED_USER_IDS"))
_ALLOWED_CHAT_IDS = _parse_int_set(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS"))


def _is_update_allowed(update: Update) -> bool:
    # If no allowlist configured, keep current behavior (allow all).
    if not _ALLOWED_USER_IDS and not _ALLOWED_CHAT_IDS:
        return True

    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if user and _ALLOWED_USER_IDS and user.id in _ALLOWED_USER_IDS:
        return True

    if chat and _ALLOWED_CHAT_IDS and chat.id in _ALLOWED_CHAT_IDS:
        return True

    return False

_application: Application = (
    Application.builder()
    .token(_TELEGRAM_TOKEN)
    .updater(None)          # webhook mode — no built-in polling
    .build()
)
_application_initialized = False

logger.info("Function app module initialized; INFO logging enabled")


async def _handle_photo_update(update: Update) -> None:
    logger.info("_handle_photo_update: start")
    if update.message is None:
        logger.info("_handle_photo_update: skipped because update has no message")
        return
    await handle_photo_message(update.message)


async def _process_update_safe(update: Update) -> None:
    try:
        await _application.process_update(update)
    except Exception:
        logger.exception("_process_update_safe: failed to process Telegram update")


async def _notify_blocked_update(update: Update) -> None:
    chat = update.effective_chat
    if chat is None:
        logger.info("_notify_blocked_update: skipped because update has no chat")
        return

    await send_message(
        chat.id,
        "No tienes permiso para usar este bot.",
        parse_mode=None,
    )

# Register command handlers
_application.add_handler(CommandHandler("start", cmd_start))
_application.add_handler(CommandHandler("help", cmd_help))
_application.add_handler(CommandHandler("mes", cmd_mes))
_application.add_handler(CommandHandler("global", cmd_global))
_application.add_handler(CommandHandler("excel", cmd_excel))
_application.add_handler(CommandHandler("restaurante", cmd_restaurante))
_application.add_handler(CommandHandler("recibo", cmd_recibo))
_application.add_handler(CommandHandler("eliminar", cmd_delete))

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
    global _application_initialized
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

    if not _is_update_allowed(update):
        logger.warning(
            "telegram_webhook: blocked update by allowlist user_id=%s chat_id=%s",
            update.effective_user.id if update.effective_user else None,
            update.effective_chat.id if update.effective_chat else None,
        )
        await _notify_blocked_update(update)
        return func.HttpResponse(status_code=200)

    # Initialize application if not already done (first warm start)
    if not _application_initialized:
        logger.info("telegram_webhook: initializing telegram application")
        await _application.initialize()
        await _application.start()
        _application_initialized = True
        logger.info("telegram_webhook: telegram application initialized")

    # In Azure Functions, detached background tasks can be dropped when the HTTP
    # invocation completes. Await processing to guarantee handlers run.
    await _process_update_safe(update)
    logger.info("telegram_webhook: update processed")

    return func.HttpResponse(status_code=200)
