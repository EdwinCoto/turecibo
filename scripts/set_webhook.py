"""
One-time script to register the webhook URL with Telegram.
Run this locally after deploying or after starting ngrok:

    python scripts/set_webhook.py
"""
import asyncio
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telegram import Bot


async def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    base_url = os.environ["FUNCTION_BASE_URL"].rstrip("/")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

    bot = Bot(token=token)
    webhook_url = f"{base_url}/api/telegram/webhook"

    await bot.set_webhook(
        url=webhook_url,
        secret_token=secret or None,
    )

    info = await bot.get_webhook_info()
    print(f"✅ Webhook registered:")
    print(f"   URL    : {info.url}")
    print(f"   Pending: {info.pending_update_count}")
    if info.last_error_message:
        print(f"   ⚠️  Last error: {info.last_error_message}")


if __name__ == "__main__":
    asyncio.run(main())
