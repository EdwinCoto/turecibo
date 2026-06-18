"""
One-time script to generate Google Drive OAuth user token.

Usage:
    python scripts/generate_google_drive_token.py
"""

import json
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _read_local_settings() -> dict:
    settings_path = Path("local.settings.json")
    if not settings_path.exists():
        raise FileNotFoundError("local.settings.json not found. Create it from template first.")
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    values = payload.get("Values") or {}
    if not isinstance(values, dict):
        raise RuntimeError("Invalid local.settings.json: Values must be an object.")
    return values


def _is_oauth_client_file(path: Path) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    return "installed" in data or "web" in data


def main() -> None:
    values = _read_local_settings()
    credentials_path = Path(values.get("GOOGLE_DRIVE_CREDENTIALS_FILE", "")).expanduser()
    token_path = Path(values.get("GOOGLE_DRIVE_TOKEN_FILE", "google_drive_token.json")).expanduser()
    root_folder_id = values.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "root")

    if not credentials_path.exists():
        raise FileNotFoundError(
            f"OAuth client JSON not found: {credentials_path}\n"
            "Create one in Google Cloud Console and set GOOGLE_DRIVE_CREDENTIALS_FILE."
        )

    if not _is_oauth_client_file(credentials_path):
        raise RuntimeError(
            "GOOGLE_DRIVE_CREDENTIALS_FILE is not an OAuth client JSON.\n"
            "Use OAuth client credentials (Desktop app), not a service account key."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    folder = (
        service.files()
        .get(fileId=root_folder_id, fields="id,name,mimeType", supportsAllDrives=True)
        .execute()
    )

    print("✅ Google Drive OAuth token generated")
    print(f"Token file: {token_path}")
    print(f"Root folder: {folder.get('name')} ({folder.get('id')})")


if __name__ == "__main__":
    main()
