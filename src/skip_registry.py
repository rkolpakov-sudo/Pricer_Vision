"""Пропуск позиций по отметке пользователя в предпросмотре.

Qt-free модуль (тестируется без QApplication). Пользователь отмечает позицию
в предпросмотре как «пропустить»; в рамках той же сессии пропускаются и все
ПОЛНОСТЬЮ АНАЛОГИЧНЫЕ позиции — те, что точно совпадают по наименованию+бренду,
либо описывают тот же товар по существующему матчеру
approach_relevance.product_name_matches (тип + размер + бренд) в обе стороны.

Кэш живёт только в памяти и исчезает вместе с сессией (как NegativeCache).
"""

from src.approach_relevance import _product_tokens, product_name_matches


def _full_analog(a: str, b: str) -> bool:
    """True, если a и b — полностью аналогичные описания одного товара."""
    if not _product_tokens(a) or not _product_tokens(b):
        return False
    return product_name_matches(a, b) and product_name_matches(b, a)


class SkipRegistry:
    """Множество помеченных пользователем позиций + транзитивные аналоги.

    mark() — отмечает позицию и все её полные аналоги.
    unmark() — если это ОСНОВНАЯ позиция (из _marked), удаляет всю группу;
    если это АНАЛОГ (авто-подхваченный), снимает отметку только с него,
    не затрагивая группу.
    _excluded — индивидуально отключённые строки (не путать с основной).
    """

    def __init__(self):
        self._marked: list[dict] = []
        self._tokens: dict[str, set] = {}
        self._excluded: set[str] = set()

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join((text or "").lower().split())

    @staticmethod
    def _key(text: str, brand: str) -> str:
        return SkipRegistry._normalize(f"{text} {brand}")

    @staticmethod
    def _display(text: str, brand: str) -> str:
        return f"{(text or '').strip()} {(brand or '').strip()}".strip()

    def mark(self, text: str, brand: str = "") -> None:
        text = (text or "").strip()
        if not text:
            return
        key = self._key(text, brand)
        # Если строка была в исключённых — возвращаем её в группу
        if key in self._excluded:
            self._excluded.discard(key)
            return
        if any(m["key"] == key for m in self._marked):
            return
        # Если уже есть primary, покрывающий этот аналог — не дублируем
        if self.matches(text, brand) is not None:
            return
        display = self._display(text, brand)
        self._marked.append({"key": key, "text": text, "brand": (brand or "").strip()})
        self._tokens[key] = _product_tokens(display)

    def unmark(self, text: str, brand: str = "") -> None:
        key = self._key(text, brand)
        if not key:
            return
        if any(m["key"] == key for m in self._marked):
            # Основная позиция — удаляем всю группу, чистим исключения
            self._marked = [m for m in self._marked if m["key"] != key]
            self._tokens.pop(key, None)
            self._excluded.clear()
        else:
            # Аналог — снимаем отметку только с него, группа остаётся
            self._excluded.add(key)

    def matches(self, text: str, brand: str = "") -> str | None:
        """Описание помеченного товара, аналогом которого является (text, brand)."""
        key = self._key(text, brand)
        if not key:
            return None
        if key in self._excluded:
            return None
        a_display = self._display(text, brand)
        a_tokens = _product_tokens(a_display)
        for m in self._marked:
            if m["key"] == key:
                return self._display(m["text"], m["brand"])
            m_tokens = self._tokens.get(m["key"])
            if m_tokens and a_tokens and not (a_tokens & m_tokens):
                continue
            if _full_analog(a_display, self._display(m["text"], m["brand"])):
                return self._display(m["text"], m["brand"])
        return None

    def is_skipped(self, text: str, brand: str = "") -> bool:
        return self.matches(text, brand) is not None

    def blocked_count(self) -> int:
        return len(self._marked)

    def reset(self) -> None:
        self._marked.clear()
        self._tokens.clear()
        self._excluded.clear()

    def __len__(self) -> int:
        return len(self._marked)
