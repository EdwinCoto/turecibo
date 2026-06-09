import logging
import re

logger = logging.getLogger(__name__)

_DNI_PATTERN = re.compile(r"^\d{8}$")


def is_valid_format(dni: str) -> bool:
    """Return True if DNI is exactly 8 numeric digits."""
    result = bool(_DNI_PATTERN.match(dni or ""))
    logger.info("is_valid_format: dni=%s valid=%s", dni, result)
    return result


async def validate_dni(dni: str) -> bool:
    """
    Validate DNI format locally.
    Returns True only when the DNI is exactly 8 numeric digits.
    """
    logger.info("validate_dni: start dni=%s", dni)
    if not is_valid_format(dni):
        logger.info("DNI format invalid: %s", dni)
        return False

    logger.info("validate_dni: valid dni=%s", dni)
    return True
