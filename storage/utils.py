"""Shared pure utilities used by both storage backends."""
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


def _normalize_amount(value: object) -> str | None:
    if value is None:
        return None
    try:
        return f"{round(float(str(value).strip()), 2):.2f}"
    except (TypeError, ValueError):
        return None


def build_receipt_fingerprint(receipt: dict) -> str:
    logger.info("build_receipt_fingerprint: start")
    extraction = receipt.get("extraction", {}) or {}
    data = extraction.get("data", {}) or {}
    receipt_date = (
        receipt.get("receipt_date")
        or data.get("emission_date")
        or receipt.get("created_at", "")[:10]
    )
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
