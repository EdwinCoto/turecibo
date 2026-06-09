---
description: "Use when implementing Telegram bot commands for querying, validating, or browsing receipt data. Covers command handlers, monthly receipt listing, restaurant validation by RUC, receipt detail views with photo, and reply formatting patterns."
applyTo: "**/*.py"
---

# Telegram Bot Commands — Receipt Management

## Command Registry

Register all commands with BotFather description and in `function_app.py`:

| Command | Arguments | Description |
|---------|-----------|-------------|
| `/start` | — | Welcome message + help |
| `/help` | — | List all commands |
| `/mes` | `[YYYY-MM]` | All receipts for a month (default: current month of current year) |
| `/restaurante` | `<RUC>` | Check if a restaurant has receipts on file |
| `/recibo` | `<id>` | Full detail + photo of a single receipt |

---

## `/mes` — Monthly Receipt List

Returns a paginated list of receipts for the given month, grouped and formatted for readability.

```python
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime
from storage.local_store import get_receipts_by_month

async def cmd_mes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    now = datetime.now()
    try:
        if not args:
            # Default: current month of current year
            month = now.strftime("%Y-%m")
        elif re.fullmatch(r"\d{4}-\d{2}", args[0]):
            month = args[0]
            year, m = month.split("-")
            datetime(int(year), int(m), 1)  # validate date
        elif re.fullmatch(r"\d{2}", args[0]):
            # Allow shorthand: /mes 03 → current year + month 03
            month = f"{now.year}-{args[0]}"
            datetime(now.year, int(args[0]), 1)  # validate month number
        else:
            raise ValueError("bad format")
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Formato inválido.\n"
            "Usa: /mes YYYY-MM  →  /mes 2024-03\n"
            "  o: /mes MM       →  /mes 03  (usa el año actual)"
        )
        return

    receipts = get_receipts_by_month(month)
    if not receipts:
        await update.message.reply_text(f"📭 No hay recibos registrados para {month}.")
        return

    lines = [f"🗓 *Recibos de {month}* ({len(receipts)} encontrados)\n"]
    for r in receipts:
        data = r.get("extraction", {}).get("data") or {}
        status_icon = "✅" if r["status"] == "processed" else "⏳" if r["status"] == "pending" else "❌"
        dni_icon = "🪪✓" if data.get("dni_valid") else "🪪✗" if data.get("dni_valid") is False else "🪪?"
        lines.append(
            f"{status_icon} `{r['id'][:8]}`  {dni_icon}\n"
            f"   🏪 {data.get('restaurant_name', 'N/A')}\n"
            f"   💰 S/ {data.get('total_amount', '?')}  IGV: S/ {data.get('igv_amount', '?')}\n"
            f"   📅 {r['created_at'][:10]}\n"
        )

    # Telegram message limit: 4096 chars — chunk if needed
    message = "\n".join(lines)
    for chunk in _chunk_message(message):
        await update.message.reply_text(chunk, parse_mode="Markdown")
```

---

## `/restaurante` — Validate Restaurant Receipts

```python
import re

async def cmd_restaurante(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not re.fullmatch(r"\d{11}", context.args[0]):
        await update.message.reply_text("❌ Proporciona un RUC válido (11 dígitos).\nEjemplo: /restaurante 20123456789")
        return

    ruc = context.args[0]
    receipts = get_receipts_by_ruc(ruc)  # storage function: filter by extraction.data.ruc

    if not receipts:
        await update.message.reply_text(f"🔍 RUC `{ruc}` — Sin recibos registrados.", parse_mode="Markdown")
        return

    name = receipts[0].get("extraction", {}).get("data", {}).get("restaurant_name", "Desconocido")
    lines = [f"🏪 *{name}*\n🔢 RUC: `{ruc}`\n📄 Recibos encontrados: {len(receipts)}\n"]
    for r in receipts[-5:]:  # show last 5
        data = r.get("extraction", {}).get("data") or {}
        lines.append(f"• `{r['id'][:8]}` — S/ {data.get('total_amount', '?')} — {r['created_at'][:10]}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
```

---

## `/recibo` — Single Receipt Detail + Photo

```python
async def cmd_recibo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("❌ Proporciona el ID del recibo.\nEjemplo: /recibo abc12345")
        return

    receipt_id = context.args[0]
    receipt = get_receipt_by_id(receipt_id)  # partial match on first 8 chars is OK

    if not receipt:
        await update.message.reply_text(f"❌ Recibo `{receipt_id}` no encontrado.", parse_mode="Markdown")
        return

    data = receipt.get("extraction", {}).get("data") or {}
    dni_status = "✅ Válido" if data.get("dni_valid") else "❌ Inválido / No encontrado"

    caption = (
        f"🧾 *Recibo* `{receipt['id'][:8]}`\n\n"
        f"🏪 Restaurante: {data.get('restaurant_name', 'N/A')}\n"
        f"🔢 RUC: `{data.get('ruc', 'N/A')}`\n"
        f"💰 Total: S/ {data.get('total_amount', 'N/A')}\n"
        f"🧾 IGV: S/ {data.get('igv_amount', 'N/A')}\n"
        f"🪪 DNI: `{data.get('dni', 'N/A')}` — {dni_status}\n"
        f"📅 Fecha: {receipt['created_at'][:10]}\n"
        f"🔄 Estado: {receipt['status']}"
    )

    photo_path = receipt.get("photo", {}).get("local_path")
    if photo_path:
        with open(photo_path, "rb") as photo_file:
            await update.message.reply_photo(photo=photo_file, caption=caption, parse_mode="Markdown")
    else:
        await update.message.reply_text(caption, parse_mode="Markdown")
```

---

## Utility: Message Chunker

Telegram has a 4096-character limit per message.

```python
def _chunk_message(text: str, limit: int = 4000) -> list[str]:
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
    return chunks
```

---

## Handler Registration

```python
from telegram.ext import CommandHandler

application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("help", cmd_help))
application.add_handler(CommandHandler("mes", cmd_mes))
application.add_handler(CommandHandler("restaurante", cmd_restaurante))
application.add_handler(CommandHandler("recibo", cmd_recibo))
```

## Storage Interface (required methods)

These functions must be implemented in `storage/local_store.py`:

```python
def get_receipts_by_month(month: str) -> list[dict]: ...      # month = "YYYY-MM"
def get_receipts_by_ruc(ruc: str) -> list[dict]: ...
def get_receipt_by_id(receipt_id: str) -> dict | None: ...    # match on id prefix
```

Local implementation: scan `./data/receipts/` directory, load JSON files, filter in memory.

## Formatting Rules

- Always use `parse_mode="Markdown"` for rich output.
- Wrap IDs and codes in backticks: `` `20123456789` ``.
- Use S/ prefix for Peruvian Sol amounts.
- Keep replies concise — max 5 items in lists; add "Ver más con /mes YYYY-MM" when truncated.
- Never expose internal file paths or stack traces to the user.
