import json
import logging
import os
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from models.receipt import Receipt

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - dependency may be absent in local unit tests
    BlobServiceClient = None

logger = logging.getLogger(__name__)


_blob_service_client_cache = None
_container_client_cache = None


def _default_storage_path() -> str:
    # Azure Functions zip deployments run from a read-only wwwroot; use /tmp unless overridden.
    if os.environ.get("IS_AZURE_FUNCTIONS_ENVIRONMENT") == "true":
        return "/tmp/turecibo/receipts"
    return "./data/receipts"


def _storage_backend() -> str:
    return os.environ.get("STORAGE_BACKEND", "local").strip().lower()


def _is_azure_backend() -> bool:
    return _storage_backend() == "azure"


def _azure_container_name() -> str:
    return os.environ.get("AZURE_STORAGE_CONTAINER", "turecibo-receipts")


def _receipts_prefix() -> str:
    return os.environ.get("AZURE_RECEIPTS_PREFIX", "receipts").strip("/")


def _photos_prefix() -> str:
    return os.environ.get("AZURE_PHOTOS_PREFIX", "photos").strip("/")


def _to_azure_uri(container: str, blob_name: str) -> str:
    return f"azure://{container}/{blob_name}"


def _parse_azure_uri(uri: str) -> tuple[str, str] | None:
    normalized_uri = uri
    # Compat: Path("azure://...") serializes as "azure:/..." on POSIX.
    if normalized_uri.startswith("azure:/") and not normalized_uri.startswith("azure://"):
        normalized_uri = normalized_uri.replace("azure:/", "azure://", 1)

    if not normalized_uri.startswith("azure://"):
        return None
    payload = normalized_uri[len("azure://"):]
    parts = payload.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _parse_blob_url(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    path = parsed.path.lstrip("/")
    if not path:
        return None
    parts = path.split("/", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _get_blob_service_client():
    global _blob_service_client_cache
    if _blob_service_client_cache is None:
        if BlobServiceClient is None:
            raise RuntimeError("azure-storage-blob is required when STORAGE_BACKEND=azure")
        connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        if not connection_string:
            raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is required when STORAGE_BACKEND=azure")
        _blob_service_client_cache = BlobServiceClient.from_connection_string(connection_string)
    return _blob_service_client_cache


def _get_container_client():
    global _container_client_cache
    if _container_client_cache is None:
        container_client = _get_blob_service_client().get_container_client(_azure_container_name())
        if not container_client.exists():
            container_client.create_container()
        _container_client_cache = container_client
    return _container_client_cache


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


def _iter_receipts_azure(name_prefix: str | None = None) -> list[dict]:
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


def _move_or_upload_photo_for_receipt(target_date: str, receipt_id: str, photo_path: str) -> str:
    parsed = _parse_azure_uri(photo_path)
    if parsed is not None:
        src_container_name, src_blob_name = parsed
        extension = Path(src_blob_name).suffix.lstrip(".") or "jpg"
        target_blob_name = _photo_blob_name(target_date, receipt_id, extension)
        target_uri = _to_azure_uri(_azure_container_name(), target_blob_name)
        if src_container_name == _azure_container_name() and src_blob_name == target_blob_name:
            return target_uri

        source_blob = _get_blob_service_client().get_blob_client(src_container_name, src_blob_name)
        data = source_blob.download_blob().readall()
        container = _get_container_client()
        container.upload_blob(name=target_blob_name, data=data, overwrite=True)
        try:
            source_blob.delete_blob()
        except Exception:
            logger.exception("Failed to delete old photo blob: %s", src_blob_name)
        return target_uri

    file_path = Path(photo_path)
    if file_path.exists():
        extension = file_path.suffix.lstrip(".") or "jpg"
        target_blob_name = _photo_blob_name(target_date, receipt_id, extension)
        data = file_path.read_bytes()
        _get_container_client().upload_blob(name=target_blob_name, data=data, overwrite=True)
        return _to_azure_uri(_azure_container_name(), target_blob_name)

    return photo_path


# Backward-compatible test override point + lazy cache.
BASE_PATH: Optional[Path] = None
_base_path_cache: Optional[Path] = None


def _get_base_path() -> Path:
    if BASE_PATH is not None:
        return BASE_PATH

    global _base_path_cache
    if _base_path_cache is None:
        _base_path_cache = Path(os.environ.get("LOCAL_STORAGE_PATH", _default_storage_path()))
        logger.info("Local storage path resolved: %s", _base_path_cache)
    return _base_path_cache

def _receipt_dir(date_str: str) -> Path:
    """Return and create the directory for a given date string (YYYY-MM-DD)."""
    logger.info("_receipt_dir: date_str=%s", date_str)
    day_dir = _get_base_path() / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir


def _receipt_date_str(receipt: Receipt) -> str:
    date_str = (receipt.receipt_date or receipt.created_at.date()).strftime("%Y-%m-%d")
    logger.info("_receipt_date_str: receipt_id=%s date=%s", receipt.id, date_str)
    return date_str


def _find_existing_receipt_file(receipt_id: str) -> Optional[Path]:
    logger.info("_find_existing_receipt_file: receipt_id=%s", receipt_id)
    base = _get_base_path()
    if not base.exists():
        return None
    for day_dir in base.iterdir():
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
        "ruc": (data.get("ruc") or "").strip(),
        "receipt_date": receipt_date,
        "total_amount": _normalize_amount(data.get("total_amount")),
        "dni": (data.get("dni") or "").strip(),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    logger.info("build_receipt_fingerprint: generated=%s", fingerprint[:12])
    return fingerprint


def save_receipt(receipt: Receipt) -> Path:
    """Persist a Receipt as a JSON file. Returns the path written."""
    logger.info("save_receipt: start receipt_id=%s", receipt.id)

    if _is_azure_backend():
        date_str = _receipt_date_str(receipt)
        blob_name = _receipt_blob_name(date_str, receipt.id)
        container = _get_container_client()

        existing_blob = _find_existing_receipt_blob_name(receipt.id)
        if existing_blob and existing_blob != blob_name:
            try:
                container.delete_blob(existing_blob)
            except Exception:
                logger.exception("Failed to delete old receipt blob: %s", existing_blob)

        if receipt.photo and receipt.photo.local_path:
            receipt.photo.local_path = _move_or_upload_photo_for_receipt(
                target_date=date_str,
                receipt_id=receipt.id,
                photo_path=receipt.photo.local_path,
            )

        payload = json.dumps(receipt.to_json_dict(), indent=2, ensure_ascii=False).encode("utf-8")
        container.upload_blob(name=blob_name, data=payload, overwrite=True)
        logger.info("Receipt saved to blob: %s", blob_name)
        return Path(f"/{_azure_container_name()}/{blob_name}")

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

    if _is_azure_backend():
        results = _iter_receipts_azure(name_prefix=f"{_receipts_prefix()}/{month}")
        logger.info("get_receipts_by_month: found=%d", len(results))
        return results

    results: list[dict] = []
    base = _get_base_path()
    if not base.exists():
        return results
    for day_dir in sorted(base.iterdir()):
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

    if _is_azure_backend():
        results: list[dict] = []
        for receipt in _iter_receipts_azure():
            if receipt.get("extraction", {}).get("data", {}) and receipt["extraction"]["data"].get("ruc") == ruc:
                results.append(receipt)
        logger.info("get_receipts_by_ruc: found=%d", len(results))
        return results

    results: list[dict] = []
    base = _get_base_path()
    if not base.exists():
        return results
    for day_dir in sorted(base.iterdir()):
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

    if _is_azure_backend():
        for receipt in _iter_receipts_azure():
            rid = receipt.get("id", "")
            if rid == receipt_id or rid.startswith(receipt_id):
                return receipt
        logger.info("get_receipt_by_id: not found receipt_id=%s", receipt_id)
        return None

    base = _get_base_path()
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
    logger.info("get_receipt_by_id: not found receipt_id=%s", receipt_id)
    return None


def get_receipt_by_telegram_file_id(telegram_file_id: str) -> Optional[dict]:
    """Find a receipt by Telegram file id."""
    logger.info("get_receipt_by_telegram_file_id: telegram_file_id=%s", telegram_file_id)
    if _is_azure_backend():
        for receipt in _iter_receipts_azure():
            if receipt.get("source", {}).get("telegram_file_id") == telegram_file_id:
                return receipt
        logger.info("get_receipt_by_telegram_file_id: not found")
        return None

    base = _get_base_path()
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
    if _is_azure_backend():
        for receipt in _iter_receipts_azure():
            source = receipt.get("source", {})
            if telegram_file_unique_id and source.get("telegram_file_unique_id") == telegram_file_unique_id:
                return receipt
            if telegram_file_id and source.get("telegram_file_id") == telegram_file_id:
                return receipt
        logger.info("get_receipt_by_telegram_photo_identity: not found")
        return None

    base = _get_base_path()
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
    logger.info("get_receipt_by_telegram_photo_identity: not found")
    return None


def get_receipt_by_photo_hash(photo_hash: str) -> Optional[dict]:
    """Find a receipt by the downloaded photo content hash."""
    logger.info("get_receipt_by_photo_hash: photo_hash=%s", photo_hash[:12])
    if _is_azure_backend():
        for receipt in _iter_receipts_azure():
            if receipt.get("photo", {}).get("content_hash") == photo_hash:
                return receipt
        logger.info("get_receipt_by_photo_hash: not found")
        return None

    base = _get_base_path()
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
    logger.info("get_receipt_by_photo_hash: not found")
    return None


def get_receipt_by_fingerprint(fingerprint: str) -> Optional[dict]:
    """Find a receipt by normalized receipt content fingerprint."""
    logger.info("get_receipt_by_fingerprint: fingerprint=%s", fingerprint[:12])
    if _is_azure_backend():
        for receipt in _iter_receipts_azure():
            stored_fingerprint = receipt.get("receipt_fingerprint")
            recomputed_fingerprint = build_receipt_fingerprint(receipt)
            if fingerprint in {stored_fingerprint, recomputed_fingerprint}:
                return receipt
        logger.info("get_receipt_by_fingerprint: not found")
        return None

    base = _get_base_path()
    if not base.exists():
        return None

    for day_dir in base.iterdir():
        if not day_dir.is_dir():
            continue
        for json_file in day_dir.glob("*.json"):
            try:
                receipt = load_receipt(json_file)
                stored_fingerprint = receipt.get("receipt_fingerprint")
                recomputed_fingerprint = build_receipt_fingerprint(receipt)
                if fingerprint in {stored_fingerprint, recomputed_fingerprint}:
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

    if _is_azure_backend():
        blob_name = _photo_blob_name(date_str, receipt_id, extension)
        _get_container_client().upload_blob(name=blob_name, data=photo_bytes, overwrite=True)
        photo_uri = _to_azure_uri(_azure_container_name(), blob_name)
        logger.info("Photo saved to blob: %s", blob_name)
        return Path(photo_uri)

    directory = _receipt_dir(date_str)
    photo_path = directory / f"{receipt_id}.{extension}"
    photo_path.write_bytes(photo_bytes)
    logger.info("Photo saved: %s (%d bytes)", photo_path, len(photo_bytes))
    return photo_path


def get_photo_bytes(photo_path: str) -> bytes | None:
    """Return photo bytes from local path or azure:// URI."""
    logger.info("get_photo_bytes: path=%s", photo_path)
    parsed = _parse_azure_uri(photo_path)
    if parsed is not None:
        container_name, blob_name = parsed
        blob_client = _get_blob_service_client().get_blob_client(container_name, blob_name)
        return blob_client.download_blob().readall()

    path = Path(photo_path)
    if not path.exists():
        return None
    return path.read_bytes()


def _delete_blob_if_exists(container_name: str, blob_name: str) -> None:
    blob_client = _get_blob_service_client().get_blob_client(container_name, blob_name)
    blob_client.delete_blob(delete_snapshots="include")


def _delete_photo_reference(photo_path: str | None) -> None:
    if not photo_path:
        return

    parsed_azure = _parse_azure_uri(photo_path)
    if parsed_azure is not None:
        container_name, blob_name = parsed_azure
        _delete_blob_if_exists(container_name, blob_name)
        return

    parsed_url = _parse_blob_url(photo_path)
    if parsed_url is not None and _is_azure_backend():
        container_name, blob_name = parsed_url
        _delete_blob_if_exists(container_name, blob_name)
        return

    photo_file = Path(photo_path)
    if photo_file.exists():
        photo_file.unlink()


def delete_receipt_by_id(receipt_id: str) -> bool:
    """Delete receipt JSON and linked photo by full ID or 8-char prefix."""
    logger.info("delete_receipt_by_id: receipt_id=%s", receipt_id)
    receipt = get_receipt_by_id(receipt_id)
    if receipt is None:
        logger.info("delete_receipt_by_id: receipt not found")
        return False

    full_receipt_id = receipt.get("id")
    if not full_receipt_id:
        logger.warning("delete_receipt_by_id: receipt has no id")
        return False

    photo_path = (receipt.get("photo") or {}).get("local_path")
    _delete_photo_reference(photo_path)

    if _is_azure_backend():
        receipt_blob_name = _find_existing_receipt_blob_name(full_receipt_id)
        if receipt_blob_name:
            _get_container_client().delete_blob(receipt_blob_name)
        logger.info("delete_receipt_by_id: deleted from azure receipt_id=%s", full_receipt_id)
        return True

    receipt_file = _find_existing_receipt_file(full_receipt_id)
    if receipt_file and receipt_file.exists():
        receipt_file.unlink()
    logger.info("delete_receipt_by_id: deleted from local receipt_id=%s", full_receipt_id)
    return True
