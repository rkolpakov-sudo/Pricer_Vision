import pytest
from src.agent_loop import (
    GRAPH_TOOL_NAMES, GRAPH_TOOL_DEFS, SYSTEM_PROMPT,
    _build_context, _execute_graph_tool,
    _error_result, _result_to_schema,
    _estimate_tokens, _trim_messages_for_budget,
    _apply_approach, _is_standard_reference,
    _stuck_target, _is_product_card_url,
    _extract_price_candidate, _build_diagnostic_message,
    _is_family_page, _is_empty_search_result,
    _pick_best_fallback, _fallback_result,
    _mismatch_warning_content,
    _portable_step_target, format_steps, format_steps_detailed,
    CONTEXT_TOKEN_BUDGET, EMPTY_PROBE_LIMIT,
    TEMP_EXPLORATION, TEMP_NAVIGATION, TEMP_EXTRACTION, TEMP_RECOVERY,
    _penalize_approaches, _deprecate_site_approaches, _inject_facts_block,
)
from src.session_facts import RowFacts


class TestConstants:
    def test_graph_tools_defined(self):
        assert "get_approaches" in GRAPH_TOOL_NAMES
        assert "save_approach" in GRAPH_TOOL_NAMES
        assert "get_confirmed_prices" in GRAPH_TOOL_NAMES
        assert "save_confirmed_price" in GRAPH_TOOL_NAMES
        assert "search_sites" in GRAPH_TOOL_NAMES
        assert "save_discovered_site" in GRAPH_TOOL_NAMES
        assert "get_hints" in GRAPH_TOOL_NAMES
        assert len(GRAPH_TOOL_NAMES) == 7

    def test_system_prompt_not_empty(self):
        assert len(SYSTEM_PROMPT) > 50

    def test_system_prompt_pre_click_verification_rule(self):
        assert "«для проверки полного названия»" in SYSTEM_PROMPT
        assert "фланцевый" in SYSTEM_PROMPT
        assert "максимум 1 шаг на проверку заголовка" in SYSTEM_PROMPT

    def test_system_prompt_pagination_guidance(self):
        assert "a[href*=page]" in SYSTEM_PROMPT
        assert "НЕ извлекай одну и ту же страницу повторно" in SYSTEM_PROMPT

    def test_system_prompt_brand_mismatch_fallback_rule(self):
        assert "brand_mismatch=true" in SYSTEM_PROMPT
        assert "не совпадает бренд" in SYSTEM_PROMPT
        assert "Бренд — НЕ жёсткий атрибут" in SYSTEM_PROMPT

    def test_system_prompt_no_manual_url_encoding(self):
        """Регрессия: агент перебирал кириллические %-коды (е/к/и вместо х) при
        ручном конструировании URL поиска. Промпт запрещает это."""
        assert "НЕ собирай URL поиска вручную" in SYSTEM_PROMPT
        assert "percent-кодирование кириллицы" in SYSTEM_PROMPT
        assert "location.href" in SYSTEM_PROMPT
        assert "Не нажимай Enter на SPA-сайтах — используй прямые URL" not in SYSTEM_PROMPT

    def test_tool_defs_have_descriptions(self):
        for td in GRAPH_TOOL_DEFS:
            assert len(td["function"]["description"]) > 5

    def test_phase_temperatures_distinct(self):
        temps = {TEMP_EXPLORATION, TEMP_NAVIGATION, TEMP_EXTRACTION, TEMP_RECOVERY}
        assert len(temps) == 4
        assert TEMP_EXTRACTION == 0.1
        assert TEMP_EXPLORATION == 0.7


