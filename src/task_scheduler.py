import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger("pricer.scheduler")


@dataclass
class ProcessingBatch:
    site_id: str
    site_url: str
    products: List = field(default_factory=list)
    priority: float = 0.0


class TaskScheduler:
    """Группирует товары по целевым сайтам, чтобы минимизировать
    переключения контекста браузера между товарами одного сайта."""

    def __init__(self, memory_manager, site_profiles: dict | None = None):
        self.mm = memory_manager
        self.site_profiles = site_profiles or {}

    def plan_processing_order(self, products: List) -> List[ProcessingBatch]:
        by_site = {}
        for product in products:
            spec_text = getattr(product, "text", "") or str(product)
            site_id = self._determine_target_site(spec_text)
            batch = by_site.setdefault(site_id, ProcessingBatch(
                site_id=site_id,
                site_url=self._site_url(site_id),
            ))
            batch.products.append(product)

        batches = list(by_site.values())
        for batch in batches:
            site_info = self._get_site_profile(batch.site_id)
            batch.priority = self._calculate_priority(site_info, len(batch.products))
            batch.site_url = site_info.get("base_url") or batch.site_url

        batches.sort(key=lambda b: b.priority, reverse=True)
        return batches

    def ordered_specs(self, products: List) -> List:
        """Возвращает товары в порядке батчей (плоский список)."""
        return [p for batch in self.plan_processing_order(products) for p in batch.products]

    def _determine_target_site(self, spec_text: str) -> str:
        try:
            product_type = self.mm._engine.classify_product_type(spec_text)
        except Exception as e:
            logger.warning("classify_product_type failed: %s", e)
            product_type = "unknown"
        sites = self.mm.get_sites(product_type)
        if sites:
            best = max(
                sites,
                key=lambda s: (s.get("priority", 2) * 1.0
                               - s.get("consecutive_failures", 0) * 0.5),
            )
            return best.get("id", "")
        return "yandex.ru"

    def _get_site_profile(self, site_id: str) -> dict:
        site = self.mm.get_all_sites().get(site_id, {})
        site = dict(site)
        approaches = []
        try:
            approaches = self.mm.get_approaches_by_site(site_id)
        except Exception as e:
            logger.warning("get_approaches_by_site failed for %s: %s", site_id, e)
        total_ok = sum(a.get("success_count", 0) for a in approaches)
        total_fail = sum(a.get("failures_count", 0) for a in approaches)
        learned = self.site_profiles.get(site_id, {})
        # Профиль из LearningLoop (последний прогон) приоритетнее расчёта по подходам
        site["success_rate"] = learned.get("success_rate", total_ok / max(total_ok + total_fail, 1))
        site["has_antibot"] = learned.get("has_antibot", False)
        site["speed_score"] = learned.get("speed_score", 0.5)
        site["block_count"] = learned.get("block_count", 0)
        site["avg_attempts"] = learned.get("avg_attempts", 0)
        return site

    def _calculate_priority(self, site_info: dict, product_count: int) -> float:
        success_rate = site_info.get("success_rate", 0.5)
        has_antibot = site_info.get("has_antibot", False)
        priority = (
            success_rate * 0.4
            + min(product_count / 10, 1.0) * 0.3
            + (0 if has_antibot else 1) * 0.2
            + site_info.get("speed_score", 0.5) * 0.1
        )
        return priority

    def _site_url(self, site_id: str) -> str:
        site = self.mm.get_all_sites().get(site_id, {})
        base_url = site.get("base_url") or ""
        return base_url or f"https://{site_id}"
