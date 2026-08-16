import logging

logger = logging.getLogger("pricer.limits")

BASE_ROUNDS = 10
MIN_ROUNDS = 5
MAX_ROUNDS = 30
BASE_ROUNDS_PER_SITE = 15


class AdaptiveRoundManager:
    """Динамические лимиты раундов на основе сложности сайта.

    Надстраивается над существующей логикой (site_round_limits с порогом
    consecutive_failures >= 3): считает per-site лимит через доступные
    в БД данные (consecutive_failures, success_count, число подходов).
    Поля is_spa/has_antibot/avg_dom_depth появятся в фазе 3 (SiteAnalyzer).
    """

    def __init__(self, base_rounds: int = BASE_ROUNDS,
                 min_rounds: int = MIN_ROUNDS,
                 max_rounds: int = MAX_ROUNDS):
        self.base_rounds = base_rounds
        self.min_rounds = min_rounds
        self.max_rounds = max_rounds

    def calculate_limit(self, site_profile: dict,
                        product_complexity: float = 0.0) -> int:
        complexity_factor = self._assess_complexity(site_profile)

        limit = int(
            self.base_rounds
            * complexity_factor
            * (1 + product_complexity * 0.5)
        )

        return max(self.min_rounds, min(limit, self.max_rounds))

    def _assess_complexity(self, site_profile: dict) -> float:
        """Оценка сложности сайта (1.0 = простой, до ~3.0 = сложный).

        Сложность растёт при: низком success_rate, множестве consecutive_failures,
        наличии антибота (задел под фазу 3).
        """
        factors = []

        if site_profile.get("has_antibot", False):
            factors.append(1.8)
        else:
            factors.append(1.0)

        success_rate = site_profile.get("success_rate", 0.5)
        factors.append(1.0 / max(success_rate, 0.1))

        consec = site_profile.get("consecutive_failures", 0)
        factors.append(1.0 + min(consec, 5) * 0.15)

        return sum(factors) / len(factors)

    def should_extend(self, current_round: int,
                      progress_score: float) -> bool:
        """Есть прогресс → можно продлить лимит."""
        return progress_score > 0.3

    def per_site_limits(self, sites: list[dict]) -> dict:
        """Строит {site_id: limit} для списка сайтов.

        Сохраняет обратную совместимость: сайты без данных получают
        base_rounds (по умолчанию 10), сайты с failures>=3 — MIN_ROUNDS.
        """
        limits = {}
        for s in sites:
            sid = s.get("id", "")
            if not sid:
                continue
            consec = s.get("consecutive_failures", 0)
            if consec >= 3:
                limits[sid] = self.min_rounds
            else:
                limits[sid] = self.base_rounds
        return limits