class TestContextBudget:
    def test_budget_constant(self):
        assert CONTEXT_TOKEN_BUDGET == 8000

    def test_estimate_tokens(self):
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("short") == 1
        assert _estimate_tokens("a" * 40) == 10

    def test_trim_keeps_small_messages(self):
        messages = [{"role": "system", "content": "s"}] + [
            {"role": "user", "content": "hello world"}
        ]
        out = _trim_messages_for_budget(messages)
        assert out == messages

    def test_trim_removes_old_when_over_budget(self):
        big = "x" * 40000  # ~10000 tokens, over budget alone
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": big},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "final"},
        ]
        out = _trim_messages_for_budget(messages)
        # последний user + system сохраняются
        roles = [m["role"] for m in out]
        assert roles[-1] == "user"
        assert roles[0] == "system"
        total = sum(_estimate_tokens(m.get("content", "")) for m in out)
        assert total <= CONTEXT_TOKEN_BUDGET

    def test_trim_preserves_last_user(self):
        messages = [{"role": "system", "content": "s"}]
        for i in range(50):
            messages.append({"role": "user", "content": "m" * 1000})
        messages.append({"role": "assistant", "content": "tail"})
        out = _trim_messages_for_budget(messages)
        assert out[-1]["content"] == "tail"
        assert any(m["role"] == "user" for m in out)


class TestBuildContext:
    def test_minimal(self):
        ctx = _build_context("test item", "unknown", [], [], [], [])
        assert "test item" in ctx

    def test_with_product_type(self):
        ctx = _build_context("ВВГ-нг", "cables", [], [], [], [],
                             product_data={"name": "Кабели", "category": "cables"})
        assert "cables" in ctx

    def test_with_approaches(self):
        approaches = [{"site_id": "tinko.ru", "success_count": 5, "method": "search_then_navigate"}]
        ctx = _build_context("test", "cables", approaches, [], [], [],
                             product_data={"name": "cables"})
        assert "tinko.ru" in ctx
        assert "успехов=5" in ctx or "5" in ctx

    def test_with_confirmed_prices(self):
        prices = [{"spec_text": "ВВГ 3x1.5", "price": 1500.5, "site_id": "tinko.ru", "confidence": 0.95}]
        ctx = _build_context("test", "cables", [], prices, [], [],
                             product_data={"name": "cables"})
        assert "1500.5" in ctx or "1500" in ctx

    def test_with_sites(self):
        sites = [{"id": "tinko.ru"}, {"id": "keaz.ru"}]
        approaches = [{"site_id": "tinko.ru"}, {"site_id": "keaz.ru"}]
        ctx = _build_context("test", "cables", approaches, [], sites, [],
                             product_data={"name": "cables"})
        assert "tinko.ru" in ctx
        assert "keaz.ru" in ctx

    def test_hints_not_in_context(self):
        hints = [{"hint_text": "Искать в каталоге"}]
        ctx = _build_context("test", "cables", [], [], [], hints,
                             product_data={"name": "cables"})
        assert "Искать в каталоге" not in ctx

    def test_no_sites_message(self):
        ctx = _build_context("test", "unknown", [], [], [], [])
        assert "Yandex" in ctx or "известных сайтов нет" in ctx

    def test_use_site_ranking_false_ignores_success_approaches(self):
        """Рейтинг выкл: сайт с успешными подходами НЕ получает приоритет над белым списком."""
        sites = [{"id": "prio1.ru", "priority": 0}, {"id": "succ.ru", "priority": 2}]
        approaches = [{"site_id": "succ.ru", "success_count": 5}]
        ctx = _build_context("test", "cables", approaches, [], sites, [],
                             use_site_ranking=False)
        # succ.ru не поднимается выше prio1.ru: первым назван prio1.ru
        assert ctx.find("prio1.ru") < ctx.find("succ.ru")

    def test_use_site_ranking_true_keeps_success_priority(self):
        sites = [{"id": "prio1.ru", "priority": 0}, {"id": "succ.ru", "priority": 2}]
        approaches = [{"site_id": "succ.ru", "success_count": 5}]
        ctx = _build_context("test", "cables", approaches, [], sites, [],
                             use_site_ranking=True)
        assert ctx.find("succ.ru") < ctx.find("prio1.ru")

    def test_site_ranking_profile_beats_plain_priority(self):
        """Сайт из рейтинг-профиля идёт выше успешных подходов (но ниже цен)."""
        sites = [{"id": "succ.ru", "priority": 2}, {"id": "rank.ru", "priority": 2}]
        approaches = [{"site_id": "succ.ru", "success_count": 5}]
        ranking = {"rank.ru": 0.5}
        ctx = _build_context("test", "cables", approaches, [], sites, [],
                             use_site_ranking=True, site_ranking=ranking)
        assert ctx.find("rank.ru") < ctx.find("succ.ru")


