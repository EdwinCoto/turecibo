import logging
import os
from typing import Optional

from telegram import Bot

logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None


def get_bot() -> Bot:
    global _bot
    logger.info("get_bot: request bot instance")
    if _bot is None:
        _bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
        logger.info("get_bot: bot instance created")
    else:
        logger.info("get_bot: reusing existing bot instance")
    return _bot


async def send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> None:
    """Send a plain text message to a chat, swallowing errors to avoid crashing background tasks."""
    preview = text.replace("\n", "\\n")[:300]
    logger.info(
        "send_message: chat_id=%s text_len=%d parse_mode=%s preview=%s",
        chat_id,
        len(text),
        parse_mode,
        preview,
    )
    try:
        await get_bot().send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        logger.info("send_message: sent chat_id=%s", chat_id)
    except Exception:
        logger.exception("Failed to send Telegram message to chat %d", chat_id)
