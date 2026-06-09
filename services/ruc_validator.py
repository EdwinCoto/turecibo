import logging
import re

logger = logging.getLogger(__name__)

_RUC_PATTERN = re.compile(r"^\d{11}$")
_RUC_VALUE_PATTERN = re.compile(r"\b(\d{11})\b")
_VALID_PREFIXES = ("10", "15", "16", "17", "20")


def normalize_ruc_value(value: object) -> str | None:
    """Extract a normalized 11-digit RUC from mixed values."""
    if value is None:
        return None

    if isinstance(value, int):
        text = str(value)
        return text if len(text) == 11 else None

    text_value = str(value).strip()
    if _RUC_PATTERN.match(text_value):
        return text_value

    match = _RUC_VALUE_PATTERN.search(text_value)
    return match.group(1) if match else None


def _is_valid_check_digit(ruc: str) -> bool:
    if not _RUC_PATTERN.match(ruc):
        return False

    factors = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    weighted_sum = sum(int(ruc[i]) * factors[i] for i in range(10))
    check_digit = 11 - (weighted_sum % 11)
    if check_digit == 10:
        check_digit = 0
    elif check_digit == 11:
        check_digit = 1

    return check_digit == int(ruc[-1])


def is_valid_format(ruc: str) -> bool:
    """Return True if RUC is exactly 11 numeric digits."""
    normalized = normalize_ruc_value(ruc)
    if not normalized:
        logger.info("is_valid_format(ruc): ruc=%s valid=False reason=normalize", ruc)
        return False

    if not normalized.startswith(_VALID_PREFIXES):
        logger.info("is_valid_format(ruc): ruc=%s valid=False reason=prefix", normalized)
        return False

    result = _is_valid_check_digit(normalized)
    logger.info("is_valid_format(ruc): ruc=%s valid=%s", ruc, result)
    return result


async def validate_ruc(ruc: str) -> bool:
    """Validate RUC format locally.

    Returns True only when the RUC is exactly 11 numeric digits.
    """
    logger.info("validate_ruc: start ruc=%s", ruc)
    if not is_valid_format(ruc):
        logger.info("RUC format invalid: %s", ruc)
        return False

    logger.info("validate_ruc: valid ruc=%s", ruc)
    return True
