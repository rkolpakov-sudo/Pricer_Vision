class SmartReview:
    """Semi-automatic review with confidence scoring.

    Input: list[dict] items from SpecStructurer
    (pos/name/specs/code/manufacturer/qty/unit/weight).
    Splits items into auto-approved and needs-review groups.
    """

    CONFIDENCE_THRESHOLD = 0.8

    def __init__(self, threshold: float = CONFIDENCE_THRESHOLD):
        self.threshold = threshold
        self.auto_approved = []
        self.needs_review = []

    def process_extraction(self, items: list[dict]) -> tuple[list[dict], list[dict]]:
        """Classify items by confidence. Returns (auto_approved, needs_review)."""
        self.auto_approved = []
        self.needs_review = []

        for row in items:
            confidence = self._calculate_confidence(row)
            row["confidence"] = confidence
            if confidence >= self.threshold:
                self.auto_approved.append(row)
            else:
                self.needs_review.append(row)

        return self.auto_approved, self.needs_review

    def _calculate_confidence(self, row: dict) -> float:
        """Heuristic confidence that a parsed item is correct.

        The item contract contains no price — price does not participate in scoring.
        """
        score = 0.0

        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip()
        manufacturer = str(row.get("manufacturer", "")).strip()
        specs = str(row.get("specs", "")).strip()
        qty = row.get("qty", 0) or 0
        unit = str(row.get("unit", "")).strip()

        if name:
            score += 0.4
        if _positive_qty(qty):
            score += 0.2
        if unit:
            score += 0.1
        if code or manufacturer:
            score += 0.2
        if specs:
            score += 0.1

        return min(score, 1.0)


def _positive_qty(value) -> bool:
    """True, если количество — положительное число (число или числовая строка)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        try:
            return float(value.strip()) > 0
        except (TypeError, ValueError):
            return False
    return False
