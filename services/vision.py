import base64
import json
import logging
import os
import re
from datetime import date, datetime
from typing import Optional

from openai import AsyncOpenAI

from models.receipt import ExtractionData

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None

_GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"
_GITHUB_MODELS_API_VERSION = "2026-03-10"
_GITHUB_MODELS_DEFAULT_MODEL = "openai/gpt-4.1"
_OPENAI_DEFAULT_MODEL = "gpt-4o"
_DNI_VALUE_PATTERN = re.compile(r"\b(\d{8})\b")
_DATE_PATTERNS = (
    re.compile(r"\b(\d{4})[-/.](\d{2})[-/.](\d{2})\b"),
    re.compile(r"\b(\d{2})[-/.](\d{2})[-/.](\d{4})\b"),
    re.compile(r"\b(\d{2})[-/.](\d{2})[-/.](\d{2})\b"),
)
_DNI_FIELD_ALIASES = (
    "dni",
    "d.n.i",
    "documento",
    "documento_identidad",
    "documento de identidad",
    "nro_documento",
    "numero_documento",
)


def _is_github_models_token(api_key: str) -> bool:
    is_github_token = api_key.startswith(("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_"))
    logger.info("_is_github_models_token: result=%s", is_github_token)
    return is_github_token


def _get_client_config() -> tuple[str | None, dict[str, str], str]:
    logger.info("_get_client_config: start")
    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ.get("OPENAI_BASE_URL")
    headers: dict[str, str] = {}
    model = os.environ.get("OPENAI_MODEL")

    if _is_github_models_token(api_key):
        base_url = base_url or _GITHUB_MODELS_BASE_URL
        model = model or _GITHUB_MODELS_DEFAULT_MODEL
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = os.environ.get(
            "GITHUB_MODELS_API_VERSION", _GITHUB_MODELS_API_VERSION
        )
    else:
        model = model or _OPENAI_DEFAULT_MODEL

    logger.info("_get_client_config: resolved model=%s base_url=%s", model, base_url or "default")
    return base_url, headers, model


def _get_client() -> AsyncOpenAI:
    global _client
    logger.info("_get_client: start")
    if _client is None:
        base_url, headers, _ = _get_client_config()
        api_key = os.environ["OPENAI_API_KEY"]
        if base_url or headers:
            _client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                default_headers=headers or None,
            )
        else:
            _client = AsyncOpenAI(api_key=api_key)
        logger.info("_get_client: client initialized")
    else:
        logger.info("_get_client: reusing cached client")
    return _client


_EXTRACTION_PROMPT = """
Eres un asistente especializado en extraer datos de boletas y facturas de restaurantes peruanos.

Analiza la imagen y extrae los siguientes campos en formato JSON estricto:

{
  "restaurant_name": "<nombre del restaurante o null>",
  "ruc": "<RUC de 11 dígitos o null>",
  "total_amount": <monto total como número decimal o null>,
  "igv_amount": <monto IGV como número decimal o null>,
  "igv_rate": <tasa IGV como decimal, ej: 0.18, o null>,
  "currency": "<moneda, usualmente PEN>",
  "dni": "<DNI de 8 dígitos del cliente o null>"
    "emission_date": "<fecha de emisión del recibo en formato YYYY-MM-DD o null>"
}

Reglas:
- Devuelve SOLO el JSON, sin texto adicional ni markdown.
- Si un campo no está visible en la imagen, usa null.
- total_amount y igv_amount deben ser números con 2 decimales.
- Si el IGV no es visible pero el total sí, calcula igv_amount = round(total * (0.18 / 1.18), 2).
- El RUC debe ser exactamente 11 dígitos numéricos.
- El DNI debe ser exactamente 8 dígitos numéricos.
- La fecha de emisión puede aparecer como "FECHA DE EMISION", "FECHA EMISION", "FECHA" o dentro de un bloque de datos del recibo.
- Si encuentras una fecha del recibo, devuélvela en "emission_date" usando el formato YYYY-MM-DD.
- Si la fecha incluye hora (por ejemplo, "27/05/2026 21:33:00"), ignora la hora y devuelve solo la fecha en formato YYYY-MM-DD.
- Si no encuentras la fecha de emisión, usa null.
- Para extraer el DNI, también considera etiquetas equivalentes como "DOCUMENTO", "D.N.I", "DNI", "DOC.", "DOCUMENTO DE IDENTIDAD" o variantes similares en el recibo.
- Aunque en la imagen aparezca con otra etiqueta, siempre devuelve ese valor en el campo JSON "dni".
"""


