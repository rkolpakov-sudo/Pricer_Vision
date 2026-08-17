import hashlib
import json
import re
import time
from pathlib import Path
from typing import Optional

from src.approach_relevance import product_name_matches

CACHE_MAX_ENTRIES = 1000
_EVICT_RATIO = 0.2


class SemanticCache:
    """Кэш результатов для похожих товаров.

    Без embedding-моделей (экономия ресурсов): нормализация названия
    + Jaccard-схожесть по общим словам.
    """

    def __init__(self, cache_file="data/semantic_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache = self._load()

    def get_similar(self, product_name: str,
                    threshold: float = 0.7) -> Optional[dict]:
        normalized = self._normalize(product_name)

        for cached_data in self.cache.values():
            similarity = self._calculate_similarity(
                normalized, cached_data.get("normalized_name", "")
            )
            if similarity >= threshold and product_name_matches(
                product_name, cached_data.get("original_name", "")
            ):
                return {
                    **cached_data.get("result", {}),
                    "cache_hit": True,
                    "similarity": similarity,
                    "original_query": cached_data.get("original_name", ""),
                }
        return None

    def store(self, product_name: str, result: dict):
        normalized = self._normalize(product_name)
        key = hashlib.md5(normalized.encode("utf-8")).hexdigest()

        self.cache[key] = {
            "original_name": product_name,
            "normalized_name": normalized,
            "result": result,
            "timestamp": time.time(),
        }

        if len(self.cache) > CACHE_MAX_ENTRIES:
            self._evict_oldest()

        self._save()

    @staticmethod
    def _normalize(name: str) -> str:
        name = re.sub(r"\(.*?\)", "", name)
        name = re.sub(r"\b\d+\s?(мм|м|кг|г|шт)\b", "", name)
        return " ".join(name.lower().split())

    @staticmethod
    def _calculate_similarity(s1: str, s2: str) -> float:
        words1 = set(s1.split())
        words2 = set(s2.split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union)

    def _load(self) -> dict:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except OSError as e:
            import logging
            logging.getLogger("pricer.cache").warning("Semantic cache save failed: %s", e)

    def _evict_oldest(self):
        sorted_items = sorted(
            self.cache.items(),
            key=lambda x: x[1].get("timestamp", 0),
        )
        to_remove = max(1, int(len(self.cache) * _EVICT_RATIO))
        for key, _ in sorted_items[:to_remove]:
            del self.cache[key]

    def clear(self):
        self.cache = {}
        self._save()
