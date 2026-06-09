import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from storage.local_store import (
    get_receipt_by_id,
    get_receipts_by_month,
    get_receipts_by_ruc,
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
        "🏪 `/restaurante <RUC>` — valida si un restaurante tiene recibos\n"
        "🧾 `/recibo <id>` — detalle completo + foto de un recibo",
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
    if not receipts:
        await update.message.reply_text(f"📭 No hay recibos registrados para {month}.")
        return

    lines = [f"🗓 *Recibos de {month}* ({len(receipts)} encontrados)\n"]
    for r in receipts:
        data = (r.get("extraction") or {}).get("data") or {}
        status_icon = {"processed": "✅", "pending": "⏳", "failed": "❌"}.get(r.get("status", ""), "❓")
        dni_valid = data.get("dni_valid")
        dni_icon = "🪪✓" if dni_valid is True else "🪪✗" if dni_valid is False else "🪪?"
        receipt_date = r.get("receipt_date") or r.get("created_at", "")[:10]
        lines.append(
            f"{status_icon} `{r['id'][:8]}`  {dni_icon}\n"
            f"   🏪 {data.get('restaurant_name') or 'N/A'}\n"
            f"   💰 S/ {data.get('total_amount', '?')}  IGV: S/ {data.get('igv_amount', '?')}\n"
            f"   📅 {receipt_date}\n"
        )

    for chunk in _chunk_message("\n".join(lines)):
        await update.message.reply_text(chunk, parse_mode="Markdown")


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
        f"💰 Total: S/ {data.get('total_amount', 'N/A')}\n"
        f"🧾 IGV: S/ {data.get('igv_amount', 'N/A')}\n"
        f"🪪 DNI: `{data.get('dni') or 'N/A'}` — {dni_status}\n"
        f"📅 Fecha: {receipt.get('receipt_date') or receipt['created_at'][:10]}\n"
        f"🔄 Estado: {receipt.get('status', 'N/A')}"
    )

    photo_path_str: Optional[str] = (receipt.get("photo") or {}).get("local_path")
    if photo_path_str:
        photo_path = Path(photo_path_str)
        if photo_path.exists():
            logger.info("cmd_recibo: sending photo receipt_id=%s", receipt.get("id", "")[:8])
            with photo_path.open("rb") as f:
                await update.message.reply_photo(photo=f, caption=caption, parse_mode="Markdown")
            return

    logger.info("cmd_recibo: sending text-only receipt_id=%s", receipt.get("id", "")[:8])
    await update.message.reply_text(caption, parse_mode="Markdown")
