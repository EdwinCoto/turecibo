"""Local filesystem storage backend — no Azure dependencies."""
import json
import logging
import os
from pathlib import Path
from typing import Optional

from models.receipt import Receipt
from storage.utils import build_receipt_fingerprint

logger = logging.getLogger(__name__)

# Overrideable in tests via patch.object(local_backend, "BASE_PATH", tmp_path).
BASE_PATH: Optional[Path] = None
_base_path_cache: Optional[Path] = None


def _default_storage_path() -> str:
    if os.environ.get("IS_AZURE_FUNCTIONS_ENVIRONMENT") == "true":
        return "/tmp/turecibo/receipts"
    return "./data/receipts"


def get_base_path() -> Path:
    if BASE_PATH is not None:
        return BASE_PATH
    global _base_path_cache
    if _base_path_cache is None:
        _base_path_cache = Path(
            os.environ.get("LOCAL_STORAGE_PATH", _default_storage_path())
        )
        logger.info("Local storage path resolved: %s", _base_path_cache)
    return _base_path_cache


def _receipt_dir(date_str: str) -> Path:
    day_dir = get_base_path() / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir


def _receipt_date_str(receipt: Receipt) -> str:
    return (receipt.receipt_date or receipt.created_at.date()).strftime("%Y-%m-%d")


def _find_existing_receipt_file(receipt_id: str) -> Optional[Path]:
    base = get_base_path()
    if not base.exists():
        return None
    for day_dir in base.iterdir():
        if not day_dir.is_dir():
            continue
        candidate = day_dir / f"{receipt_id}.json"
        if candidate.exists():
            return candidate
    return None


# ──────────────────────────────────────────
# Public API
# ──────────────────────────────────────────

def save_receipt(receipt: Receipt) -> Path:
    date_str = _receipt_date_str(receipt)
    directory = _receipt_dir(date_str)
    file_path = directory / f"{receipt.id}.json"

    existing_json = _find_existing_receipt_file(receipt.id)
    if existing_json and existing_json != file_path:
        existing_json.replace(file_path)

    if receipt.photo and receipt.photo.local_path:
        current = Path(receipt.photo.local_path)
        if current.exists() and current.parent != directory:
            new_path = directory / current.name
            current.replace(new_path)
            receipt.photo.local_path = str(new_path)

    file_path.write_text(json.dumps(receipt.to_json_dict(), indent=2, ensure_ascii=False))
    logger.info("Receipt saved: %s", file_path)
    return file_path


def load_receipt(file_path: Path) -> dict:
    return json.loads(file_path.read_text())


def get_receipts_by_month(month: str) -> list[dict]:
    results: list[dict] = []
    base = get_base_path()
    if not base.exists():
        return results
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        if not day_dir.name.startswith(month):
            continue
        for json_file in sorted(day_dir.glob("*.json")):
            try:
                results.append(load_receipt(json_file))
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    return results


def get_receipts_by_ruc(ruc: str) -> list[dict]:
    results: list[dict] = []
    base = get_base_path()
    if not base.exists():
        return results
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                data = (receipt.get("extraction") or {}).get("data") or {}
                if data.get("ruc") == ruc:
                    results.append(receipt)
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    return results


def get_receipt_by_id(receipt_id: str) -> Optional[dict]:
    base = get_base_path()
    if not base.exists():
        return None
    for day_dir in base.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            stem = json_file.stem
            if stem == receipt_id or stem.startswith(receipt_id):
                try:
                    return load_receipt(json_file)
                except Exception:
                    logger.exception("Failed to load receipt file: %s", json_file)
    return None


def get_receipt_by_telegram_file_id(telegram_file_id: str) -> Optional[dict]:
    base = get_base_path()
    if not base.exists():
        return None
    for day_dir in base.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                if receipt.get("source", {}).get("telegram_file_id") == telegram_file_id:
                    return receipt
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    return None


def get_receipt_by_telegram_photo_identity(
    telegram_file_unique_id: str | None,
    telegram_file_id: str | None = None,
) -> Optional[dict]:
    base = get_base_path()
    if not base.exists():
        return None
    for day_dir in base.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                source = receipt.get("source", {})
                if telegram_file_unique_id and source.get("telegram_file_unique_id") == telegram_file_unique_id:
                    return receipt
                if telegram_file_id and source.get("telegram_file_id") == telegram_file_id:
                    return receipt
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    return None


def get_receipt_by_photo_hash(photo_hash: str) -> Optional[dict]:
    base = get_base_path()
    if not base.exists():
        return None
    for day_dir in base.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                if receipt.get("photo", {}).get("content_hash") == photo_hash:
                    return receipt
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    return None


def get_receipt_by_fingerprint(fingerprint: str) -> Optional[dict]:
    base = get_base_path()
    if not base.exists():
        return None
    for day_dir in base.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                stored = receipt.get("receipt_fingerprint")
                recomputed = build_receipt_fingerprint(receipt)
                if fingerprint in {stored, recomputed}:
                    return receipt
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    return None


def save_photo(receipt_id: str, date_str: str, photo_bytes: bytes, extension: str = "jpg") -> Path:
    directory = _receipt_dir(date_str)
    photo_path = directory / f"{receipt_id}.{extension}"
    photo_path.write_bytes(photo_bytes)
    logger.info("Photo saved: %s (%d bytes)", photo_path, len(photo_bytes))
    return photo_path


def get_photo_bytes(photo_path: str) -> bytes | None:
    path = Path(photo_path)
    if not path.exists():
        return None
    return path.read_bytes()


def delete_receipt_by_id(receipt_id: str) -> bool:
    receipt = get_receipt_by_id(receipt_id)
    if receipt is None:
        return False
    full_receipt_id = receipt.get("id")
    if not full_receipt_id:
        return False

    photo_path = (receipt.get("photo") or {}).get("local_path")
    if photo_path:
        photo_file = Path(photo_path)
        if photo_file.exists():
            photo_file.unlink()

    receipt_file = _find_existing_receipt_file(full_receipt_id)
    if receipt_file and receipt_file.exists():
        receipt_file.unlink()
    logger.info("Receipt deleted from local: %s", full_receipt_id)
    return True
