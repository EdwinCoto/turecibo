import logging
import re

logger = logging.getLogger(__name__)

_RUC_PATTERN = re.compile(r"^\d{11}$")


def is_valid_format(ruc: str) -> bool:
    """Return True if RUC is exactly 11 numeric digits."""
    result = bool(_RUC_PATTERN.match(ruc or ""))
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
