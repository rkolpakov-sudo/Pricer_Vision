from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional
from enum import Enum


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    EXTRACT = "extract"
    SWITCH_SITE = "switch_site"
    ASK_USER = "ask_user"


class AgentDecision(BaseModel):
    """Валидированное решение агента (для будущего рефакторинга на AgentDecision;
    сейчас цикл работает с tool_calls напрямую)."""
    reasoning: str = Field(..., min_length=10, description="Обоснование действия")
    action: ActionType
    target: Optional[str] = None
    value: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_target(self):
        if self.action in (ActionType.CLICK, ActionType.TYPE) and not self.target:
            raise ValueError(f"Action {self.action} requires target")
        return self


class ExtractedPrice(BaseModel):
    """Валидированная извлечённая цена"""
    product_name: str
    price: float = Field(..., gt=0)
    currency: str = "RUB"
    url: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    raw_text: Optional[str] = None

    @field_validator("price")
    @classmethod
    def validate_price(cls, v):
        if v > 10_000_000:  # Защита от галлюцинаций (совпадает с PRICE_ANOMALY_HIGH в validator.py)
            raise ValueError(f"Unrealistic price: {v}")
        return round(v, 2)


class ExtractionResult(BaseModel):
    """Результат извлечения для одного товара — контракт process_row"""
    spec_text: str = Field(..., min_length=1)
    product_type: str = "unknown"
    found: bool
    price: Optional[float] = None
    confidence: float = 0.0
    url: str = ""
    site: str = ""
    reason: str = ""
    requires_review: bool = True
    error: Optional[str] = None
    elapsed: Optional[float] = None
    brand_mismatch: bool = False

    @model_validator(mode="after")
    def validate_price(self):
        if self.found and self.price is None:
            raise ValueError("found=True requires price")
        if self.price is not None:
            if self.price <= 0:
                raise ValueError(f"Price must be positive: {self.price}")
            if self.price > 10_000_000:
                raise ValueError(f"Unrealistic price: {self.price}")
        return self