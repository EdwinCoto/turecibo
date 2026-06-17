import logging
import re

logger = logging.getLogger(__name__)

_ELECTRONIC_RECEIPT_PATTERN = re.compile(r"^B[A-Z0-9]{3}-\d{1,8}$")
_ELECTRONIC_RECEIPT_VALUE_PATTERN = re.compile(r"\b(B[A-Z0-9]{3})\s*-\s*(\d{1,8})\b", re.IGNORECASE)


def normalize_electronic_receipt_number(value: object) -> str | None:
    """Extract and normalize an electronic receipt number like B130-00274475."""
    if value is None:
        return None

    text_value = str(value).strip().upper()
    match = _ELECTRONIC_RECEIPT_VALUE_PATTERN.search(text_value)
    if not match:
        logger.info("🔴 normalize_electronic_receipt_number: no match value=%s", text_value)
        return None

    series, correlative = match.groups()
    return f"{series}-{correlative}"


def is_valid_format(receipt_number: str) -> bool:
    """Validate format and business rules for electronic receipt number."""
    normalized = normalize_electronic_receipt_number(receipt_number)
    if not normalized:
        logger.info("🔴 is_valid_format: valid=False reason=normalize value=%s", receipt_number)
        return False

    if not _ELECTRONIC_RECEIPT_PATTERN.match(normalized):
        logger.info("🔴 is_valid_format: valid=False reason=pattern value=%s", normalized)
        return False

    _, correlative = normalized.split("-", 1)
    if int(correlative) <= 0:
        logger.info("🔴 is_valid_format: valid=False reason=correlative=0 value=%s", normalized)
        return False

    logger.info("✅ is_valid_format: valid=True value=%s", normalized)
    return True


async def validate_electronic_receipt_number(receipt_number: str) -> bool:
    """Async wrapper for electronic receipt number validation."""
    return is_valid_format(receipt_number)
