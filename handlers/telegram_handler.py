import logging
import re
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openpyxl import Workbook
from telegram import Update
from telegram.ext import ContextTypes

from models.receipt import Receipt
from services import electronic_receipt_validator, vision

from storage.receipt_store import (
    delete_receipt_by_id,
    get_photo_bytes,
    get_receipt_by_id,
    get_receipts_by_month,
    get_receipts_by_ruc,
    save_receipt,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# Utility
# ──────────────────────────────────────────

def _chunk_message(text: str, limit: int = 4000) -> list[str]:
    logger.info("_chunk_message: start text_len=%d limit=%d", len(text), limit)
    lines = text.split("\n")
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)
    logger.info("_chunk_message: generated chunks=%d", len(chunks))
    return chunks


def _month_name_es(month_number: int) -> str:
    month_names = (
        "Enero",
        "Febrero",
        "Marzo",
        "Abril",
        "Mayo",
        "Junio",
        "Julio",
        "Agosto",
        "Septiembre",
        "Octubre",
        "Noviembre",
        "Diciembre",
    )
    if month_number < 1 or month_number > 12:
        raise ValueError(f"Invalid month value: {month_number}")
    return month_names[month_number - 1]


# ──────────────────────────────────────────
# /start  /help
# ──────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_start: handling start command")
    await update.message.reply_text(
        "👋 ¡Hola! Soy *TuRecibo Bot*.\n\n"
        "📸 Envíame fotos de tus boletas de restaurantes y las procesaré automáticamente.\n\n"
        "Usa /help para ver todos los comandos disponibles.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_help: handling help command")
    await update.message.reply_text(
        "📋 *Comandos disponibles*\n\n"
        "📸 *Enviar foto* — manda una foto de tu boleta y la proceso automáticamente\n\n"
        "🗓 `/mes` — recibos del mes actual\n"
        "🗓 `/mes MM` — recibos de un mes (ej: `/mes 03`)\n"
        "🗓 `/mes YYYY-MM` — recibos de un mes específico (ej: `/mes 2024-03`)\n\n"
        "📅 `/global` — recibos del año actual, ordenados por mes\n"
        "📅 `/global YYYY` — recibos de un año específico (ej: `/global 2026`)\n\n"
        "📄 `/excel` — exporta recibos del año actual a Excel\n"
        "📄 `/excel YYYY` — exporta recibos de un año específico a Excel\n\n"
        "🏪 `/restaurante <RUC>` — valida si un restaurante tiene recibos\n"
        "🧾 `/recibo <id>` — detalle completo + foto de un recibo\n"
        "🔄 `/sync <id>` — sincroniza el número de boleta electrónica\n"
        "🗑 `/eliminar <id>` — elimina recibo y archivos asociados",
        parse_mode="Markdown",
    )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("handle_text_message: received text message")
    if not update.message:
        logger.info("handle_text_message: update has no message")
        return

    await update.message.reply_text(
        "📸 Envíame una foto de tu boleta para poder procesarla.",
    )


# ──────────────────────────────────────────
# /mes
# ──────────────────────────────────────────

