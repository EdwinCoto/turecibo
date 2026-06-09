from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ReceiptStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class ExtractionStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class ReceiptSource(BaseModel):
    telegram_user_id: int
    telegram_chat_id: int
    telegram_message_id: int
    telegram_file_id: str
    telegram_file_unique_id: Optional[str] = None


class ReceiptPhoto(BaseModel):
    local_path: str
    size_bytes: int
    content_hash: Optional[str] = None


class ExtractionData(BaseModel):
    restaurant_name: Optional[str] = None
    ruc: Optional[str] = None
    total_amount: Optional[float] = None
    igv_amount: Optional[float] = None
    igv_rate: float = 0.18
    currency: str = "PEN"
    emission_date: Optional[date] = None
    dni: Optional[str] = None
    dni_valid: Optional[bool] = None


class ExtractionResult(BaseModel):
    status: ExtractionStatus = ExtractionStatus.PENDING
    processed_at: Optional[datetime] = None
    error: Optional[str] = None
    data: Optional[ExtractionData] = None


class Receipt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    receipt_date: Optional[date] = None
    receipt_fingerprint: Optional[str] = None
    status: ReceiptStatus = ReceiptStatus.PENDING
    source: ReceiptSource
    photo: Optional[ReceiptPhoto] = None
    extraction: ExtractionResult = Field(default_factory=ExtractionResult)

    def to_json_dict(self) -> dict:
        logger.info("Receipt.to_json_dict: receipt_id=%s status=%s", self.id, self.status)
        return self.model_dump(mode="json")