class TestErrorResult:
    def test_returns_error_dict(self):
        r = _error_result("test spec", "something broke")
        assert r["spec_text"] == "test spec"
        assert r["price"] is None
        assert r["error"] == "something broke"
        assert r["requires_review"] is True


class TestApplyApproach:
    def test_replaces_param_slots(self):
        approach = {
            "concrete": [{"action": "browser_type", "text": "искать {product_name}", "target": "e1"}],
            "param_slots": {"product_name": {"type": "string"}},
        }
        adapted = _apply_approach(approach, "Кран Ду15")
        assert adapted["concrete"][0]["text"] == "искать Кран Ду15"
        assert adapted["search_query"] == "Кран Ду15"

    def test_scrubs_stale_text_in_type_steps(self):
        """Подход сохранён от ДРУГОГО товара: жёстко зашитый текст подменяется."""
        approach = {
            "concrete": [
                {"action": "browser_navigate", "url": "https://site.ru"},
                {"action": "browser_type", "text": "SRE-Е-2,5/STY-2,5", "target": "e84"},
                {"action": "browser_snapshot"},
            ],
        }
        adapted = _apply_approach(approach, "Воздуховод из оцинкованной стали Ø100")
        texts = [s.get("text") for s in adapted["concrete"]]
        assert "SRE-Е-2,5/STY-2,5" not in texts
        assert adapted["concrete"][1]["text"] == "Воздуховод из оцинкованной стали Ø100"

    def test_keeps_navigate_url(self):
        approach = {"concrete": [{"action": "browser_navigate", "url": "https://site.ru/catalog"}]}
        adapted = _apply_approach(approach, "Товар")
        assert adapted["concrete"][0]["url"] == "https://site.ru/catalog"


class TestPortableStepTarget:
    def test_css_target_kept(self):
        step = {"action": "browser_click", "target": ".search-btn", "element": "Кнопка поиска"}
        assert _portable_step_target(step) == ".search-btn"

    def test_role_locator_kept(self):
        step = {"action": "browser_click", "target": 'textbox "Поиск"'}
        assert _portable_step_target(step) == 'textbox "Поиск"'

    def test_hash_ref_replaced_by_element(self):
        """Playwright-ref e82 бэкенд-специфичен — вместо него показываем element."""
        step = {"action": "browser_click", "target": "e82", "element": 'textbox "Поиск по наименованию или коду товара"'}
        assert _portable_step_target(step) == 'textbox "Поиск по наименованию или коду товара"'

    def test_hash_ref_replaced_by_text(self):
        step = {"action": "browser_type", "target": "f2e81", "text": "Мембранный бак 100л"}
        assert _portable_step_target(step) == "Мембранный бак 100л"

    def test_empty_step(self):
        assert _portable_step_target({}) == ""


class TestFormatStepsPortable:
    def test_does_not_expose_hash_refs(self):
        """Подход, сохранённый под Playwright, не должен показывать LLM хеш-рефы
        (e82/f2e17), т.к. в Camoufox они не существуют."""
        concrete = [
            {"action": "browser_type", "target": "e82", "text": "Запрос", "element": 'textbox "Поиск"'},
            {"action": "browser_click", "target": "e83", "element": "Кнопка Найти"},
        ]
        rendered = format_steps(concrete)
        assert "e82" not in rendered
        assert "e83" not in rendered
        assert "Поиск" in rendered

    def test_detailed_prefers_portable_target(self):
        concrete = [{"action": "browser_click", "target": "e7", "element": "Кнопка ОК в cookie-баннере"}]
        rendered = format_steps_detailed(concrete)
        assert "e7" not in rendered
        assert "cookie-баннере" in rendered

    def test_detailed_keeps_url_and_text(self):
        concrete = [
            {"action": "browser_navigate", "url": "https://site.ru"},
            {"action": "browser_type", "text": "Товар"},
        ]
        rendered = format_steps_detailed(concrete)
        assert "https://site.ru" in rendered
        assert "text=Товар" in rendered