async def cmd_mes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_mes: received args=%s", context.args)
    args = context.args
    now = datetime.now()
    try:
        if not args:
            month = now.strftime("%Y-%m")
        elif re.fullmatch(r"\d{4}-\d{2}", args[0]):
            month = args[0]
            year, m = month.split("-")
            datetime(int(year), int(m), 1)
        elif re.fullmatch(r"\d{2}", args[0]):
            month = f"{now.year}-{args[0]}"
            datetime(now.year, int(args[0]), 1)
        else:
            raise ValueError("bad format")
    except (ValueError, IndexError):
        logger.info("cmd_mes: invalid month format args=%s", args)
        await update.message.reply_text(
            "❌ Formato inválido.\n"
            "Usa: `/mes YYYY-MM`  →  `/mes 2024-03`\n"
            "  o: `/mes MM`       →  `/mes 03`  (usa el año actual)",
            parse_mode="Markdown",
        )
        return

    receipts = get_receipts_by_month(month)
    logger.info("cmd_mes: month=%s receipts_found=%d", month, len(receipts))
    try:
        year, month_num = month.split("-")
        month_label = f"{_month_name_es(int(month_num))} {year}"
    except ValueError:
        logger.warning("cmd_mes: invalid computed month=%s", month)
        await update.message.reply_text(
            f"❌ Fecha incorrecta: el mes `{month}` no es válido.",
            parse_mode="Markdown",
        )
        return
    if not receipts:
        await update.message.reply_text(f"📭 No hay recibos registrados para {month_label}.")
        return

    lines = [f"🗓 *Recibos de {month_label}* ({len(receipts)} encontrados)\n"]
    for r in receipts:
        data = (r.get("extraction") or {}).get("data") or {}
        status_icon = {"processed": "✅", "pending": "⏳", "failed": "❌"}.get(r.get("status", ""), "❓")
        dni_valid = data.get("dni_valid")
        dni_icon = "🪪✓" if dni_valid is True else "🪪✗" if dni_valid is False else "🪪?"
        receipt_date = r.get("receipt_date") or r.get("created_at", "")[:10]
        lines.append(
            f"{status_icon} `{r['id'][:8]}`  {dni_icon}\n"
            f"   🏪 {data.get('restaurant_name') or 'N/A'}\n"
            f"   💰 S/ {data.get('total_amount', '?')}\n"
            f"   📅 {receipt_date}\n"
        )

    for chunk in _chunk_message("\n".join(lines)):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_global: received args=%s", context.args)
    args = context.args
    now = datetime.now()

    try:
        if not args:
            year = now.year
        elif re.fullmatch(r"\d{4}", args[0]):
            year = int(args[0])
            datetime(year, 1, 1)
        else:
            raise ValueError("bad format")
    except (ValueError, IndexError):
        logger.info("cmd_global: invalid year format args=%s", args)
        await update.message.reply_text(
            "❌ Formato inválido.\n"
            "Usa: `/global YYYY`  →  `/global 2026`\n"
            "  o: `/global`       →  usa el año actual",
            parse_mode="Markdown",
        )
        return

    monthly_data: list[tuple[int, list[dict]]] = []
    total_receipts = 0
    for month_number in range(1, 13):
        month_key = f"{year}-{month_number:02d}"
        receipts = [
            receipt
            for receipt in get_receipts_by_month(month_key)
            if receipt.get("status") == "processed"
        ]
        if receipts:
            monthly_data.append((month_number, receipts))
            total_receipts += len(receipts)

    logger.info("cmd_global: year=%d months_with_data=%d receipts_found=%d", year, len(monthly_data), total_receipts)

    if total_receipts == 0:
        await update.message.reply_text(f"📭 No hay recibos registrados para {year}.")
        return

    lines = [f"📅 *Recibos de {year}* ({total_receipts} encontrados)\n"]
    for month_number, receipts in monthly_data:
        try:
            month_name = _month_name_es(month_number)
        except ValueError:
            logger.warning("cmd_global: invalid month number in aggregation=%s", month_number)
            await update.message.reply_text(
                f"❌ Fecha incorrecta: el mes `{year}-{month_number:02d}` no es válido.",
                parse_mode="Markdown",
            )
            return

        lines.append(f"\n*{month_name}* ({len(receipts)})")
        for r in receipts:
            data = (r.get("extraction") or {}).get("data") or {}
            status_icon = {"processed": "✅", "pending": "⏳", "failed": "❌"}.get(r.get("status", ""), "❓")
            receipt_date = r.get("receipt_date") or r.get("created_at", "")[:10]
            lines.append(
                f"{status_icon} `{r['id'][:8]}` — {receipt_date}\n"
                f"   🏪 {data.get('restaurant_name') or 'N/A'}\n"
                f"   🔢 RUC: `{data.get('ruc') or 'N/A'}`\n"
                f"   🧾 Boleta: `{data.get('electronic_receipt_number') or 'N/A'}`\n"
                f"   💰 S/ {data.get('total_amount', '?')}"
            )

    for chunk in _chunk_message("\n".join(lines)):
        await update.message.reply_text(chunk, parse_mode="Markdown")


def _build_excel(year: int, monthly_data: list[tuple[int, list[dict]]]) -> BytesIO:
    workbook = Workbook()
    # Remove default empty sheet.
    workbook.remove(workbook.active)

    headers = ["RUC", "NOMBRE DEL COMERCIO O RESTAURANTE", "FECHA", "MONTO", "DNI"]

    for month_number, receipts in monthly_data:
        sheet_name = _month_name_es(month_number)[:31]
        sheet = workbook.create_sheet(title=sheet_name)
        sheet.append(headers)

        sorted_receipts = sorted(
            receipts,
            key=lambda r: r.get("receipt_date") or r.get("created_at", "")[:10],
        )

        for receipt in sorted_receipts:
            data = (receipt.get("extraction") or {}).get("data") or {}
            receipt_date = receipt.get("receipt_date") or receipt.get("created_at", "")[:10]
            sheet.append(
                [
                    data.get("ruc") or "",
                    data.get("restaurant_name") or "",
                    receipt_date,
                    data.get("total_amount") if data.get("total_amount") is not None else "",
                    data.get("dni") or "",
                ]
            )

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


