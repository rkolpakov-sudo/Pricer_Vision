import pytest
from src.graph_engine import GraphEngine


class TestGraphEngine:
    def test_build_creates_tables(self, graph_engine):
        row = graph_engine._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in row]
        for t in ("approaches", "confirmed_prices", "hints", "product_sites", "product_types", "sites",
                  "matching_equivalences"):
            assert t in names

    def test_empty_stats(self, graph_engine):
        stats = graph_engine.get_stats()
        assert stats["approaches"] == 0
        assert stats["confirmed_prices"] == 0
        assert stats["sites"] == 0

    def test_save_and_load_approach(self, graph_engine, sample_approach):
        aid = graph_engine.save_approach(sample_approach)
        assert aid > 0

        approaches = graph_engine.get_approaches("cables", "tinko.ru")
        assert len(approaches) == 1
        assert approaches[0]["id"] == aid
        assert approaches[0]["site_id"] == "tinko.ru"

    def test_get_best_approach(self, graph_engine, sample_approach):
        graph_engine.save_approach(sample_approach)
        best = graph_engine.get_best_approach("cables", "tinko.ru")
        assert best is not None
        assert best["site_id"] == "tinko.ru"

    def test_get_best_approach_empty(self, graph_engine):
        assert graph_engine.get_best_approach("nonexistent", "") is None

    def test_approaches_by_product(self, graph_engine, sample_approach):
        graph_engine.save_approach(sample_approach)
        all_cables = graph_engine.get_approaches("cables")
        assert len(all_cables) == 1

    def test_save_confirmed_price(self, graph_engine):
        pid = graph_engine.save_confirmed_price({
            "spec_text": "test cable",
            "product_type_id": "cables",
            "site_id": "tinko.ru",
            "price": 1500.50,
            "url": "https://tinko.ru/product/cable1",
        })
        assert pid > 0

        prices = graph_engine.get_confirmed_prices("test cable", max_results=5)
        assert len(prices) >= 1
        assert prices[0]["price"] == 1500.50

    def test_get_confirmed_prices_skip_family_page_url(self, graph_engine):
        """Цена с семейной страницы (santech /catalog/N/M/i<id>/) НЕ возвращается
        как кандидат на reuse — на ней несколько товаров с одной ценой."""
        graph_engine.save_confirmed_price({
            "spec_text": "Кран шаровой Ду15, завод-изготовитель Ридан",
            "product_type_id": "valves",
            "site_id": "santech.ru",
            "price": 1193.2,
            "url": "https://www.santech.ru/catalog/317/318/i2641/",
        })
        assert graph_engine.get_confirmed_prices("Кран шаровой Ду15, завод-изготовитель Ридан") == []

    def test_get_confirmed_prices_skip_homepage_url(self, graph_engine):
        """Цена с главной/поисковой страницы НЕ возвращается как кандидат на reuse."""
        graph_engine.save_confirmed_price({
            "spec_text": "Кран шаровой Ду15, завод-изготовитель Ридан",
            "product_type_id": "valves",
            "site_id": "santech.ru",
            "price": 1193.2,
            "url": "https://www.santech.ru",
        })
        assert graph_engine.get_confirmed_prices("Кран шаровой Ду15, завод-изготовитель Ридан") == []
        graph_engine.save_confirmed_price({
            "spec_text": "Кран шаровой Ду20, завод-изготовитель Ридан",
            "product_type_id": "valves",
            "site_id": "santech.ru",
            "price": 2974.2,
            "url": "https://www.santech.ru/catalog/search/?search=кран",
        })
        assert graph_engine.get_confirmed_prices("Кран шаровой Ду20, завод-изготовитель Ридан") == []

    def test_get_confirmed_prices_by_token_overlap(self, graph_engine):
        graph_engine.save_confirmed_price({
            "spec_text": "ВВГ-нг 3x1.5 ОГНЕСТОЙКИЙ КАБЕЛЬ",
            "product_type_id": "cables",
            "site_id": "tinko.ru",
            "price": 100,
            "url": "https://tinko.ru/product/vvg-3x1-5",
        })
        same_size = graph_engine.get_confirmed_prices("ВВГ-нг 3x1.5 ОГНЕСТОЙКИЙ КАБЕЛЬ")
        assert len(same_size) >= 1

        # Разный типоразмер (3x1.5 vs 3x2.5) — цена НЕ переиспользуется
        other_size = graph_engine.get_confirmed_prices("ВВГ-нг 3x2.5 ОГНЕСТОЙКИЙ КАБЕЛЬ")
        assert other_size == []

    def test_get_confirmed_prices_rejects_other_size(self, graph_engine):
        """Кран шаровой Ду15 ≠ Ду20: цена с одного типоразмера не уходит на другой."""
        graph_engine.save_confirmed_price({
            "spec_text": "Кран шаровой Ду15, завод-изготовитель Ридан",
            "product_type_id": "valves",
            "site_id": "santech.ru",
            "price": 1193.2,
            "url": "https://www.santech.ru/catalog/317/318/i2641/v9/",
        })
        assert graph_engine.get_confirmed_prices("Кран шаровой Ду20") == []
        assert graph_engine.get_confirmed_prices("Кран шаровой Ду15")[0]["price"] == 1193.2

    def test_get_confirmed_prices_strict_one_sided_size(self, graph_engine):
        """Историческая запись БЕЗ размера не переиспользуется для позиции С размером.

        Регрессия vtk_spec_v2: цена «Изоляция Ø25» (conf 0.95) мгновенно
        уходила на трубки ENERGOFLEX 28…110 мм.
        """
        graph_engine.save_confirmed_price({
            "spec_text": "Изоляция 13 мм для труб Ø25, ENERGOFLEX SUPER",
            "product_type_id": "insulation",
            "site_id": "santech.ru",
            "price": 49.9,
            "url": "https://www.santech.ru/catalog/407/408/i23728/v43932/",
        })
        # размер есть только у запроса — строгий режим запрещает реюз
        assert graph_engine.get_confirmed_prices(
            "Трубка ENERGOFLEX Super SK 60/40-2"
        ) == []
        # тот же товар — реюз работает
        same = graph_engine.get_confirmed_prices("Изоляция 13 мм для труб Ø25, ENERGOFLEX SUPER")
        assert len(same) == 1 and same[0]["price"] == 49.9

    def test_get_confirmed_prices_guidance_lenient(self, graph_engine):
        """Щадящий режим (ignore_sizes=True) для ГИДА: цена семьи с ДРУГИМ размером
        показывается агенту как «куда идти», но в реюз (строгий) не попадает.

        Регрессия обучения: тренировка на C10 500x400 сохранила цену на
        mircli.ru/satro-paladin.com, но соседние размеры (500x600...) получали
        «Нет похожих цен» и начинали поиск с santech.ru заново.
        """
        graph_engine.save_confirmed_price({
            "spec_text": "Стальной панельный радиатор LEMAX Premium Compact Hygiene LEMAX Premium C10 500x400",
            "product_type_id": "plumbing_heating_radiators",
            "site_id": "mircli.ru",
            "price": 3580.0,
            "url": "https://mircli.ru/product/lemaks-premium-c-10h500h400/",
        })
        query = ("Стальной панельный радиатор LEMAX Premium Compact Hygiene "
                 "LEMAX Premium C10 500x600")
        # строгий реюз — НЕ переиспользуем цену другого размера
        assert graph_engine.get_confirmed_prices(query, strict_sizes=True) == []
        # щадящий гид — сайт с ценой семьи виден
        guide = graph_engine.get_confirmed_prices(query, strict_sizes=False, ignore_sizes=True)
        assert len(guide) == 1
        assert guide[0]["site_id"] == "mircli.ru"
        assert guide[0]["price"] == 3580.0

    def test_get_confirmed_prices_rejects_slash_size_mismatch(self, graph_engine):
        """Слэш-типоразмер 60/40 ≠ изоляция мм13/ø25 — разные размеры, отказ."""
        graph_engine.save_confirmed_price({
            "spec_text": "Трубка ENERGOFLEX Super SK 60/40-2",
            "product_type_id": "insulation",
            "site_id": "santech.ru",
            "price": 512.8,
            "url": "https://www.santech.ru/catalog/407/408/i23728/v43931/",
        })
        assert graph_engine.get_confirmed_prices(
            "Трубка ENERGOFLEX Super SK 110/40-2"
        ) == []

    def test_get_confirmed_prices_rejects_other_product(self, graph_engine):
        """Теплосчетчик ≠ Кран шаровой: общие структурные слова не дают совпадения."""
        graph_engine.save_confirmed_price({
            "spec_text": "Кран шаровой Ду15, завод-изготовитель Ридан",
            "product_type_id": "valves",
            "site_id": "santech.ru",
            "price": 1193.2,
        })
        assert graph_engine.get_confirmed_prices("Теплосчетчик, завод-изготовитель Пульсар") == []

    def test_get_confirmed_prices_rejects_other_brand(self, graph_engine):
        graph_engine.save_confirmed_price({
            "spec_text": "Кран шаровой Ду15, завод-изготовитель Ридан",
            "product_type_id": "valves",
            "site_id": "santech.ru",
            "price": 1193.2,
        })
        assert graph_engine.get_confirmed_prices("Кран шаровой Ду15, завод-изготовитель Пульсар") == []

    def test_get_confirmed_prices_rejects_other_subtype(self, graph_engine):
        """Клапан статический ≠ Клапан авт. (автомат): различающее слово подтипа
        «статический» должно исключать переиспользование цены автоматического клапана."""
        graph_engine.save_confirmed_price({
            "spec_text": "Клапан балансировочный авт. Ду15",
            "product_type_id": "valves",
            "site_id": "santech.ru",
            "price": 15676.8,
            "url": "https://www.santech.ru/catalog/337/340/i1322/v55/",
        })
        assert graph_engine.get_confirmed_prices("клапан баланс. статический Ду15") == []
        assert len(graph_engine.get_confirmed_prices("Клапан балансировочный авт. Ду15")) >= 1

    def test_save_confirmed_price_updates_existing(self, graph_engine):
        """Повторная запись той же spec+site через MemoryManager ОБНОВЛЯЕТ строку,
        а не плодит дубликаты (regression: 8 дублей «авт. Ду15» из rule8_reuse)."""
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        mm.save_price(
            spec_text="Кран шаровой Ду15", product_type="valves",
            site="santech.ru", price=100.0, url="https://santech.ru/product/kran15",
            confidence=0.95,
        )
        mm.save_price(
            spec_text="Кран шаровой Ду15", product_type="valves",
            site="santech.ru", price=120.0, url="https://santech.ru/product/kran15",
            confidence=0.95, reason="rule8_reuse",
        )
        prices = graph_engine.get_confirmed_prices("Кран шаровой Ду15", max_results=20)
        same = [p for p in prices if p.get("site_id") == "santech.ru"]
        assert len(same) == 1
        assert same[0]["price"] == 120.0

    def test_save_hint(self, graph_engine):
        hid = graph_engine.save_hint("cables", "tinko.ru", "Искать в разделе Кабель", 0.8)
        assert hid > 0

        hints = graph_engine.get_hints("cables")
        assert len(hints) == 1
        assert hints[0]["hint_text"] == "Искать в разделе Кабель"

    def test_save_discovered_site(self, graph_engine):
        sid = graph_engine.save_discovered_site("new-site.ru", "New Site", "cables")
        assert sid is not None

        sites = graph_engine.get_sites_for_product("cables")
        assert any(s["id"] == "new-site.ru" for s in sites)

    def test_set_site_priority_creates_new_site(self, graph_engine):
        ok = graph_engine.set_product_site_priority("cables", "brand-new.example.com", 0)
        assert ok is True
        sites = graph_engine.get_sites_for_product("cables")
        entry = next((s for s in sites if s["id"] == "brand-new.example.com"), None)
        assert entry is not None
        assert entry["priority"] == 0

    def test_set_site_priority_updates_existing(self, graph_engine):
        graph_engine.save_discovered_site("existing.ru", "Existing", "cables")
        graph_engine.set_product_site_priority("cables", "existing.ru", 1)
        sites = graph_engine.get_sites_for_product("cables")
        entry = next((s for s in sites if s["id"] == "existing.ru"), None)
        assert entry is not None
        assert entry["priority"] == 1

    def test_site_priority_persists_across_engine(self, graph_engine):
        graph_engine.set_product_site_priority("cables", "persist.example.com", 2)
        graph_engine._built = False
        sites = graph_engine.get_sites_for_product("cables")
        assert any(s["id"] == "persist.example.com" and s["priority"] == 2 for s in sites)

    def test_classify_product_type(self, graph_engine):
        assert graph_engine.classify_product_type("ВВГ-нг") == "unknown"
        graph_engine.save_product_type("cables", "Кабели", keywords="ВВГ, NYM, кабель")
        assert graph_engine.classify_product_type("ВВГ-нг 3x1.5") == "cables"

    def test_update_approach_success(self, graph_engine, sample_approach):
        aid = graph_engine.save_approach(sample_approach)
        graph_engine.update_approach_success(aid)
        a = graph_engine.get_approaches("cables", "tinko.ru")[0]
        assert a["success_count"] >= 2

    def test_new_approach_default_success_one(self, graph_engine, sample_approach):
        """Новая запись подхода по умолчанию успешна (дефолт сохранён) — системный
        путь после найденной цены полагается на это."""
        aid = graph_engine.save_approach(sample_approach)
        a = graph_engine.get_approaches("cables", "tinko.ru")[0]
        assert a["success_count"] >= 1

    def test_approach_explicit_zero_success_not_boosted(self, graph_engine, sample_approach):
        """Агентская «заглушка» (save_approach без цены, success_count=0) не должна
        ранжироваться как успешный подход."""
        data = dict(sample_approach)
        data["success_count"] = 0
        aid = graph_engine.save_approach(data)
        a = graph_engine.get_approaches("cables", "tinko.ru")[0]
        assert a["success_count"] == 0


    def test_update_approach_failure(self, graph_engine, sample_approach):
        aid = graph_engine.save_approach(sample_approach)
        graph_engine.update_approach_failure(aid)
        a = graph_engine.get_approaches("cables", "tinko.ru")[0]
        assert a["consecutive_failures"] == 1

    def test_cooldown_after_3_failures(self, graph_engine, sample_approach):
        aid = graph_engine.save_approach(sample_approach)
        for _ in range(3):
            graph_engine.update_approach_failure(aid)
        a = graph_engine.get_best_approach("cables", "tinko.ru")
        assert a is None

    def test_yaml_seed_loading(self, graph_engine, sample_yaml):
        graph_engine.load_yaml_seed(sample_yaml)
        stats = graph_engine.get_stats()
        assert stats["sites"] >= 2
        assert stats["product_types"] >= 1

        sites = graph_engine.get_sites_for_product("cables_power_cables")
        assert len(sites) == 2

    def test_get_cached_categories(self, graph_engine, sample_yaml):
        graph_engine.load_yaml_seed(sample_yaml)
        cats = graph_engine.get_cached_categories()
        assert "cables_power_cables" in cats
        assert len(cats["cables_power_cables"]["sites"]) == 2

    def test_get_recent_approaches(self, graph_engine, sample_approach):
        for _ in range(3):
            graph_engine.save_approach(sample_approach)
        recent = graph_engine.get_recent_approaches(2)
        assert len(recent) == 2

    def test_build_idempotent(self, graph_engine):
        graph_engine.build()
        graph_engine.build()
        assert graph_engine._built is True

    def test_price_without_product_type(self, graph_engine):
        pid = graph_engine.save_confirmed_price({
            "spec_text": "generic item",
            "product_type_id": None,
            "site_id": "some-site.ru",
            "price": 500,
        })
        assert pid > 0

    def test_save_hint_with_expires_at(self, graph_engine):
        from datetime import datetime, timedelta
        exp = (datetime.now() + timedelta(days=90)).isoformat()
        hid = graph_engine.save_hint("cables", "tinko.ru", "TTL hint", 0.5, expires_at=exp)
        assert hid > 0
        hints = graph_engine.get_hints("cables")
        assert hints[0]["expires_at"] == exp

    def test_delete_expired_hints(self, graph_engine):
        from datetime import datetime, timedelta
        past = (datetime.now() - timedelta(days=1)).isoformat()
        future = (datetime.now() + timedelta(days=90)).isoformat()
        graph_engine.save_hint("cables", "tinko.ru", "expired", 0.5, expires_at=past)
        graph_engine.save_hint("cables", "keaz.ru", "active", 0.5, expires_at=future)
        graph_engine.save_hint("cables", "keaz.ru", "no-ttl", 0.5, expires_at=None)
        deleted = graph_engine.delete_expired_hints()
        assert deleted == 1
        hints = graph_engine.get_hints("cables")
        texts = {h["hint_text"] for h in hints}
        assert "expired" not in texts
        assert "active" in texts
        assert "no-ttl" in texts

    def test_apply_pragmas(self, graph_engine):
        cache_rows = graph_engine._conn.execute("PRAGMA cache_size").fetchall()
        assert cache_rows and int(cache_rows[0][0]) == -64000
        sync_row = graph_engine._conn.execute("PRAGMA synchronous").fetchone()
        assert int(sync_row[0]) == 1


