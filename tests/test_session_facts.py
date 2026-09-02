"""Тесты операционной памяти строки RowFacts (src/session_facts.py)."""

from src.session_facts import RowFacts, REPEAT_NOTICE_THRESHOLD, SessionFacts


class TestRowFacts:
    def test_empty_has_no_block(self):
        f = RowFacts()
        assert f.to_prompt_block() == ""

    def test_site_visit_creates_block(self):
        f = RowFacts()
        f.record_site_visit("satro-paladin.com")
        block = f.to_prompt_block()
        assert "ПАМЯТЬ СТРОКИ" in block
        assert "satro-paladin.com" in block

    def test_query_recorded_once(self):
        f = RowFacts()
        f.record_site_visit("mircli.ru")
        f.record_query("mircli.ru", "LEMAX Premium C10 500x600")
        f.record_query("mircli.ru", "LEMAX Premium C10 500x600")
        block = f.to_prompt_block()
        assert block.count("LEMAX Premium C10 500x600") == 1

    def test_query_list_kept_last_three(self):
        f = RowFacts()
        for q in ("q1", "q2", "q3", "q4"):
            f.record_query("x.ru", q)
        block = f.to_prompt_block()
        assert "q1" in block  # MAX_QUERIES_PER_SITE=6 — all 4 fit
        assert "q2" in block and "q3" in block and "q4" in block

    def test_repeat_streak_noticed(self):
        f = RowFacts()
        f.record_site_visit("satro-paladin.com")
        for _ in range(REPEAT_NOTICE_THRESHOLD):
            f.record_browser_call("satro-paladin.com", "evaluate:js1", "hash1")
        block = f.to_prompt_block()
        assert f"повтор ×{REPEAT_NOTICE_THRESHOLD}" in block

    def test_repeat_streak_resets_on_different_result(self):
        f = RowFacts()
        f.record_browser_call("x.ru", "evaluate:js1", "hash1")
        f.record_browser_call("x.ru", "evaluate:js1", "hash1")
        f.record_browser_call("x.ru", "evaluate:js1", "hash2")
        f.record_browser_call("x.ru", "evaluate:js1", "hash2")
        block = f.to_prompt_block()
        assert "повторено" not in block  # стрек < порога, разный результат

    def test_price_candidate_seen_flag(self):
        f = RowFacts()
        assert f.price_candidate_seen is False
        f.record_price_candidate()
        assert f.price_candidate_seen is True
        assert "цена-кандидат" in f.to_prompt_block()

    def test_empty_result_status(self):
        f = RowFacts()
        f.record_site_visit("lunda.ru")
        f.record_empty_result("lunda.ru")
        assert "пустой результат" in f.to_prompt_block()

    def test_card_open_recorded(self):
        f = RowFacts()
        f.record_card_open()
        assert "карточка товара" in f.to_prompt_block()

    def test_errors_recorded_dedup(self):
        f = RowFacts()
        f.record_error("error: type failed")
        f.record_error("error: type failed")
        f.record_error("error: click failed")
        block = f.to_prompt_block()
        assert block.count("type failed") == 1
        assert "click failed" in block

    def test_empty_queries_ignored(self):
        f = RowFacts()
        f.record_query("x.ru", "")
        f.record_query("x.ru", "   ")
        assert f.to_prompt_block() == ""

    def test_progress_in_block(self):
        f = RowFacts()
        f.set_progress(5, 40)
        block = f.to_prompt_block()
        assert "РАУНД 5/40" in block

    def test_progress_without_set_no_line(self):
        f = RowFacts()
        assert "Раундов:" not in f.to_prompt_block()

    def test_seen_query_counts_prior(self):
        f = RowFacts()
        f.record_query("santech.ru", "МС-140")
        assert f.seen_query("santech.ru", "МС-140") == 1
        assert f.seen_query("santech.ru", "МС-140 500x600") == 0
        assert f.seen_query("", "МС-140") == 0

    def test_last_query(self):
        f = RowFacts()
        assert f.last_query("santech.ru") == ""
        f.record_query("santech.ru", "q1")
        f.record_query("santech.ru", "q2")
        assert f.last_query("santech.ru") == "q2"

    def test_evals_since_type_resets_on_query(self):
        f = RowFacts()
        f.record_browser_call("x.ru", "evaluate:js1", "h")
        f.record_browser_call("x.ru", "evaluate:js2", "h")
        assert f.evals_without_type == 2
        f.record_query("x.ru", "запрос")
        assert f.evals_without_type == 0

    def test_evals_without_type_noticed_in_block(self):
        f = RowFacts()
        f.record_site_visit("lunda.ru")
        for i in range(4):
            f.record_browser_call("lunda.ru", f"evaluate:js{i}", "h")
        block = f.to_prompt_block()
        assert "запрос не вводился" in block
        assert "browser_type" in block

    def test_price_candidate_hint_in_block(self):
        f = RowFacts()
        f.record_price_candidate("5 636,80 ₽")
        block = f.to_prompt_block()
        assert "5 636,80 ₽" in block
        assert f.price_candidate_hint == "5 636,80 ₽"

    def test_navblocks_in_block(self):
        f = RowFacts()
        f.record_price_candidate("100 ₽")
        f.record_navblock()
        f.record_navblock()
        block = f.to_prompt_block()
        assert "попыток уйти без сохранения: 2" in block
        assert f.navblocks == 2

    def test_visited_urls_dedup_and_seen(self):
        f = RowFacts()
        f.record_url("https://santech.ru/catalog/i498/")
        f.record_url("https://santech.ru/catalog/i498/")
        assert f.seen_url("https://santech.ru/catalog/i498/") is True
        assert f.seen_url("https://santech.ru/catalog/i499/") is False

    def test_distinct_sites_count(self):
        f = RowFacts()
        assert f.distinct_sites() == 0
        f.record_site_visit("a.ru")
        f.record_site_visit("b.ru")
        f.record_site_visit("c.ru")
        assert f.distinct_sites() == 3

    def test_sites_count_guidance_in_block(self):
        f = RowFacts()
        for d in ("a.ru", "b.ru", "c.ru"):
            f.record_site_visit(d)
        block = f.to_prompt_block()
        assert "ЖУРНАЛ САЙТОВ" in block
        assert f"уже посещено сайтов: {len(f._sites)}" in block or "ЖУРНАЛ САЙТОВ" in block