class TestStandardReference:
    def test_gost_filtered(self):
        assert _is_standard_reference("ГОСТ 14918-2020") is True

    def test_tu_filtered(self):
        assert _is_standard_reference("ТУ 36-1234") is True

    def test_model_not_filtered(self):
        assert _is_standard_reference("CC11 500x400") is False
        assert _is_standard_reference("Aerostar-100") is False
        assert _is_standard_reference("Осв 21-12 №6,3") is False

    def test_empty_not_filtered(self):
        assert _is_standard_reference("") is False


class TestStuckTarget:
    def test_click_keeps_target(self):
        assert _stuck_target("browser_click", {"target": "e123"}) == "e123"

    def test_navigate_uses_url(self):
        assert _stuck_target("browser_navigate", {"url": "https://x.ru"}) == "https://x.ru"

    def test_evaluate_without_function(self):
        assert _stuck_target("browser_evaluate", {}).startswith("js:")

    def test_evaluate_distinguishes_scripts(self):
        t1 = _stuck_target("browser_evaluate", {"function": "() => { return 1 }"})
        t2 = _stuck_target("browser_evaluate", {"function": "() => { return 2 }"})
        assert t1 != t2

    def test_evaluate_identical_scripts_same_signature(self):
        f = "() => document.title"
        assert _stuck_target("browser_evaluate", {"function": f}) == _stuck_target("browser_evaluate", {"function": f})

    def test_three_different_evaluates_not_stuck(self):
        from src.stuck_detector import StuckDetector, StuckLevel
        d = StuckDetector(repeat_threshold=3)
        for script in ("() => 1", "() => 2", "() => 3"):
            d.record_action("browser_evaluate", _stuck_target("browser_evaluate", {"function": script}), "success")
        assert d.detect() == StuckLevel.OK

    def test_three_identical_evaluates_critical(self):
        from src.stuck_detector import StuckDetector, StuckLevel
        d = StuckDetector(repeat_threshold=3)
        for _ in range(3):
            d.record_action("browser_evaluate", _stuck_target("browser_evaluate", {"function": "() => 1"}), "success")
        assert d.detect() == StuckLevel.CRITICAL


class TestProductCardUrl:
    def test_santech_catalog(self):
        assert _is_product_card_url("https://www.santech.ru/catalog/293/306/i46584/v155997/") is True

    def test_home_page(self):
        assert _is_product_card_url("https://www.santech.ru/") is False

    def test_search_results(self):
        assert _is_product_card_url("https://site.ru/search?text=клапан") is False

    def test_query_id(self):
        assert _is_product_card_url("https://site.ru/item.html?id=123") is True

    def test_empty(self):
        assert _is_product_card_url("") is False


class TestPriceCandidate:
    def test_cyrillic_ruble(self):
        assert _extract_price_candidate("Цена: 7 201,30 Р") == "7 201,30 Р"

    def test_latin_p(self):
        assert _extract_price_candidate("2 570,50 P") == "2 570,50 P"

    def test_rub_word(self):
        assert _extract_price_candidate("1 200 руб за шт") == "1 200 руб"

    def test_pressure_rating_not_price(self):
        assert _extract_price_candidate("Клапан Ду15 Ру16") is None

    def test_none(self):
        assert _extract_price_candidate(None) is None

    def test_no_currency(self):
        assert _extract_price_candidate("артикул 46584") is None


class TestFamilyPage:
    def test_santech_family_without_variant(self):
        assert _is_family_page("https://www.santech.ru/catalog/337/340/i1322/") is True

    def test_variant_card_not_family(self):
        assert _is_family_page("https://www.santech.ru/catalog/337/340/i1322/v6/") is False

    def test_variant_long_slug_not_family(self):
        assert _is_family_page("https://www.santech.ru/catalog/293/306/i46584/v155997/") is False

    def test_ridan_product_not_family(self):
        assert _is_family_page("https://ridan.ru/product/065N9548GR") is False

    def test_dn_url_not_family(self):
        assert _is_family_page("https://dn.ru/sharovyi-kran/teplosnabzhenie/flantcevyi-kran/") is False

    def test_empty(self):
        assert _is_family_page("") is False

    def test_trailing_slash_variants(self):
        assert _is_family_page("https://www.santech.ru/catalog/259/261/i1112/") is True
        assert _is_family_page("https://www.santech.ru/catalog/259/261/i1112") is True


