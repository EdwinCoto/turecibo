from __future__ import annotations

"""Google Drive storage backend."""
import io
import json
import logging
import os
from pathlib import Path
from typing import Optional

from models.receipt import Receipt
from storage.utils import build_receipt_fingerprint

try:
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
except ImportError:  # pragma: no cover
    service_account = None
    Credentials = None
    InstalledAppFlow = None
    Request = None
    build = None
    MediaIoBaseDownload = None
    MediaIoBaseUpload = None

logger = logging.getLogger(__name__)

_SCOPES = ("https://www.googleapis.com/auth/drive",)
_folder_mime_type = "application/vnd.google-apps.folder"

_drive_service_cache = None
_receipts_root_cache: Optional[str] = None
_photos_root_cache: Optional[str] = None


def _credentials_file_path() -> str:
    path = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
    if not path:
        raise RuntimeError(
            "GOOGLE_DRIVE_CREDENTIALS_FILE is required when STORAGE_BACKEND=google_drive"
        )
    return path


def _root_folder_id() -> str:
    return os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "root").strip() or "root"


def _receipts_folder_name() -> str:
    return os.environ.get("GOOGLE_DRIVE_RECEIPTS_FOLDER", "receipts").strip() or "receipts"


def _photos_folder_name() -> str:
    return os.environ.get("GOOGLE_DRIVE_PHOTOS_FOLDER", "photos").strip() or "photos"


def _token_file_path() -> str:
    return os.environ.get("GOOGLE_DRIVE_TOKEN_FILE", "google_drive_token.json").strip()


def _is_oauth_credentials_file(path: str) -> bool:
    """Return True if the file is an OAuth client secrets file (not a service account)."""
    try:
        data = json.loads(Path(path).read_text())
        return "installed" in data or "web" in data
    except Exception:
        return False


