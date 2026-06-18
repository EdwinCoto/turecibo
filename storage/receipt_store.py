"""
Receipt storage facade.

Routes every call to either ``local_backend`` (filesystem) or
``azure_backend`` (Azure Blob) or ``google_drive_backend`` based on the
STORAGE_BACKEND env-var.

All existing imports keep working without changes::

    from storage.receipt_store import save_receipt, get_receipt_by_id, ...

Tests that need to override the filesystem root should patch
``storage.local_backend.BASE_PATH`` directly::

    from storage import local_backend
    with patch.object(local_backend, "BASE_PATH", tmp_path):
        ...
"""
import logging
import os
from pathlib import Path
from typing import Optional

from models.receipt import Receipt
from storage import azure_backend, google_drive_backend, local_backend
from storage.utils import _normalize_amount, build_receipt_fingerprint  # re-export

logger = logging.getLogger(__name__)


def _backend_name() -> str:
    raw = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
    if raw == "azure":
        return "azure"
    if raw in {"google_drive", "google-drive", "gdrive", "google"}:
        return "google_drive"
    return "local"


# ──────────────────────────────────────────
# Public API — delegates to backend modules
# ──────────────────────────────────────────

def save_receipt(receipt: Receipt) -> Path:
    logger.info("save_receipt: start receipt_id=%s", receipt.id)
    backend = _backend_name()
    if backend == "azure":
        return azure_backend.save_receipt(receipt)
    if backend == "google_drive":
        return google_drive_backend.save_receipt(receipt)
    return local_backend.save_receipt(receipt)


def load_receipt(file_path: Path) -> dict:
    return local_backend.load_receipt(file_path)


def get_receipts_by_month(month: str) -> list[dict]:
    logger.info("get_receipts_by_month: month=%s", month)
    backend = _backend_name()
    if backend == "azure":
        results = azure_backend.get_receipts_by_month(month)
    elif backend == "google_drive":
        results = google_drive_backend.get_receipts_by_month(month)
    else:
        results = local_backend.get_receipts_by_month(month)
    logger.info("get_receipts_by_month: found=%d", len(results))
    return results


def get_receipts_by_year(year: str) -> list[dict]:
    logger.info("get_receipts_by_year: year=%s", year)
    backend = _backend_name()
    if backend == "azure":
        results = azure_backend.get_receipts_by_year(year)
    elif backend == "google_drive":
        results = google_drive_backend.get_receipts_by_year(year)
    else:
        results = local_backend.get_receipts_by_year(year)
    logger.info("get_receipts_by_year: found=%d", len(results))
    return results


def get_receipts_by_ruc(ruc: str) -> list[dict]:
    logger.info("get_receipts_by_ruc: ruc=%s", ruc)
    backend = _backend_name()
    if backend == "azure":
        results = azure_backend.get_receipts_by_ruc(ruc)
    elif backend == "google_drive":
        results = google_drive_backend.get_receipts_by_ruc(ruc)
    else:
        results = local_backend.get_receipts_by_ruc(ruc)
    logger.info("get_receipts_by_ruc: found=%d", len(results))
    return results


def get_receipt_by_id(receipt_id: str) -> Optional[dict]:
    logger.info("get_receipt_by_id: receipt_id=%s", receipt_id)
    backend = _backend_name()
    if backend == "azure":
        result = azure_backend.get_receipt_by_id(receipt_id)
    elif backend == "google_drive":
        result = google_drive_backend.get_receipt_by_id(receipt_id)
    else:
        result = local_backend.get_receipt_by_id(receipt_id)
    if result is None:
        logger.info("get_receipt_by_id: not found receipt_id=%s", receipt_id)
    return result