class TestEmptySearchResult:
    def test_empty_array(self):
        assert _is_empty_search_result("browser_evaluate", "[]") is True

    def test_empty_object(self):
        assert _is_empty_search_result("browser_evaluate", "{}") is True

    def test_null(self):
        assert _is_empty_search_result("browser_evaluate", "null") is True

    def test_no_matches_find(self):
        assert _is_empty_search_result("browser_find", 'No matches found for "клапан".') is True

    def test_empty_string(self):
        assert _is_empty_search_result("browser_evaluate", "") is True

    def test_price_not_empty(self):
        assert _is_empty_search_result("browser_evaluate", "7 201,30 Р") is False

    def test_snapshot_never_empty_probe(self):
        assert _is_empty_search_result("browser_snapshot", "[]") is False

    def test_error_not_empty(self):
        assert _is_empty_search_result("browser_evaluate", "error: timeout") is False

    def test_real_price_block(self):
        assert _is_empty_search_result("browser_find", 'Found 118 matches for "клапан":') is False


class TestDiagnosticMessage:
    def test_mentions_card_and_candidate(self):
        msg = _build_diagnostic_message(
            "клапан Ду15", "https://x.ru/catalog/1/i2/",
            card_open=True, price_candidate_seen=True,
            recent_errors=["error: SyntaxError: Unexpected end of input"],
        )
        assert "Карточка товара ОТКРЫТА" in msg
        assert "price_candidate" in msg
        assert "SyntaxError" in msg
        assert "ПРОАНАЛИЗИРУЙ" in msg

    def test_switch_advice_when_no_card(self):
        msg = _build_diagnostic_message("клапан", "", card_open=False, price_candidate_seen=False)
        assert "Карточка товара НЕ открыта" in msg

    def test_no_errors_omits_errors_section(self):
        msg = _build_diagnostic_message("клапан", "", recent_errors=[])
        assert "Последние ошибки" not in msg


