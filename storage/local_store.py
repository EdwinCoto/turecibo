import json
import logging
import os
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.receipt import Receipt

logger = logging.getLogger(__name__)


def _default_storage_path() -> str:
    # Azure Functions zip deployments run from a read-only wwwroot; use /tmp unless overridden.
    if os.environ.get("WEBSITE_INSTANCE_ID"):
        return "/tmp/turecibo/receipts"
    return "./data/receipts"


BASE_PATH = Path(os.environ.get("LOCAL_STORAGE_PATH", _default_storage_path()))


def _receipt_dir(date_str: str) -> Path:
    """Return and create the directory for a given date string (YYYY-MM-DD)."""
    logger.info("_receipt_dir: date_str=%s", date_str)
    day_dir = BASE_PATH / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir


def _receipt_date_str(receipt: Receipt) -> str:
    date_str = (receipt.receipt_date or receipt.created_at.date()).strftime("%Y-%m-%d")
    logger.info("_receipt_date_str: receipt_id=%s date=%s", receipt.id, date_str)
    return date_str


def _find_existing_receipt_file(receipt_id: str) -> Optional[Path]:
    logger.info("_find_existing_receipt_file: receipt_id=%s", receipt_id)
    if not BASE_PATH.exists():
        return None
    for day_dir in BASE_PATH.iterdir():
        if not day_dir.is_dir():
            continue
        candidate = day_dir / f"{receipt_id}.json"
        if candidate.exists():
            return candidate
    return None


def _normalize_amount(value: object) -> str | None:
    logger.info("_normalize_amount: value_type=%s", type(value).__name__)
    if value is None:
        return None
    try:
        text_value = str(value).strip()
        return f"{round(float(text_value), 2):.2f}"
    except (TypeError, ValueError):
        return None