class TestMatchingEquivalences:
    def test_record_and_has(self, graph_engine):
        assert graph_engine.has_matching_equivalence("Кран Ду15", "Кран шаровой Ду15") is False
        graph_engine.record_matching_equivalence("Кран Ду15", "Кран шаровой Ду15")
        assert graph_engine.has_matching_equivalence("Кран Ду15", "Кран шаровой Ду15") is True

    def test_normalization_case_and_whitespace(self, graph_engine):
        graph_engine.record_matching_equivalence("  КРАН   Ду15 ", "Кран шаровой Ду15")
        assert graph_engine.has_matching_equivalence("кран ду15", "кран шаровой ду15") is True

    def test_duplicate_ignored(self, graph_engine):
        graph_engine.record_matching_equivalence("А", "Б")
        graph_engine.record_matching_equivalence("А", "Б")
        assert len(graph_engine.get_matching_equivalences()) == 1

    def test_persists_across_rebuild(self, graph_engine):
        graph_engine.record_matching_equivalence("А", "Б")
        graph_engine.rebuild()
        assert graph_engine.has_matching_equivalence("А", "Б") is True

    def test_empty_input_ignored(self, graph_engine):
        graph_engine.record_matching_equivalence("", "Б")
        graph_engine.record_matching_equivalence("А", "")
        assert graph_engine.get_matching_equivalences() == []

    def test_memory_manager_wrappers(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        mm.record_matching_equivalence("А", "Б")
        assert mm.has_matching_equivalence("А", "Б") is True
        assert mm.has_matching_equivalence("А", "В") is False


class TestCategories:
    def test_yaml_seed_creates_categories(self, graph_engine, sample_yaml):
        graph_engine.load_yaml_seed(sample_yaml)
        cats = graph_engine.list_categories()
        ids = [c["id"] for c in cats]
        assert "cables" in ids
        c = next(x for x in cats if x["id"] == "cables")
        assert c["type_count"] == 1

    def test_crud_category(self, graph_engine):
        graph_engine.save_category("insulation", "Изоляция", priority=3)
        assert any(c["id"] == "insulation" for c in graph_engine.list_categories())
        graph_engine.rename_category("insulation", "Изоляция для труб")
        cats = graph_engine.list_categories()
        c = next(x for x in cats if x["id"] == "insulation")
        assert c["name"] == "Изоляция для труб"

    def test_delete_category_only_when_empty(self, graph_engine):
        graph_engine.save_category("empty", "Пустая")
        ok, msg = graph_engine.delete_category("empty")
        assert ok
        # непустая — нельзя удалить (иначе типы осиротеют)
        graph_engine.save_category("full", "Полная")
        graph_engine.save_product_type("full_a", "A", category="full")
        ok, msg = graph_engine.delete_category("full")
        assert not ok and "тип" in msg

    def test_set_product_type_category_keeps_metadata(self, graph_engine):
        graph_engine.save_product_type("t1", "Т1", category="c1", keywords="кабель")
        graph_engine.save_category("c2", "Кат2")
        graph_engine.set_product_type_category("t1", "c2")
        prod = graph_engine.get_all_products()["t1"]
        assert prod["category"] == "c2"
        assert prod["keywords"] == "кабель"   # перенос не трогает keywords/name
        assert prod["name"] == "Т1"

    def test_yaml_reload_keeps_user_type_edits(self, graph_engine, sample_yaml):
        graph_engine.load_yaml_seed(sample_yaml)          # cables/power_cables из YAML
        graph_engine.save_product_type("cables_power_cables", "ИМЯ ПОЛЬЗОВАТЕЛЯ",
                                       category="cables", keywords="пользовательские")
        graph_engine.load_yaml_seed(sample_yaml)          # повторный сид
        prod = graph_engine.get_all_products()["cables_power_cables"]
        assert prod["name"] == "ИМЯ ПОЛЬЗОВАТЕЛЯ"          # не перезаписан
        assert prod["keywords"] == "пользовательские"

    def test_split_product_type_migrates_and_cleans_source(self, graph_engine):
        graph_engine.save_product_type("tools", "Инструменты", category="tools_general",
                                       keywords="рулетка, изоляция, energoflex")
        graph_engine.save_confirmed_price({
            "spec_text": "Изоляция 13 мм для труб Ø25 ENERGOFLEX",
            "product_type_id": "tools", "site_id": "vseinstrumenti.ru",
            "price": 200.0, "url": "https://vseinstrumenti.ru/product/x",
        })
        graph_engine.save_confirmed_price({
            "spec_text": "Рулетка 5м",
            "product_type_id": "tools", "site_id": "tinko.ru",
            "price": 150.0, "url": "https://tinko.ru/product/y",
        })
        res = graph_engine.split_product_type(
            "tools", "tools_general_pipe_insulation", "Изоляция для труб",
            "insulation", "изоляция, energoflex, теплоизоляция")
        assert res["ok"] is True
        assert res["confirmed_moved"] == 1
        # спецификация «Изоляция …» теперь классифицируется в новый тип
        pt = graph_engine.classify_product_type("Изоляция 13 мм для труб Ø40 ENERGOFLEX")
        assert pt == "tools_general_pipe_insulation"
        # «Рулетка» осталась в tools
        pt2 = graph_engine.classify_product_type("Рулетка 5м")
        assert pt2 == "tools"
        # новые keywords убраны из источника
        prod = graph_engine.get_all_products()["tools"]
        assert "изоляция" not in (prod["keywords"] or "")
        assert "energoflex" not in (prod["keywords"] or "")

    def test_split_source_not_found(self, graph_engine):
        res = graph_engine.split_product_type("nope", "x", "X", "c", "kw")
        assert res["ok"] is False

    def test_split_copies_only_moved_site_records(self, graph_engine):
        """Fix 2: при сплите в новый тип НЕ копируется весь список сайтов источника —
        только сайты, где у перенесённых записей были реальные цены/подходы."""
        graph_engine.save_product_type("tools", "Инструменты", category="tools_general",
                                       keywords="рулетка, изоляция, energoflex")
        # Белый список источника: vseinstrumenti (есть переносимая цена) и tinko (мусор для изоляции)
        for sid in ("vseinstrumenti.ru", "tinko.ru", "satro-paladin.com"):
            graph_engine._conn.execute(
                "INSERT OR IGNORE INTO sites (id, name, base_url) VALUES (?, ?, ?)",
                (sid, sid, f"https://{sid}"))
            graph_engine._conn.execute(
                "INSERT OR IGNORE INTO product_sites (product_type_id, site_id, priority, consecutive_failures) "
                "VALUES ('tools', ?, 1, 0)", (sid,))
        graph_engine.save_confirmed_price({
            "spec_text": "Изоляция 13 мм для труб Ø25 ENERGOFLEX",
            "product_type_id": "tools", "site_id": "vseinstrumenti.ru",
            "price": 200.0, "url": "https://vseinstrumenti.ru/product/x",
        })
        graph_engine._conn.commit()
        graph_engine._built = False
        graph_engine.build()

        res = graph_engine.split_product_type(
            "tools", "insulation", "Изоляция для труб",
            "insulation", "изоляция, energoflex, теплоизоляция")
        assert res["ok"] is True
        assert res["confirmed_moved"] == 1
        new_sites = {s["id"] for s in graph_engine.get_sites_for_product("insulation")}
        # Переносимая цена была на vseinstrumenti — сайт скопирован.
        assert "vseinstrumenti.ru" in new_sites
        # Мусорные сайты источника (tinko, satro-paladin) НЕ копируются.
        assert "tinko.ru" not in new_sites
        assert "satro-paladin.com" not in new_sites