class TestExecuteGraphTool:
    def test_unknown_tool(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("nonexistent_tool", {}, graph_engine, mm)
        assert "error" in result

    def test_get_approaches_empty(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("get_approaches", {"product_type": "cables"}, graph_engine, mm)
        assert "Нет" in result

    def test_search_sites_empty(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("search_sites", {"product_type": "cables"}, graph_engine, mm)
        assert "Нет" in result

    def test_save_discovered_site(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("save_discovered_site", {
            "domain": "new-site.ru", "name": "New Site", "product_type": "cables"
        }, graph_engine, mm)
        assert "сохранён" in result.lower()
        sites = mm.get_sites("cables")
        assert any(s["id"] == "new-site.ru" for s in sites)

    def test_save_confirmed_price_family_page_rejected(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("save_confirmed_price", {
            "product_name": "Клапан балансировочный автомат латунь APT-R Ду15",
            "price": 15676.8, "confidence": 0.95,
            "url": "https://www.santech.ru/catalog/337/340/i1322/",
            "site": "santech.ru",
        }, graph_engine, mm, spec_text="Клапан балансировочный авт. Ду15")
        assert result.startswith("error:")
        assert "семейная страница" in result
        prices = mm.get_relevant_prices("Клапан балансировочный авт. Ду15", 10)
        assert not any("i1322" in (p.get("url") or "") for p in prices)

    def test_save_confirmed_price_variant_card_accepted(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("save_confirmed_price", {
            "product_name": "Клапан балансировочный автомат латунь APT-R Ду15",
            "price": 15676.8, "confidence": 0.95,
            "url": "https://www.santech.ru/catalog/337/340/i1322/v6/",
            "site": "santech.ru",
        }, graph_engine, mm, spec_text="Клапан балансировочный авт. Ду15")
        assert "обрабатываются системой" in result


class TestMismatchWarningContent:
    def test_reports_missing_word(self):
        msg = _mismatch_warning_content(
            "Компенсатор сильфонный под приварку Ду40",
            "Компенсатор сильфонный осевой многослойный б/кожух",
        )
        assert "приварку" in msg
        assert "СОВЕТ" in msg
        assert "confirm=true" in msg
        assert not msg.startswith("error:")

    def test_full_title_no_missing_but_still_warns(self):
        msg = _mismatch_warning_content(
            "Компенсатор сильфонный под приварку Ду40",
            "Компенсатор сильфонный осевой многослойный с кожухом под приварку Ду 40",
        )
        assert "СОВЕТ" in msg
        assert "h1" in msg

    def test_descriptive_words_separated_from_key(self):
        """Система-советник НЕ решает за LLM: описательные слова (серия/комплектация)
        выделяются отдельно от ключевых, решение остаётся за LLM."""
        msg = _mismatch_warning_content(
            "Стальной панельный радиатор с боковым подключением LEMAX Premium Compact Hygiene, "
            "тип C10, в компл. с краном для выпуска воздуха и креплениями LEMAX Premium C10 500x600",
            "Стальной панельный радиатор Лемакс Premium C 10х500х600",
        )
        assert "КЛЮЧЕВЫЕ слова" in msg or "Описательные/комплектационные" in msg
        assert "Перепроверь НА КАРТОЧКЕ" in msg
        assert "confirm=true" in msg

    def test_graph_tool_save_confirmed_price_is_passthrough(self, graph_engine):
        """Решение по save_confirmed_price принимает инлайн-обработчик, graph-tool — пассивный статус."""
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("save_confirmed_price", {
            "product_name": "Компенсатор сильфонный осевой б/кожух",
            "price": 5088.5, "confidence": 0.6,
            "url": "https://www.santech.ru/catalog/293/306/i256/v1/",
            "site": "santech.ru",
            "brand_mismatch": True,
        }, graph_engine, mm, spec_text="Компенсатор сильфонный под приварку Ду20")
        assert "обрабатываются системой" in result
        assert "принят" not in result

    def test_graph_tool_family_page_still_rejected(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("save_confirmed_price", {
            "product_name": "Клапан балансировочный Ду15",
            "price": 100.0, "confidence": 0.9,
            "url": "https://www.santech.ru/catalog/337/340/i1322/",
            "site": "santech.ru",
        }, graph_engine, mm, spec_text="Клапан балансировочный авт. Ду15")
        assert "семейная страница" in result


class TestResultToSchema:
    def test_success_result(self):
        result = {
            "spec_text": "ВВГ 3x1.5", "product_type": "cables",
            "price": 1500.5, "confidence": 0.95, "url": "https://tinko.ru",
            "site": "tinko.ru", "reason": "", "requires_review": False,
            "elapsed": 12.3,
        }
        out = _result_to_schema(result)
        assert out["spec_text"] == "ВВГ 3x1.5"
        assert out["found"] is True
        assert out["price"] == 1500.5
        assert out["requires_review"] is False
        assert out["product_type"] == "cables"
        assert out["brand_mismatch"] is False

    def test_no_price_result(self):
        result = {
            "spec_text": "Кабель", "price": None, "confidence": 0.0,
            "requires_review": True, "error": "Max rounds reached", "elapsed": 30.0,
        }
        out = _result_to_schema(result)
        assert out["found"] is False
        assert out["price"] is None
        assert out["error"] == "Max rounds reached"

    def test_returns_original_on_invalid(self):
        result = {"spec_text": "", "price": -5, "requires_review": True}
        out = _result_to_schema(result)
        assert out is result


class TestFallbackResult:
    def test_pick_best_fallback_empty(self):
        assert _pick_best_fallback([]) is None

    def test_pick_best_fallback_confidence(self):
        candidates = [
            {"price": 100.0, "confidence": 0.4, "product_name": "A"},
            {"price": 200.0, "confidence": 0.6, "product_name": "B"},
        ]
        assert _pick_best_fallback(candidates)["product_name"] == "B"

    def test_fallback_result_marked(self):
        result = _fallback_result(
            "Клапан балансировочный авт. фланцевый Ду100",
            "unknown",
            [{
                "price": 328106.6, "confidence": 0.7,
                "url": "https://www.santech.ru/catalog/337/340/i867/v3/",
                "site": "santech.ru",
                "product_name": "Клапан балансировочный автомат чугун Giacomini R206CY310",
            }],
            elapsed=120.0,
        )
        assert result is not None
        assert result["price"] == 328106.6
        assert result["brand_mismatch"] is True
        assert result["requires_review"] is True
        assert result["confidence"] == 0.5
        assert "не совпадает бренд" in result["reason"]
        assert result["error"] is None

    def test_fallback_result_empty_candidates(self):
        assert _fallback_result("spec", "unknown", [], elapsed=1.0) is None

    def test_fallback_result_keeps_product_type(self):
        result = _fallback_result("spec", "cables", [
            {"price": 50.0, "confidence": 0.3, "url": "http://x", "site": "x.ru", "product_name": "X"},
        ], elapsed=5.0)
        assert result["product_type"] == "cables"
        assert result["site"] == "x.ru"


class TestPrecisionDeprecation:
    class FakeMM:
        def __init__(self):
            self.failed = []
            self.deprecated_all = []

        def record_failure(self, approach_id):
            self.failed.append(approach_id)

        def get_site_approaches(self, product_type, domain):
            return [{"id": 10}, {"id": 11}]

        def get_approaches_by_site(self, domain):
            return [{"id": 10}, {"id": 11}]

    def test_penalize_only_passed_ids(self):
        mm = self.FakeMM()
        _penalize_approaches(mm, [1, 2, 3], "test:")
        assert mm.failed == [1, 2, 3]

    def test_penalize_empty_noop(self):
        mm = self.FakeMM()
        _penalize_approaches(mm, [], "test:")
        _penalize_approaches(mm, None, "test:")
        assert mm.failed == []

    def test_penalize_dedup_and_skip_invalid(self):
        mm = self.FakeMM()
        _penalize_approaches(mm, [1, "2", 1, None, "x"], "test:")
        assert mm.failed == [1, 2]

    def test_deprecate_with_ids_routes_to_penalize(self):
        mm = self.FakeMM()
        _deprecate_site_approaches(mm, "pt", "site.ru", "test:", approach_ids=[5, 6])
        assert mm.failed == [5, 6]

    def test_deprecate_without_ids_falls_back_to_all(self):
        mm = self.FakeMM()
        _deprecate_site_approaches(mm, "pt", "site.ru", "test:")
        assert mm.failed == [10, 11]


class TestFactsBlock:
    def test_inject_empty_block_noop(self):
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        assert _inject_facts_block(messages, "") == messages

    def test_inject_after_system(self):
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        out = _inject_facts_block(messages, "ФАКТЫ")
        assert out[0] == {"role": "system", "content": "s"}
        assert out[1] == {"role": "user", "content": "ФАКТЫ"}
        assert out[2] == {"role": "user", "content": "u"}

    def test_facts_survive_trim_and_reinject(self):
        # имитация: огромная история обрезается trim, затем факты вставляются свежими
        messages = [{"role": "system", "content": "sys"}]
        for i in range(200):
            messages.append({"role": "assistant", "content": "x" * 2000})
            messages.append({"role": "tool", "content": "y" * 2000})
        messages.append({"role": "user", "content": "финал"})
        trimmed = _trim_messages_for_budget(messages, budget=2000)
        facts = RowFacts()
        facts.record_site_visit("satro-paladin.com")
        out = _inject_facts_block(trimmed, facts.to_prompt_block())
        assert any("ФАКТЫ СЕССИИ" in m.get("content", "") for m in out)

    def test_facts_block_replaces_not_accumulates(self):
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        out1 = _inject_facts_block(messages, "ФАКТЫ v1")
        # следующий вызов строит из того же исходного списка (локальная вставка)
        out2 = _inject_facts_block(messages, "ФАКТЫ v2")
        assert sum(1 for m in out1 if m.get("content") == "ФАКТЫ v1") == 1
        assert sum(1 for m in out2 if m.get("content") == "ФАКТЫ v2") == 1