def build_receipt_fingerprint(receipt: dict) -> str:
    logger.info("build_receipt_fingerprint: start")
    extraction = receipt.get("extraction", {}) or {}
    data = extraction.get("data", {}) or {}
    receipt_date = receipt.get("receipt_date") or data.get("emission_date") or receipt.get("created_at", "")[:10]

    canonical = {
        "receipt_date": receipt_date,
        "restaurant_name": (data.get("restaurant_name") or "").strip().lower(),
        "ruc": (data.get("ruc") or "").strip(),
        "total_amount": _normalize_amount(data.get("total_amount")),
        "igv_amount": _normalize_amount(data.get("igv_amount")),
        "currency": (data.get("currency") or "").strip().upper(),
        "dni": (data.get("dni") or "").strip(),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    logger.info("build_receipt_fingerprint: generated=%s", fingerprint[:12])
    return fingerprint


def save_receipt(receipt: Receipt) -> Path:
    """Persist a Receipt as a JSON file. Returns the path written."""
    logger.info("save_receipt: start receipt_id=%s", receipt.id)
    date_str = _receipt_date_str(receipt)
    directory = _receipt_dir(date_str)
    file_path = directory / f"{receipt.id}.json"

    existing_json = _find_existing_receipt_file(receipt.id)
    if existing_json and existing_json != file_path:
        existing_json.replace(file_path)

    if receipt.photo and receipt.photo.local_path:
        current_photo_path = Path(receipt.photo.local_path)
        if current_photo_path.exists() and current_photo_path.parent != directory:
            new_photo_path = directory / current_photo_path.name
            current_photo_path.replace(new_photo_path)
            receipt.photo.local_path = str(new_photo_path)

    file_path.write_text(json.dumps(receipt.to_json_dict(), indent=2, ensure_ascii=False))
    logger.info("Receipt saved: %s", file_path)
    return file_path


def load_receipt(file_path: Path) -> dict:
    logger.info("load_receipt: path=%s", file_path)
    return json.loads(file_path.read_text())


def get_receipts_by_month(month: str) -> list[dict]:
    """Return all receipts for a given month (YYYY-MM), sorted by created_at asc."""
    logger.info("get_receipts_by_month: month=%s", month)
    results: list[dict] = []
    if not BASE_PATH.exists():
        return results
    for day_dir in sorted(BASE_PATH.iterdir()):
        if not day_dir.is_dir():
            continue
        # day_dir.name is YYYY-MM-DD; check prefix match
        if not day_dir.name.startswith(month):
            continue
        for json_file in sorted(day_dir.glob("*.json")):
            try:
                results.append(load_receipt(json_file))
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    logger.info("get_receipts_by_month: found=%d", len(results))
    return results


def get_receipts_by_ruc(ruc: str) -> list[dict]:
    """Return all receipts that match a given RUC."""
    logger.info("get_receipts_by_ruc: ruc=%s", ruc)
    results: list[dict] = []
    if not BASE_PATH.exists():
        return results
    for day_dir in sorted(BASE_PATH.iterdir()):
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                if receipt.get("extraction", {}).get("data", {}) and \
                        receipt["extraction"]["data"].get("ruc") == ruc:
                    results.append(receipt)
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    logger.info("get_receipts_by_ruc: found=%d", len(results))
    return results


def get_receipt_by_id(receipt_id: str) -> Optional[dict]:
    """Find a receipt by full ID or 8-char prefix."""
    logger.info("get_receipt_by_id: receipt_id=%s", receipt_id)
    if not BASE_PATH.exists():
        return None
    for day_dir in BASE_PATH.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            stem = json_file.stem
            if stem == receipt_id or stem.startswith(receipt_id):
                try:
                    return load_receipt(json_file)
                except Exception:
                    logger.exception("Failed to load receipt file: %s", json_file)
    logger.info("get_receipt_by_id: not found receipt_id=%s", receipt_id)
    return None


def get_receipt_by_telegram_file_id(telegram_file_id: str) -> Optional[dict]:
    """Find a receipt by Telegram file id."""
    logger.info("get_receipt_by_telegram_file_id: telegram_file_id=%s", telegram_file_id)
    if not BASE_PATH.exists():
        return None

    for day_dir in BASE_PATH.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                if receipt.get("source", {}).get("telegram_file_id") == telegram_file_id:
                    return receipt
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    logger.info("get_receipt_by_telegram_file_id: not found")
    return None


def get_receipt_by_telegram_photo_identity(
    telegram_file_unique_id: str | None,
    telegram_file_id: str | None = None,
) -> Optional[dict]:
    """Find a receipt by Telegram photo identity, preferring the stable unique id."""
    logger.info(
        "get_receipt_by_telegram_photo_identity: unique_id=%s file_id=%s",
        telegram_file_unique_id,
        telegram_file_id,
    )
    if not BASE_PATH.exists():
        return None

    for day_dir in BASE_PATH.iterdir():
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
    logger.info("get_receipt_by_telegram_photo_identity: not found")
    return None


def get_receipt_by_photo_hash(photo_hash: str) -> Optional[dict]:
    """Find a receipt by the downloaded photo content hash."""
    logger.info("get_receipt_by_photo_hash: photo_hash=%s", photo_hash[:12])
    if not BASE_PATH.exists():
        return None

    for day_dir in BASE_PATH.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                if receipt.get("photo", {}).get("content_hash") == photo_hash:
                    return receipt
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    logger.info("get_receipt_by_photo_hash: not found")
    return None


def get_receipt_by_fingerprint(fingerprint: str) -> Optional[dict]:
    """Find a receipt by normalized receipt content fingerprint."""
    logger.info("get_receipt_by_fingerprint: fingerprint=%s", fingerprint[:12])
    if not BASE_PATH.exists():
        return None

    for day_dir in BASE_PATH.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                existing_fingerprint = receipt.get("receipt_fingerprint") or build_receipt_fingerprint(receipt)
                if existing_fingerprint == fingerprint:
                    return receipt
            except Exception:
                logger.exception("Failed to load receipt file: %s", json_file)
    logger.info("get_receipt_by_fingerprint: not found")
    return None


def save_photo(receipt_id: str, date_str: str, photo_bytes: bytes, extension: str = "jpg") -> Path:
    """Save raw photo bytes and return the local path."""
    logger.info(
        "save_photo: start receipt_id=%s date=%s extension=%s bytes=%d",
        receipt_id,
        date_str,
        extension,
        len(photo_bytes),
    )
    directory = _receipt_dir(date_str)
    photo_path = directory / f"{receipt_id}.{extension}"
    photo_path.write_bytes(photo_bytes)
    logger.info("Photo saved: %s (%d bytes)", photo_path, len(photo_bytes))
    return photo_path