def _build_drive_service():
    if build is None:
        raise RuntimeError(
            "google-auth and google-api-python-client are required when STORAGE_BACKEND=google_drive"
        )
    creds_file = _credentials_file_path()

    if _is_oauth_credentials_file(creds_file):
        # OAuth flow — uses user's personal Drive quota
        creds = None
        token_file = _token_file_path()
        if Path(token_file).exists():
            creds = Credentials.from_authorized_user_file(token_file, list(_SCOPES))
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(creds_file, list(_SCOPES))
                creds = flow.run_local_server(port=0)
            Path(token_file).write_text(creds.to_json())
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    # Service account flow
    if service_account is None:
        raise RuntimeError(
            "google-auth is required when STORAGE_BACKEND=google_drive"
        )
    credentials = service_account.Credentials.from_service_account_file(
        creds_file,
        scopes=list(_SCOPES),
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _get_drive_service():
    global _drive_service_cache
    if _drive_service_cache is None:
        _drive_service_cache = _build_drive_service()
    return _drive_service_cache


def _list_files(query: str, fields: str) -> list[dict]:
    service = _get_drive_service()
    results: list[dict] = []
    page_token = None
    while True:
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields=f"nextPageToken, files({fields})",
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        results.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return results


def _find_file(parent_id: str, name: str, mime_type: str | None = None) -> Optional[dict]:
    escaped_name = name.replace("'", "\\'")
    query = f"'{parent_id}' in parents and name = '{escaped_name}' and trashed = false"
    if mime_type:
        query += f" and mimeType = '{mime_type}'"
    matches = _list_files(query, "id, name, mimeType, parents")
    return matches[0] if matches else None


def _create_folder(parent_id: str, name: str) -> str:
    metadata = {
        "name": name,
        "mimeType": _folder_mime_type,
        "parents": [parent_id],
    }
    created = (
        _get_drive_service()
        .files()
        .create(body=metadata, fields="id", supportsAllDrives=True)
        .execute()
    )
    return created["id"]


def _ensure_folder(parent_id: str, name: str) -> str:
    existing = _find_file(parent_id, name, mime_type=_folder_mime_type)
    if existing:
        return existing["id"]
    return _create_folder(parent_id, name)


def _receipts_root_folder_id() -> str:
    global _receipts_root_cache
    if _receipts_root_cache is None:
        _receipts_root_cache = _ensure_folder(_root_folder_id(), _receipts_folder_name())
    return _receipts_root_cache


def _photos_root_folder_id() -> str:
    global _photos_root_cache
    if _photos_root_cache is None:
        _photos_root_cache = _ensure_folder(_root_folder_id(), _photos_folder_name())
    return _photos_root_cache


def _ensure_day_folder(parent_id: str, day_folder: str) -> str:
    return _ensure_folder(parent_id, day_folder)


def _iter_receipt_files() -> list[dict]:
    folders_to_visit = [_receipts_root_folder_id()]
    receipt_files: list[dict] = []
    while folders_to_visit:
        current_folder = folders_to_visit.pop()
        child_folders = _list_files(
            f"'{current_folder}' in parents and mimeType = '{_folder_mime_type}' and trashed = false",
            "id, name",
        )
        folders_to_visit.extend(item["id"] for item in child_folders)

        child_files = _list_files(
            f"'{current_folder}' in parents and mimeType != '{_folder_mime_type}' and trashed = false",
            "id, name, parents",
        )
        for file_meta in child_files:
            if file_meta.get("name", "").endswith(".json"):
                receipt_files.append(file_meta)
    return receipt_files


def _download_file_bytes(file_id: str) -> bytes:
    if MediaIoBaseDownload is None:
        raise RuntimeError(
            "google-auth and google-api-python-client are required when STORAGE_BACKEND=google_drive"
        )
    request = _get_drive_service().files().get_media(fileId=file_id, supportsAllDrives=True)
    output = io.BytesIO()
    downloader = MediaIoBaseDownload(output, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return output.getvalue()


def _upload_file(parent_id: str, file_name: str, data: bytes, mime_type: str) -> str:
    if MediaIoBaseUpload is None:
        raise RuntimeError(
            "google-auth and google-api-python-client are required when STORAGE_BACKEND=google_drive"
        )
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
    metadata = {"name": file_name, "parents": [parent_id]}
    created = (
        _get_drive_service()
        .files()
        .create(
            body=metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    return created["id"]


def _delete_file(file_id: str) -> None:
    _get_drive_service().files().delete(fileId=file_id, supportsAllDrives=True).execute()


def _load_receipt(file_id: str) -> dict:
    raw = _download_file_bytes(file_id).decode("utf-8")
    return json.loads(raw)


def _receipt_date_str(receipt: Receipt) -> str:
    if receipt.receipt_date is not None:
        return receipt.receipt_date.strftime("%Y-%m-%d")
    if receipt.extraction and receipt.extraction.data and receipt.extraction.data.emission_date is not None:
        return receipt.extraction.data.emission_date.strftime("%Y-%m-%d")
    return receipt.created_at.date().strftime("%Y-%m-%d")


def _build_gdrive_uri(kind: str, date_str: str, file_id: str) -> str:
    return f"gdrive://{kind}/{date_str}/{file_id}"


def parse_gdrive_uri(uri: str) -> tuple[str, str, str] | None:
    normalized = uri
    if normalized.startswith("gdrive:/") and not normalized.startswith("gdrive://"):
        normalized = normalized.replace("gdrive:/", "gdrive://", 1)
    if not normalized.startswith("gdrive://"):
        return None
    payload = normalized[len("gdrive://"):]
    parts = payload.split("/")
    if len(parts) < 3:
        return None
    kind, date_str, file_id = parts[0], parts[1], parts[-1]
    if not kind or not date_str or not file_id:
        return None
    return kind, date_str, file_id


def _find_existing_receipt_file(receipt_id: str) -> Optional[dict]:
    expected_name = f"{receipt_id}.json"
    for file_meta in _iter_receipt_files():
        if file_meta.get("name") == expected_name:
            return file_meta
    return None


def _move_or_upload_photo(target_date: str, receipt_id: str, photo_path: str) -> str:
    parsed = parse_gdrive_uri(photo_path)
    if parsed is not None:
        # Keep already-uploaded Drive photo references as-is.
        # Re-fetching by file ID can fail if ownership/visibility changed
        # (e.g. switching from service account to OAuth user).
        return photo_path

    file_path = Path(photo_path)
    if file_path.exists():
        extension = file_path.suffix.lstrip(".") or "jpg"
        return _save_photo_file(receipt_id, target_date, file_path.read_bytes(), extension)

    return photo_path


def _save_photo_file(receipt_id: str, date_str: str, photo_bytes: bytes, extension: str) -> str:
    photos_root = _photos_root_folder_id()
    day_folder_id = _ensure_day_folder(photos_root, date_str)
    file_name = f"{receipt_id}.{extension}"
    existing = _find_file(day_folder_id, file_name)
    if existing:
        _delete_file(existing["id"])
    file_id = _upload_file(day_folder_id, file_name, photo_bytes, "image/jpeg")
    return _build_gdrive_uri("photos", date_str, file_id)


def _iter_receipts() -> list[dict]:
    results: list[dict] = []
    for file_meta in _iter_receipt_files():
        try:
            results.append(_load_receipt(file_meta["id"]))
        except Exception:
            logger.exception("Failed to load receipt file from Google Drive: %s", file_meta.get("id"))
    return results


def save_receipt(receipt: Receipt) -> Path:
    date_str = _receipt_date_str(receipt)
    day_folder_id = _ensure_day_folder(_receipts_root_folder_id(), date_str)
    file_name = f"{receipt.id}.json"

    existing = _find_existing_receipt_file(receipt.id)
    if existing:
        _delete_file(existing["id"])

    if receipt.photo and receipt.photo.local_path:
        receipt.photo.local_path = _move_or_upload_photo(date_str, receipt.id, receipt.photo.local_path)

    payload = json.dumps(receipt.to_json_dict(), indent=2, ensure_ascii=False).encode("utf-8")
    file_id = _upload_file(day_folder_id, file_name, payload, "application/json")
    logger.info("Receipt saved to Google Drive: %s", file_id)
    return Path(_build_gdrive_uri("receipts", date_str, file_id))


def get_receipts_by_month(month: str) -> list[dict]:
    return [
        receipt
        for receipt in _iter_receipts()
        if ((receipt.get("receipt_date") or receipt.get("created_at", "")[:10])[:7] == month)
    ]


def get_receipts_by_year(year: str) -> list[dict]:
    prefix = f"{year}-"
    return [
        receipt
        for receipt in _iter_receipts()
        if ((receipt.get("receipt_date") or receipt.get("created_at", "")[:10]).startswith(prefix))
    ]


def get_receipts_by_ruc(ruc: str) -> list[dict]:
    results: list[dict] = []
    for receipt in _iter_receipts():
        data = (receipt.get("extraction") or {}).get("data") or {}
        if data.get("ruc") == ruc:
            results.append(receipt)
    return results


def get_receipt_by_id(receipt_id: str) -> Optional[dict]:
    for receipt in _iter_receipts():
        current_id = receipt.get("id", "")
        if current_id == receipt_id or current_id.startswith(receipt_id):
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
    uri = _save_photo_file(receipt_id, date_str, photo_bytes, extension)
    logger.info("Photo saved to Google Drive: %s", uri)
    return Path(uri)


def get_photo_bytes(photo_path: str) -> bytes | None:
    parsed = parse_gdrive_uri(photo_path)
    if parsed is None:
        return None
    _, _, file_id = parsed
    return _download_file_bytes(file_id)


def delete_receipt_by_id(receipt_id: str) -> bool:
    receipt = get_receipt_by_id(receipt_id)
    if receipt is None:
        return False

    full_receipt_id = receipt.get("id")
    if not full_receipt_id:
        return False

    existing_receipt_file = _find_existing_receipt_file(full_receipt_id)
    if existing_receipt_file:
        _delete_file(existing_receipt_file["id"])

    photo_path = (receipt.get("photo") or {}).get("local_path")
    if photo_path:
        parsed = parse_gdrive_uri(photo_path)
        if parsed is not None:
            _, _, photo_file_id = parsed
            _delete_file(photo_file_id)

    logger.info("Receipt deleted from Google Drive: %s", full_receipt_id)
    return True
