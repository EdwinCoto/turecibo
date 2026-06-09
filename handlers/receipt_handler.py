import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from datetime import date

from telegram import Message, PhotoSize

from models.receipt import (
    ExtractionResult,
    ExtractionStatus,
    Receipt,
    ReceiptPhoto,
    ReceiptSource,
    ReceiptStatus,
)
from services import dni_validator, vision
from services.telegram_client import get_bot, send_message
from storage.local_store import (
    build_receipt_fingerprint,
    get_receipt_by_fingerprint,
    save_photo,
    save_receipt,
)

logger = logging.getLogger(__name__)


def _format_receipt_date(receipt: Receipt) -> str:
    logger.info("_format_receipt_date: receipt_id=%s", receipt.id)
    if receipt.receipt_date is not None:
        return receipt.receipt_date.isoformat()

    if receipt.extraction.data and receipt.extraction.data.emission_date is not None:
        return receipt.extraction.data.emission_date.isoformat()

    return receipt.created_at.date().isoformat()


def _format_success_message(receipt: Receipt, extraction_data) -> str:
    logger.info("_format_success_message: receipt_id=%s", receipt.id)
    dni_status = (
        "✅ Válido"
        if extraction_data.dni_valid is True
        else "❌ Inválido"
        if extraction_data.dni_valid is False
        else "—"
    )
    receipt_date = _format_receipt_date(receipt)

    return (
        f"✅ *Recibo procesado* `{receipt.id[:8]}`\n\n"
        f"🏪 {extraction_data.restaurant_name or 'N/A'}\n"
        f"🔢 RUC: `{extraction_data.ruc or 'N/A'}`\n"
        f"💰 Total: S/ {extraction_data.total_amount or 'N/A'}\n"
        f"🧾 IGV: S/ {extraction_data.igv_amount or 'N/A'}\n"
        f"📅 Fecha: {receipt_date}\n"
        f"🪪 DNI: `{extraction_data.dni or 'N/A'}` — {dni_status}"
    )


def _format_duplicate_message(existing_receipt: dict) -> str:
    logger.info("_format_duplicate_message: existing_receipt_id=%s", existing_receipt.get("id", "")[:8])
    receipt_id = existing_receipt.get("id", "")[:8]
    receipt_date = existing_receipt.get("receipt_date") or existing_receipt.get("created_at", "")[:10]

    return (
        f"ℹ️ El recibo `{receipt_id}` ya está almacenado.\n"
        f"📅 Fecha: {receipt_date}\n"
        f"Usa /recibo `{receipt_id}` para verlo otra vez."
    )


async def handle_photo_message(message: Message) -> None:
    """
    Entry point called for each Telegram message containing photos.
    Sends immediate ACK and fires off background processing for each photo.
    """
    chat_id = message.chat.id
    logger.info("handle_photo_message: chat_id=%s photos_received=%d", chat_id, len(message.photo))
    photo_count = len(message.photo) // 4 or 1  # Telegram sends multiple sizes; count unique photos
    logger.info("handle_photo_message: estimated_unique_photos=%d", photo_count)

    # Group media sends multiple PhotoSize arrays per photo — pick the highest resolution
    # of each unique photo (last item per group is highest res in Telegram's API)
    best_photos = _select_best_photos(message.photo)
    logger.info("handle_photo_message: best_photos_selected=%d", len(best_photos))

    ack = "📥 Recibido ✅" if len(best_photos) == 1 else f"📥 {len(best_photos)} fotos recibidas ✅"
    await send_message(chat_id, ack)

    for photo in best_photos:
        logger.info("handle_photo_message: scheduling receipt for file_id=%s", photo.file_id)
        receipt = Receipt(
            source=ReceiptSource(
                telegram_user_id=message.from_user.id if message.from_user else 0,
                telegram_chat_id=chat_id,
                telegram_message_id=message.message_id,
                telegram_file_id=photo.file_id,
                telegram_file_unique_id=photo.file_unique_id,
            )
        )
        # Save pending record immediately
        save_receipt(receipt)
        logger.info("handle_photo_message: pending receipt saved receipt_id=%s", receipt.id)
        # Process asynchronously — do not await
        import asyncio
        asyncio.ensure_future(_process_receipt(receipt, photo, chat_id))


