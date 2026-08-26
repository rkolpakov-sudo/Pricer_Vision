import json
import pytest

from src.semantic_cache import SemanticCache


@pytest.fixture
def cache(tmp_path):
    return SemanticCache(cache_file=str(tmp_path / "cache.json"))


def test_empty_cache_returns_none(cache):
    assert cache.get_similar("ВВГнг 3x1.5") is None


def test_store_and_get_exact(cache):
    cache.store("ВВГнг 3x1.5 кабель", {"price": 100.0, "confidence": 0.95, "url": "https://tinko.ru/product/vvg", "site": "tinko.ru"})
    hit = cache.get_similar("ВВГнг 3x1.5 кабель")
    assert hit is not None
    assert hit["cache_hit"] is True
    assert hit["price"] == 100.0
    assert hit["similarity"] >= 0.9


def test_store_and_get_similar(cache):
    cache.store("ВВГнг 3x1.5 кабель", {"price": 100.0, "confidence": 0.95, "url": "https://tinko.ru/product/vvg", "site": "tinko.ru"})
    hit = cache.get_similar("ВВГнг 3x1.5 100м кабель")
    assert hit is not None
    assert hit["similarity"] >= 0.7


def test_no_hit_for_different_product(cache):
    cache.store("ВВГнг 3x1.5 кабель", {"price": 100.0, "confidence": 0.95, "url": "https://tinko.ru/product/vvg", "site": "tinko.ru"})
    assert cache.get_similar("индукционный котёл") is None


def test_no_hit_for_other_size(cache):
    """Разный типоразмер не даёт cache hit (Ду15 ≠ Ду20), даже при низком пороге."""
    cache.store("Кран шаровой Ду15, завод-изготовитель Ридан",
                {"price": 1193.2, "confidence": 0.95, "url": "https://www.santech.ru/catalog/317/318/i2641/v9/", "site": "santech.ru"})
    assert cache.get_similar("Кран шаровой Ду20, завод-изготовитель Ридан", threshold=0.5) is None
    hit = cache.get_similar("Кран шаровой Ду15, завод-изготовитель Ридан")
    assert hit is not None and hit["price"] == 1193.2


def test_no_hit_when_size_only_on_query_side(cache):
    """Строгие размеры: запись без размера не отдаётся позиции с размером.

    Регрессия vtk_spec_v2: изоляция Ø25 из кэша улетала на трубки
    ENERGOFLEX всех диаметров.
    """
    cache.store("Изоляция для труб, ENERGOFLEX SUPER",
                {"price": 49.9, "confidence": 0.95, "url": "https://www.santech.ru/catalog/407/408/i23728/v43932/", "site": "santech.ru"})
    assert cache.get_similar("Трубка ENERGOFLEX Super SK 60/40-2", threshold=0.25) is None


def test_no_hit_for_other_brand(cache):
    """Разный бренд не даёт cache hit (Ридан ≠ Пульсар), даже при низком пороге."""
    cache.store("Кран шаровой Ду15, завод-изготовитель Ридан",
                {"price": 1193.2, "confidence": 0.95, "url": "https://www.santech.ru/catalog/317/318/i2641/v9/", "site": "santech.ru"})
    assert cache.get_similar("Кран шаровой Ду15, завод-изготовитель Пульсар", threshold=0.5) is None


def test_low_threshold_blocks(cache):
    cache.store("ВВГнг 3x1.5 кабель", {"price": 100.0, "confidence": 0.95, "url": "https://tinko.ru/product/vvg", "site": "tinko.ru"})
    # Схожесть "кабель ввгнг 3x1.5" vs "труба пвх" < 1.0 — threshold 1.0 блокирует
    assert cache.get_similar("труба пвх гибкая", threshold=1.0) is None


def test_store_overwrites_same_normalized(cache):
    cache.store("ВВГнг 3x1.5 кабель", {"price": 100.0, "confidence": 0.95, "url": "https://tinko.ru/product/vvg", "site": "tinko.ru"})
    cache.store("ВВГнг 3x1.5 кабель", {"price": 120.0, "confidence": 0.9, "url": "https://tinko.ru/product/vvg", "site": "tinko.ru"})
    hit = cache.get_similar("ВВГнг 3x1.5 кабель")
    assert hit["price"] == 120.0


def test_persists_to_disk(tmp_path):
    path = tmp_path / "cache.json"
    c1 = SemanticCache(cache_file=str(path))
    c1.store("ВВГнг 3x1.5", {"price": 100.0, "confidence": 0.95, "url": "https://tinko.ru/product/vvg", "site": "tinko.ru"})
    c2 = SemanticCache(cache_file=str(path))
    hit = c2.get_similar("ВВГнг 3x1.5")
    assert hit is not None
    assert hit["price"] == 100.0


def test_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{ not valid json", encoding="utf-8")
    cache = SemanticCache(cache_file=str(path))
    assert cache.get_similar("anything") is None


def test_normalize_strips_parens_and_units():
    assert SemanticCache._normalize("Кабель (медный) ВВГнг 3x1.5 100м") == "кабель ввгнг 3x1.5"
    assert SemanticCache._normalize("  ВВГ  3x1.5  ") == "ввг 3x1.5"


def test_similarity_zero_for_empty():
    assert SemanticCache._calculate_similarity("", "x") == 0.0
    assert SemanticCache._calculate_similarity("a", "") == 0.0


def test_evict_oldest(tmp_path):
    cache = SemanticCache(cache_file=str(tmp_path / "cache.json"))
    cache.cache = {}
    from src.semantic_cache import CACHE_MAX_ENTRIES
    for i in range(CACHE_MAX_ENTRIES + 50):
        cache.store(f"товар {i}", {"price": i, "confidence": 0.9, "url": "https://x.ru/product/i", "site": "x.ru"})
    assert len(cache.cache) <= CACHE_MAX_ENTRIES


def test_clear(cache):
    cache.store("ВВГнг", {"price": 1, "confidence": 0.9, "url": "https://x.ru/product/1", "site": "x.ru"})
    cache.clear()
    assert cache.get_similar("ВВГнг") is None


def test_brand_mismatch_entry_stored_but_not_auto_reusable(cache):
    """Фолбэк «не совпадает бренд» пишется в кэш, но из-за капа confidence (<= 0.5)
    не проходит порог auto-reuse process_row (confidence > 0.8)."""
    cache.store("Клапан балансировочный авт. фланцевый Ду100", {
        "price": 328106.6, "confidence": 0.5, "requires_review": True, "brand_mismatch": True,
        "url": "https://www.santech.ru/catalog/337/340/i1322/v55/", "site": "santech.ru",
    })
    hit = cache.get_similar("Клапан балансировочный авт. фланцевый Ду100")
    assert hit is not None
    assert hit["brand_mismatch"] is True
    assert hit["confidence"] <= 0.8