def get_receipt_by_telegram_file_id(telegram_file_id: str) -> Optional[dict]:
    logger.info("get_receipt_by_telegram_file_id: telegram_file_id=%s", telegram_file_id)
    backend = _backend_name()
    if backend == "azure":
        return azure_backend.get_receipt_by_telegram_file_id(telegram_file_id)
    if backend == "google_drive":
        return google_drive_backend.get_receipt_by_telegram_file_id(telegram_file_id)
    return local_backend.get_receipt_by_telegram_file_id(telegram_file_id)


def get_receipt_by_telegram_photo_identity(
    telegram_file_unique_id: str | None,
    telegram_file_id: str | None = None,
) -> Optional[dict]:
    logger.info(
        "get_receipt_by_telegram_photo_identity: unique_id=%s file_id=%s",
        telegram_file_unique_id,
        telegram_file_id,
    )
    backend = _backend_name()
    if backend == "azure":
        return azure_backend.get_receipt_by_telegram_photo_identity(telegram_file_unique_id, telegram_file_id)
    if backend == "google_drive":
        return google_drive_backend.get_receipt_by_telegram_photo_identity(telegram_file_unique_id, telegram_file_id)
    return local_backend.get_receipt_by_telegram_photo_identity(telegram_file_unique_id, telegram_file_id)


def get_receipt_by_photo_hash(photo_hash: str) -> Optional[dict]:
    logger.info("get_receipt_by_photo_hash: photo_hash=%s", photo_hash[:12])
    backend = _backend_name()
    if backend == "azure":
        return azure_backend.get_receipt_by_photo_hash(photo_hash)
    if backend == "google_drive":
        return google_drive_backend.get_receipt_by_photo_hash(photo_hash)
    return local_backend.get_receipt_by_photo_hash(photo_hash)


def get_receipt_by_fingerprint(fingerprint: str) -> Optional[dict]:
    logger.info("get_receipt_by_fingerprint: fingerprint=%s", fingerprint[:12])
    backend = _backend_name()
    if backend == "azure":
        return azure_backend.get_receipt_by_fingerprint(fingerprint)
    if backend == "google_drive":
        return google_drive_backend.get_receipt_by_fingerprint(fingerprint)
    return local_backend.get_receipt_by_fingerprint(fingerprint)


def save_photo(receipt_id: str, date_str: str, photo_bytes: bytes, extension: str = "jpg") -> Path:
    logger.info("save_photo: start receipt_id=%s date=%s extension=%s bytes=%d", receipt_id, date_str, extension, len(photo_bytes))
    backend = _backend_name()
    if backend == "azure":
        return azure_backend.save_photo(receipt_id, date_str, photo_bytes, extension)
    if backend == "google_drive":
        return google_drive_backend.save_photo(receipt_id, date_str, photo_bytes, extension)
    return local_backend.save_photo(receipt_id, date_str, photo_bytes, extension)


def get_photo_bytes(photo_path: str) -> bytes | None:
    """Route by URI scheme: azure:// or gdrive://, else local path."""
    logger.info("get_photo_bytes: path=%s", photo_path)
    normalized = photo_path
    if normalized.startswith("azure:/") and not normalized.startswith("azure://"):
        normalized = normalized.replace("azure:/", "azure://", 1)
    if normalized.startswith("gdrive:/") and not normalized.startswith("gdrive://"):
        normalized = normalized.replace("gdrive:/", "gdrive://", 1)
    if normalized.startswith("azure://"):
        return azure_backend.get_photo_bytes(normalized)
    if normalized.startswith("gdrive://"):
        return google_drive_backend.get_photo_bytes(normalized)
    return local_backend.get_photo_bytes(photo_path)


def delete_receipt_by_id(receipt_id: str) -> bool:
    logger.info("delete_receipt_by_id: receipt_id=%s", receipt_id)
    backend = _backend_name()
    if backend == "azure":
        return azure_backend.delete_receipt_by_id(receipt_id)
    if backend == "google_drive":
        return google_drive_backend.delete_receipt_by_id(receipt_id)
    return local_backend.delete_receipt_by_id(receipt_id)
