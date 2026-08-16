import logging

logger = logging.getLogger("pricer.validator")

CONF_MIN = 0.6
CONF_GOOD = 0.8
PRICE_ANOMALY_HIGH = 10_000_000
PRICE_SUSPICIOUS_LOW = 1.0


def validate_result(result: dict, spec_text: str = "") -> dict:
    result.setdefault("price", None)
    result.setdefault("confidence", 0.0)
    result.setdefault("url", "")
    result.setdefault("site", "")
    result.setdefault("reason", "")
    result.setdefault("requires_review", True)

    price = result.get("price")
    confidence = result.get("confidence", 0.0)

    if price is None:
        result["confidence"] = 0.0
        result["requires_review"] = True
        return result

    try:
        price = float(price)
        if price <= 0:
            result["price"] = None
            result["confidence"] = 0.0
            result["reason"] += " (invalid: negative or zero)"
            result["requires_review"] = True
            return result
    except (ValueError, TypeError):
        result["price"] = None
        result["confidence"] = 0.0
        result["reason"] += " (invalid: not a number)"
        result["requires_review"] = True
        return result

    result["price"] = price
    confidence = max(0.0, min(1.0, confidence))
    result["confidence"] = confidence

    if confidence < CONF_MIN:
        result["requires_review"] = True

    if confidence >= CONF_GOOD and result.get("url") and result.get("site"):
        result["requires_review"] = False

    if price > PRICE_ANOMALY_HIGH:
        result["confidence"] = round(result["confidence"] * 0.8, 2)
        result["requires_review"] = True
        result["reason"] += " (anomalous high price)"

    if price < PRICE_SUSPICIOUS_LOW:
        result["confidence"] = round(result["confidence"] * 0.5, 2)
        result["requires_review"] = True
        result["reason"] += " (suspicious low price)"

    result["confidence"] = round(min(result["confidence"], 1.0), 2)

    return result


def format_price(price: float) -> str:
    if price is None:
        return ""
    if price >= 1000:
        return f"{price:,.2f}".replace(",", " ")
    return f"{price:.2f}"