def _normalize_dni_value(value: object) -> str | None:
    logger.info("_normalize_dni_value: start value_type=%s", type(value).__name__)
    if value is None:
        return None

    if isinstance(value, int):
        return f"{value:08d}" if 0 <= value <= 99999999 else None

    if isinstance(value, str):
        match = _DNI_VALUE_PATTERN.search(value)
        return match.group(1) if match else None

    return None


def _normalize_emission_date_value(value: object) -> date | None:
    logger.info("_normalize_emission_date_value: start value_type=%s", type(value).__name__)
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, int):
        value = str(value)

    if isinstance(value, str):
        text = value.strip()
        for pattern in _DATE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue

            groups = match.groups()
            try:
                if len(groups[0]) == 4:
                    return date(int(groups[0]), int(groups[1]), int(groups[2]))
                if len(groups[2]) == 4:
                    return date(int(groups[2]), int(groups[1]), int(groups[0]))
                return datetime.strptime(groups[0] + "-" + groups[1] + "-" + groups[2], "%d-%m-%y").date()
            except ValueError:
                continue

    return None


def _normalize_extraction_payload(parsed: dict) -> dict:
    logger.info("_normalize_extraction_payload: start keys=%s", sorted(parsed.keys()))
    normalized = dict(parsed)

    alias_lookup = {
        str(key).strip().lower(): value
        for key, value in normalized.items()
    }

    if normalized.get("dni") is None:
        for field_name in _DNI_FIELD_ALIASES:
            if field_name in alias_lookup:
                normalized["dni"] = alias_lookup[field_name]
                break

    normalized["dni"] = _normalize_dni_value(normalized.get("dni"))

    if normalized.get("emission_date") is None:
        for field_name in ("emission_date", "fecha_emision", "fecha de emision", "fecha emisión", "fecha"):
            if field_name in alias_lookup:
                normalized["emission_date"] = alias_lookup[field_name]
                break

    normalized["emission_date"] = _normalize_emission_date_value(normalized.get("emission_date"))

    logger.info("_normalize_extraction_payload: normalized keys=%s", sorted(normalized.keys()))
    return normalized


async def extract_receipt_data(image_bytes: bytes, receipt_id: str | None = None) -> ExtractionData:
    """
    Send the receipt image to OpenAI vision and parse the structured response.
    Raises on API errors — callers should handle and mark extraction as failed.
    """
    logger.info("extract_receipt_data: start receipt_id=%s image_bytes=%d", receipt_id or "n/a", len(image_bytes))
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    client = _get_client()
    base_url, _, model = _get_client_config()
    logger.info(
        "Starting receipt extraction via Copilot",
        extra={
            "receipt_id": receipt_id,
            "model": model,
            "base_url": base_url or "https://api.openai.com/v1",
            "image_bytes": len(image_bytes),
        },
    )
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _EXTRACTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                    },
                ],
            }
        ],
        max_tokens=500,
        temperature=0,
    )
    logger.info("extract_receipt_data: completion received receipt_id=%s", receipt_id or "n/a")

    raw = response.choices[0].message.content or ""
    logger.info("Copilot raw extraction response for receipt %s: %s", receipt_id or "n/a", raw)

    parsed = _normalize_extraction_payload(json.loads(raw.strip()))
    logger.info("Copilot parsed extraction payload for receipt %s: %s", receipt_id or "n/a", parsed)

    # Normalise amounts to 2 decimal places
    if parsed.get("total_amount") is not None:
        parsed["total_amount"] = round(float(parsed["total_amount"]), 2)
    if parsed.get("igv_amount") is not None:
        parsed["igv_amount"] = round(float(parsed["igv_amount"]), 2)
    elif parsed.get("total_amount") is not None:
        parsed["igv_amount"] = round(parsed["total_amount"] * (0.18 / 1.18), 2)

    extraction_data = ExtractionData(**parsed)
    logger.info(
        "Normalized extraction data for receipt %s: %s",
        receipt_id or "n/a",
        extraction_data.model_dump(mode="json"),
    )

    logger.info("extract_receipt_data: done receipt_id=%s", receipt_id or "n/a")
    return extraction_data
