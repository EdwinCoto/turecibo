"""
Receipt storage facade.

Routes every call to either ``local_backend`` (filesystem) or
``azure_backend`` (Azure Blob) based on the STORAGE_BACKEND env-var.

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
from storage import azure_backend, local_backend
from storage.utils import _normalize_amount, build_receipt_fingerprint  # re-export

logger = logging.getLogger(__name__)


def _is_azure_backend() -> bool:
    return os.environ.get("STORAGE_BACKEND", "local").strip().lower() == "azure"


# ──────────────────────────────────────────
# Public API — delegates to backend modules
# ──────────────────────────────────────────

def save_receipt(receipt: Receipt) -> Path:
    logger.info("save_receipt: start receipt_id=%s", receipt.id)
    if _is_azure_backend():
        return azure_backend.save_receipt(receipt)
    return local_backend.save_receipt(receipt)


def load_receipt(file_path: Path) -> dict:
    return local_backend.load_receipt(file_path)


def get_receipts_by_month(month: str) -> list[dict]:
    logger.info("get_receipts_by_month: month=%s", month)
    results = azure_backend.get_receipts_by_month(month) if _is_azure_backend() else local_backend.get_receipts_by_month(month)
    logger.info("get_receipts_by_month: found=%d", len(results))
    return results


def get_receipts_by_ruc(ruc: str) -> list[dict]:
    logger.info("get_receipts_by_ruc: ruc=%s", ruc)
    results = azure_backend.get_receipts_by_ruc(ruc) if _is_azure_backend() else local_backend.get_receipts_by_ruc(ruc)
    logger.info("get_receipts_by_ruc: found=%d", len(results))
    return results


def get_receipt_by_id(receipt_id: str) -> Optional[dict]:
    logger.info("get_receipt_by_id: receipt_id=%s", receipt_id)
    result = azure_backend.get_receipt_by_id(receipt_id) if _is_azure_backend() else local_backend.get_receipt_by_id(receipt_id)
    if result is None:
        logger.info("get_receipt_by_id: not found receipt_id=%s", receipt_id)
    return result


def get_receipt_by_telegram_file_id(telegram_file_id: str) -> Optional[dict]:
    logger.info("get_receipt_by_telegram_file_id: telegram_file_id=%s", telegram_file_id)
    if _is_azure_backend():
        return azure_backend.get_receipt_by_telegram_file_id(telegram_file_id)
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
    if _is_azure_backend():
        return azure_backend.get_receipt_by_telegram_photo_identity(telegram_file_unique_id, telegram_file_id)
    return local_backend.get_receipt_by_telegram_photo_identity(telegram_file_unique_id, telegram_file_id)


def get_receipt_by_photo_hash(photo_hash: str) -> Optional[dict]:
    logger.info("get_receipt_by_photo_hash: photo_hash=%s", photo_hash[:12])
    if _is_azure_backend():
        return azure_backend.get_receipt_by_photo_hash(photo_hash)
    return local_backend.get_receipt_by_photo_hash(photo_hash)


def get_receipt_by_fingerprint(fingerprint: str) -> Optional[dict]:
    logger.info("get_receipt_by_fingerprint: fingerprint=%s", fingerprint[:12])
    if _is_azure_backend():
        return azure_backend.get_receipt_by_fingerprint(fingerprint)
    return local_backend.get_receipt_by_fingerprint(fingerprint)


def save_photo(receipt_id: str, date_str: str, photo_bytes: bytes, extension: str = "jpg") -> Path:
    logger.info("save_photo: start receipt_id=%s date=%s extension=%s bytes=%d", receipt_id, date_str, extension, len(photo_bytes))
    if _is_azure_backend():
        return azure_backend.save_photo(receipt_id, date_str, photo_bytes, extension)
    return local_backend.save_photo(receipt_id, date_str, photo_bytes, extension)


def get_photo_bytes(photo_path: str) -> bytes | None:
    """Route by URI scheme: azure:// → azure_backend, local path → local_backend."""
    logger.info("get_photo_bytes: path=%s", photo_path)
    normalized = photo_path
    if normalized.startswith("azure:/") and not normalized.startswith("azure://"):
        normalized = normalized.replace("azure:/", "azure://", 1)
    if normalized.startswith("azure://"):
        return azure_backend.get_photo_bytes(normalized)
    return local_backend.get_photo_bytes(photo_path)


def delete_receipt_by_id(receipt_id: str) -> bool:
    logger.info("delete_receipt_by_id: receipt_id=%s", receipt_id)
    if _is_azure_backend():
        return azure_backend.delete_receipt_by_id(receipt_id)
    return local_backend.delete_receipt_by_id(receipt_id)