async def cmd_excel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_excel: received args=%s", context.args)
    args = context.args
    now = datetime.now()

    try:
        if not args:
            year = now.year
        elif re.fullmatch(r"\d{4}", args[0]):
            year = int(args[0])
            datetime(year, 1, 1)
        else:
            raise ValueError("bad format")
    except (ValueError, IndexError):
        logger.info("cmd_excel: invalid year format args=%s", args)
        await update.message.reply_text(
            "❌ Formato inválido.\n"
            "Usa: `/excel YYYY`  →  `/excel 2026`\n"
            "  o: `/excel`       →  usa el año actual",
            parse_mode="Markdown",
        )
        return

    monthly_data: list[tuple[int, list[dict]]] = []
    total_receipts = 0
    for month_number in range(1, 13):
        month_key = f"{year}-{month_number:02d}"
        receipts = get_receipts_by_month(month_key)
        if receipts:
            monthly_data.append((month_number, receipts))
            total_receipts += len(receipts)

    logger.info("cmd_excel: year=%d months_with_data=%d receipts_found=%d", year, len(monthly_data), total_receipts)

    if total_receipts == 0:
        await update.message.reply_text(f"📭 No hay recibos registrados para {year}.")
        return

    excel_file = _build_excel(year, monthly_data)
    await update.message.reply_document(
        document=excel_file,
        filename=f"recibos-{year}.xlsx",
        caption=f"📄 Exportación completada: {total_receipts} recibos de {year}.",
    )


# ──────────────────────────────────────────
# /restaurante
# ──────────────────────────────────────────

