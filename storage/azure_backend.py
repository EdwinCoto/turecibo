"""Azure Blob Storage backend — no local filesystem dependencies."""
import json
import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from models.receipt import Receipt
from storage.utils import build_receipt_fingerprint

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover
    BlobServiceClient = None

logger = logging.getLogger(__name__)

_blob_service_client_cache = None
_container_client_cache = None


# ──────────────────────────────────────────
# URI helpers
# ──────────────────────────────────────────

def _to_azure_uri(container: str, blob_name: str) -> str:
    return f"azure://{container}/{blob_name}"


def parse_azure_uri(uri: str) -> tuple[str, str] | None:
    """Return (container, blob_name) for an azure:// URI, or None."""
    normalized = uri
    if normalized.startswith("azure:/") and not normalized.startswith("azure://"):
        normalized = normalized.replace("azure:/", "azure://", 1)
    if not normalized.startswith("azure://"):
        return None
    payload = normalized[len("azure://"):]
    parts = payload.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _parse_blob_url(url: str) -> tuple[str, str] | None:
    """Return (container, blob_name) for an https://account.blob.core.windows.net/... URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    path = parsed.path.lstrip("/")
    if not path:
        return None
    parts = path.split("/", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else None


# ──────────────────────────────────────────
# Client helpers
# ──────────────────────────────────────────

def _azure_container_name() -> str:
    return os.environ.get("AZURE_STORAGE_CONTAINER", "turecibo-receipts")


def _receipts_prefix() -> str:
    return os.environ.get("AZURE_RECEIPTS_PREFIX", "receipts").strip("/")


def _photos_prefix() -> str:
    return os.environ.get("AZURE_PHOTOS_PREFIX", "photos").strip("/")


def _get_blob_service_client():
    global _blob_service_client_cache
    if _blob_service_client_cache is None:
        if BlobServiceClient is None:
            raise RuntimeError("azure-storage-blob is required when STORAGE_BACKEND=azure")
        conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        if not conn:
            raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is required when STORAGE_BACKEND=azure")
        _blob_service_client_cache = BlobServiceClient.from_connection_string(conn)
    return _blob_service_client_cache


def _get_container_client():
    global _container_client_cache
    if _container_client_cache is None:
        client = _get_blob_service_client().get_container_client(_azure_container_name())
        if not client.exists():
            client.create_container()
        _container_client_cache = client
    return _container_client_cache


# ──────────────────────────────────────────
# Blob name helpers
# ──────────────────────────────────────────

def _receipt_blob_name(date_str: str, receipt_id: str) -> str:
    return f"{_receipts_prefix()}/{date_str}/{receipt_id}.json"


def _photo_blob_name(date_str: str, receipt_id: str, extension: str) -> str:
    return f"{_photos_prefix()}/{date_str}/{receipt_id}.{extension}"


def _find_existing_receipt_blob_name(receipt_id: str) -> str | None:
    suffix = f"/{receipt_id}.json"
    for blob in _get_container_client().list_blobs(name_starts_with=f"{_receipts_prefix()}/"):
        if blob.name.endswith(suffix):
            return blob.name
    return None


def _iter_receipts(name_prefix: str | None = None) -> list[dict]:
    results: list[dict] = []
    prefix = name_prefix or f"{_receipts_prefix()}/"
    container = _get_container_client()
    for blob in container.list_blobs(name_starts_with=prefix):
        if not blob.name.endswith(".json"):
            continue
        try:
            raw = container.download_blob(blob.name).readall().decode("utf-8")
            results.append(json.loads(raw))
        except Exception:
            logger.exception("Failed to load receipt blob: %s", blob.name)
    return results


def _receipt_date_str(receipt: Receipt) -> str:
    if receipt.receipt_date is not None:
        return receipt.receipt_date.strftime("%Y-%m-%d")
    if receipt.extraction and receipt.extraction.data and receipt.extraction.data.emission_date is not None:
        return receipt.extraction.data.emission_date.strftime("%Y-%m-%d")
    return receipt.created_at.date().strftime("%Y-%m-%d")


def _move_or_upload_photo(target_date: str, receipt_id: str, photo_path: str) -> str:
    parsed = parse_azure_uri(photo_path)
    if parsed is not None:
        src_container, src_blob = parsed
        extension = Path(src_blob).suffix.lstrip(".") or "jpg"
        target_blob = _photo_blob_name(target_date, receipt_id, extension)
        target_uri = _to_azure_uri(_azure_container_name(), target_blob)
        if src_container == _azure_container_name() and src_blob == target_blob:
            return target_uri
        source = _get_blob_service_client().get_blob_client(src_container, src_blob)
        data = source.download_blob().readall()
        _get_container_client().upload_blob(name=target_blob, data=data, overwrite=True)
        try:
            source.delete_blob()
        except Exception:
            logger.exception("Failed to delete old photo blob: %s", src_blob)
        return target_uri

    file_path = Path(photo_path)
    if file_path.exists():
        extension = file_path.suffix.lstrip(".") or "jpg"
        target_blob = _photo_blob_name(target_date, receipt_id, extension)
        _get_container_client().upload_blob(name=target_blob, data=file_path.read_bytes(), overwrite=True)
        return _to_azure_uri(_azure_container_name(), target_blob)

    return photo_path


# ──────────────────────────────────────────
# Public API
# ──────────────────────────────────────────

def save_receipt(receipt: Receipt) -> Path:
    date_str = _receipt_date_str(receipt)
    blob_name = _receipt_blob_name(date_str, receipt.id)
    container = _get_container_client()

    existing = _find_existing_receipt_blob_name(receipt.id)
    if existing and existing != blob_name:
        try:
            container.delete_blob(existing)
        except Exception:
            logger.exception("Failed to delete old receipt blob: %s", existing)

    if receipt.photo and receipt.photo.local_path:
        receipt.photo.local_path = _move_or_upload_photo(date_str, receipt.id, receipt.photo.local_path)

    payload = json.dumps(receipt.to_json_dict(), indent=2, ensure_ascii=False).encode("utf-8")
    container.upload_blob(name=blob_name, data=payload, overwrite=True)
    logger.info("Receipt saved to blob: %s", blob_name)
    return Path(f"/{_azure_container_name()}/{blob_name}")


def get_receipts_by_month(month: str) -> list[dict]:
    return _iter_receipts(name_prefix=f"{_receipts_prefix()}/{month}")


def get_receipts_by_year(year: str) -> list[dict]:
    return _iter_receipts(name_prefix=f"{_receipts_prefix()}/{year}-")


def get_receipts_by_ruc(ruc: str) -> list[dict]:
    results = []
    for receipt in _iter_receipts():
        data = (receipt.get("extraction") or {}).get("data") or {}
        if data.get("ruc") == ruc:
            results.append(receipt)
    return results


def get_receipt_by_id(receipt_id: str) -> Optional[dict]:
    for receipt in _iter_receipts():
        rid = receipt.get("id", "")
        if rid == receipt_id or rid.startswith(receipt_id):
            return receipt
    return None


def get_receipt_by_telegram_file_id(telegram_file_id: str) -> Optional[dict]:
    for receipt in _iter_receipts():
        if receipt.get("source", {}).get("telegram_file_id") == telegram_file_id:
            return receipt
    return None


def get_receipt_by_telegram_photo_identity(
    telegram_file_unique_id: str | None,
    telegram_file_id: str | None = None,
) -> Optional[dict]:
    for receipt in _iter_receipts():
        source = receipt.get("source", {})
        if telegram_file_unique_id and source.get("telegram_file_unique_id") == telegram_file_unique_id:
            return receipt
        if telegram_file_id and source.get("telegram_file_id") == telegram_file_id:
            return receipt
    return None


def get_receipt_by_photo_hash(photo_hash: str) -> Optional[dict]:
    for receipt in _iter_receipts():
        if receipt.get("photo", {}).get("content_hash") == photo_hash:
            return receipt
    return None


def get_receipt_by_fingerprint(fingerprint: str) -> Optional[dict]:
    for receipt in _iter_receipts():
        stored = receipt.get("receipt_fingerprint")
        recomputed = build_receipt_fingerprint(receipt)
        if fingerprint in {stored, recomputed}:
            return receipt
    return None


def save_photo(receipt_id: str, date_str: str, photo_bytes: bytes, extension: str = "jpg") -> Path:
    blob_name = _photo_blob_name(date_str, receipt_id, extension)
    _get_container_client().upload_blob(name=blob_name, data=photo_bytes, overwrite=True)
    uri = _to_azure_uri(_azure_container_name(), blob_name)
    logger.info("Photo saved to blob: %s", blob_name)
    return Path(uri)


def get_photo_bytes(photo_path: str) -> bytes | None:
    """Read bytes from an azure:// URI. Returns None if not an Azure URI."""
    parsed = parse_azure_uri(photo_path)
    if parsed is None:
        return None
    container_name, blob_name = parsed
    return _get_blob_service_client().get_blob_client(container_name, blob_name).download_blob().readall()


def delete_receipt_by_id(receipt_id: str) -> bool:
    receipt = get_receipt_by_id(receipt_id)
    if receipt is None:
        return False
    full_receipt_id = receipt.get("id")
    if not full_receipt_id:
        return False

    photo_path = (receipt.get("photo") or {}).get("local_path")
    if photo_path:
        _delete_photo_ref(photo_path)

    receipt_blob = _find_existing_receipt_blob_name(full_receipt_id)
    if receipt_blob:
        _get_container_client().delete_blob(receipt_blob)
    logger.info("Receipt deleted from Azure: %s", full_receipt_id)
    return True


def _delete_photo_ref(photo_path: str) -> None:
    parsed_azure = parse_azure_uri(photo_path)
    if parsed_azure is not None:
        container_name, blob_name = parsed_azure
        _get_blob_service_client().get_blob_client(container_name, blob_name).delete_blob(delete_snapshots="include")
        return
    parsed_url = _parse_blob_url(photo_path)
    if parsed_url is not None:
        container_name, blob_name = parsed_url
        _get_blob_service_client().get_blob_client(container_name, blob_name).delete_blob(delete_snapshots="include")