def _select_best_photos(photos: tuple[PhotoSize, ...]) -> list[PhotoSize]:
    """
    Telegram sends each photo as multiple resolutions grouped by file_unique_id prefix.
    We pick the highest-resolution version (largest file_size) per unique photo.
    """
    logger.info("_select_best_photos: total_sizes=%d", len(photos))
    seen: dict[str, PhotoSize] = {}
    for p in photos:
        key = p.file_unique_id[:8]
        if key not in seen or (p.file_size or 0) > (seen[key].file_size or 0):
            seen[key] = p
    selected = list(seen.values())
    logger.info("_select_best_photos: selected=%d", len(selected))
    return selected


async def _process_receipt(receipt: Receipt, photo: PhotoSize, chat_id: int) -> None:
    """
    Background task: download photo → extract data via OpenAI vision → validate DNI → save.
    Sends a follow-up Telegram message with results or error.
    """
    date_str = receipt.created_at.strftime("%Y-%m-%d")
    logger.info(
        "_process_receipt: start receipt_id=%s chat_id=%s file_id=%s",
        receipt.id,
        chat_id,
        photo.file_id,
    )

    try:
        # 1. Download photo from Telegram
        bot = get_bot()
        tg_file = await bot.get_file(photo.file_id)
        photo_bytes = await tg_file.download_as_bytearray()
        photo_bytes = bytes(photo_bytes)
        logger.info("_process_receipt: photo downloaded receipt_id=%s bytes=%d", receipt.id, len(photo_bytes))

        # 2. Extract data via OpenAI vision
        extraction_data = await vision.extract_receipt_data(photo_bytes, receipt.id)
        logger.info("_process_receipt: extraction completed receipt_id=%s", receipt.id)

        # 3. Validate DNI if present
        if extraction_data.dni:
            extraction_data.dni_valid = await dni_validator.validate_dni(extraction_data.dni)
        else:
            extraction_data.dni_valid = None
        logger.info(
            "_process_receipt: dni validation receipt_id=%s dni=%s dni_valid=%s",
            receipt.id,
            extraction_data.dni,
            extraction_data.dni_valid,
        )

        receipt.receipt_date = extraction_data.emission_date
        receipt.receipt_fingerprint = build_receipt_fingerprint(
            {
                "created_at": receipt.created_at.isoformat(),
                "receipt_date": receipt.receipt_date.isoformat() if receipt.receipt_date else None,
                "extraction": {
                    "data": extraction_data.model_dump(mode="json"),
                },
            }
        )

        existing_receipt = get_receipt_by_fingerprint(receipt.receipt_fingerprint)
        if existing_receipt:
            logger.info(
                "_process_receipt: duplicate found receipt_id=%s existing_id=%s",
                receipt.id,
                existing_receipt.get("id", "")[:8],
            )
            duplicate_message = _format_duplicate_message(existing_receipt)
            logger.info(
                "_process_receipt: duplicate reply text receipt_id=%s text=%s",
                receipt.id,
                duplicate_message,
            )
            await send_message(chat_id, duplicate_message)
            return

        # 4. Save photo locally only after confirming the receipt is new
        photo_hash = hashlib.sha256(photo_bytes).hexdigest()
        photo_path: Path = save_photo(receipt.id, date_str, photo_bytes)
        receipt.photo = ReceiptPhoto(
            local_path=str(photo_path),
            size_bytes=len(photo_bytes),
            content_hash=photo_hash,
        )
        save_receipt(receipt)
        logger.info("_process_receipt: photo metadata saved receipt_id=%s", receipt.id)

        logger.info(
            "Receipt %s extracted data after local validation: %s",
            receipt.id,
            extraction_data.model_dump(mode="json"),
        )

        # 5. Update receipt with successful extraction
        receipt.extraction = ExtractionResult(
            status=ExtractionStatus.SUCCESS,
            processed_at=datetime.now(tz=timezone.utc),
            data=extraction_data,
        )
        receipt.status = ReceiptStatus.PROCESSED
        save_receipt(receipt)
        logger.info("_process_receipt: receipt processed receipt_id=%s", receipt.id)

        # 6. Notify user
        await send_message(
            chat_id,
            _format_success_message(receipt, extraction_data),
        )

    except Exception:
        logger.exception("Failed to process receipt %s", receipt.id)
        receipt.extraction = ExtractionResult(
            status=ExtractionStatus.FAILED,
            processed_at=datetime.now(tz=timezone.utc),
            error="Processing failed — see server logs.",
        )
        receipt.status = ReceiptStatus.FAILED
        save_receipt(receipt)
        logger.info("_process_receipt: receipt marked failed receipt_id=%s", receipt.id)
        await send_message(
            chat_id,
            f"❌ No pude procesar el recibo `{receipt.id[:8]}`.\nIntenta enviando la foto de nuevo.",
        )