async def cmd_restaurante(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_restaurante: received args=%s", context.args)
    if not context.args or not re.fullmatch(r"\d{11}", context.args[0]):
        logger.info("cmd_restaurante: invalid ruc args=%s", context.args)
        await update.message.reply_text(
            "❌ Proporciona un RUC válido (11 dígitos).\nEjemplo: `/restaurante 20123456789`",
            parse_mode="Markdown",
        )
        return

    ruc = context.args[0]
    receipts = get_receipts_by_ruc(ruc)
    logger.info("cmd_restaurante: ruc=%s receipts_found=%d", ruc, len(receipts))

    if not receipts:
        await update.message.reply_text(
            f"🔍 RUC `{ruc}` — Sin recibos registrados.", parse_mode="Markdown"
        )
        return

    name = (receipts[0].get("extraction") or {}).get("data", {}).get("restaurant_name", "Desconocido")
    lines = [f"🏪 *{name}*\n🔢 RUC: `{ruc}`\n📄 Recibos encontrados: {len(receipts)}\n"]
    for r in receipts[-5:]:
        data = (r.get("extraction") or {}).get("data") or {}
        receipt_date = r.get("receipt_date") or r.get("created_at", "")[:10]
        lines.append(f"• `{r['id'][:8]}` — S/ {data.get('total_amount', '?')} — {receipt_date}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ──────────────────────────────────────────
# /recibo
# ──────────────────────────────────────────

async def cmd_recibo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_recibo: received args=%s", context.args)
    if not context.args:
        logger.info("cmd_recibo: missing receipt id")
        await update.message.reply_text(
            "❌ Proporciona el ID del recibo.\nEjemplo: `/recibo abc12345`",
            parse_mode="Markdown",
        )
        return

    receipt = get_receipt_by_id(context.args[0])
    if not receipt:
        logger.info("cmd_recibo: receipt not found receipt_id=%s", context.args[0])
        await update.message.reply_text(
            f"❌ Recibo `{context.args[0]}` no encontrado.", parse_mode="Markdown"
        )
        return

    data = (receipt.get("extraction") or {}).get("data") or {}
    dni_valid = data.get("dni_valid")
    dni_status = "✅ Válido" if dni_valid is True else "❌ Inválido / No encontrado"

    caption = (
        f"🧾 *Recibo* `{receipt['id'][:8]}`\n\n"
        f"🏪 Restaurante: {data.get('restaurant_name') or 'N/A'}\n"
        f"🔢 RUC: `{data.get('ruc') or 'N/A'}`\n"
        f"🧾 Boleta: `{data.get('electronic_receipt_number') or 'N/A'}`\n"
        f"💰 Total: S/ {data.get('total_amount', 'N/A')}\n"
        f" DNI: `{data.get('dni') or 'N/A'}` — {dni_status}\n"
        f"📅 Fecha: {receipt.get('receipt_date') or receipt['created_at'][:10]}\n"
        f"🔄 Estado: {receipt.get('status', 'N/A')}"
    )

    photo_path_str: Optional[str] = (receipt.get("photo") or {}).get("local_path")
    if photo_path_str:
        photo_bytes = get_photo_bytes(photo_path_str)
        if photo_bytes:
            logger.info("cmd_recibo: sending photo receipt_id=%s", receipt.get("id", "")[:8])
            await update.message.reply_photo(photo=BytesIO(photo_bytes), caption=caption, parse_mode="Markdown")
            return

    logger.info("cmd_recibo: sending text-only receipt_id=%s", receipt.get("id", "")[:8])
    await update.message.reply_text(caption, parse_mode="Markdown")


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_sync: received args=%s", context.args)
    if not context.args:
        await update.message.reply_text(
            "❌ Proporciona el ID del recibo.\nEjemplo: `/sync abc12345`",
            parse_mode="Markdown",
        )
        return

    receipt = get_receipt_by_id(context.args[0].strip())
    if not receipt:
        await update.message.reply_text(
            f"❌ Recibo `{context.args[0]}` no encontrado.",
            parse_mode="Markdown",
        )
        return

    receipt_id = receipt.get("id", "")
    extraction = (receipt.get("extraction") or {})
    data = (extraction.get("data") or {})
    existing_receipt_number = data.get("electronic_receipt_number")

    if existing_receipt_number:
        await update.message.reply_text(
            "✅ Este recibo ya tiene número de boleta sincronizado.\n"
            f"🧾 Boleta: `{existing_receipt_number}`",
            parse_mode="Markdown",
        )
        return

    photo_path = (receipt.get("photo") or {}).get("local_path")
    if not photo_path:
        await update.message.reply_text(
            f"❌ El recibo `{receipt_id[:8]}` no tiene foto asociada para reextraer datos.",
            parse_mode="Markdown",
        )
        return

    photo_bytes = get_photo_bytes(photo_path)
    if not photo_bytes:
        await update.message.reply_text(
            f"❌ No pude leer la foto almacenada para el recibo `{receipt_id[:8]}`.",
            parse_mode="Markdown",
        )
        return

    extraction_data = await vision.extract_receipt_data(photo_bytes, receipt_id)
    normalized_receipt_number = electronic_receipt_validator.normalize_electronic_receipt_number(
        extraction_data.electronic_receipt_number
    )
    if not normalized_receipt_number or not await electronic_receipt_validator.validate_electronic_receipt_number(
        normalized_receipt_number
    ):
        await update.message.reply_text(
            "⚠️ No se pudo extraer un número de boleta electrónica válido desde la imagen.",
            parse_mode="Markdown",
        )
        return

    payload = dict(receipt)
    extraction_payload = dict(extraction)
    data_payload = dict(data)
    photo_payload = dict(payload.get("photo") or {})
    if photo_payload and photo_payload.get("size_bytes") is None:
        photo_payload["size_bytes"] = len(photo_bytes)
    data_payload["electronic_receipt_number"] = normalized_receipt_number
    extraction_payload["data"] = data_payload
    extraction_payload["processed_at"] = datetime.now(tz=timezone.utc).isoformat()
    if extraction_payload.get("status") == "pending":
        extraction_payload["status"] = "success"
    payload["photo"] = photo_payload
    payload["extraction"] = extraction_payload

    updated_receipt = Receipt.model_validate(payload)
    save_receipt(updated_receipt)

    await update.message.reply_text(
        "✅ Sincronización completada.\n"
        f"🧾 Boleta: `{normalized_receipt_number}`",
        parse_mode="Markdown",
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_delete: received args=%s", context.args)
    if not context.args:
        await update.message.reply_text(
            "❌ Proporciona el ID del recibo a eliminar.\nEjemplo: `/eliminar abc12345`",
            parse_mode="Markdown",
        )
        return

    receipt_id = context.args[0].strip()
    deleted = delete_receipt_by_id(receipt_id)
    if not deleted:
        await update.message.reply_text(
            f"❌ Recibo `{receipt_id}` no encontrado.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        f"✅ Recibo `{receipt_id}` eliminado con sus archivos relacionados.",
        parse_mode="Markdown",
    )