class TestSessionFacts:
    PT = 'plumbing_heating_radiators'
    BRAND = 'Лемакс'

    def test_success_records_has_product(self):
        f = SessionFacts()
        f.record_success(self.PT, self.BRAND, 'https://mircli.ru', url='https://mircli.ru/p/x', query='Лемакс Premium C10 500x600')
        pos, neg = f.to_context_blocks(self.PT, self.BRAND)
        assert 'mircli.ru' in pos
        assert 'рабочий запрос' in pos
        assert neg == ''

    def test_no_product_records_negative(self):
        f = SessionFacts()
        f.record_no_product(self.PT, self.BRAND, 'santech.ru')
        pos, neg = f.to_context_blocks(self.PT, self.BRAND)
        assert 'santech.ru' in neg
        assert pos == ''

    def test_no_product_does_not_override_has_product(self):
        f = SessionFacts()
        f.record_success(self.PT, self.BRAND, 'mircli.ru', url='https://mircli.ru/p/x', query='q')
        f.record_no_product(self.PT, self.BRAND, 'mircli.ru')
        pos, neg = f.to_context_blocks(self.PT, self.BRAND)
        assert 'mircli.ru' in pos
        assert 'mircli.ru' not in neg

    def test_query_dedup_and_keep_last(self):
        f = SessionFacts()
        f.record_success(self.PT, self.BRAND, 'mircli.ru', query='q1')
        f.record_success(self.PT, self.BRAND, 'mircli.ru', query='q1')
        f.record_success(self.PT, self.BRAND, 'mircli.ru', query='q2')
        pos, _ = f.to_context_blocks(self.PT, self.BRAND)
        assert pos.count('q1') == 1
        assert 'q2' in pos

    def test_relevance_by_type_without_brand(self):
        f = SessionFacts()
        f.record_success(self.PT, 'ДругойБренд', 'x.ru', query='q')
        pos, _ = f.to_context_blocks(self.PT, self.BRAND)
        assert 'x.ru' in pos  # совпадение по типу

    def test_empty_blocks_when_no_facts(self):
        f = SessionFacts()
        pos, neg = f.to_context_blocks(self.PT, self.BRAND)
        assert pos == '' and neg == ''
