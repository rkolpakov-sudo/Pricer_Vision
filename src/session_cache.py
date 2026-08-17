"""Сессионный отрицательный кэш «не найденных» товаров.

Qt-free модуль — тестируется без QApplication.

Механизм: если агент не нашёл цену на товар (spec_text) дважды в ходе одной
сессии, товар помечается «не найден»; все последующие вхождения этого товара
в той же сессии пропускаются сразу, без поиска. Кэш существует только в памяти
и уничтожается вместе с сессией (в БД ничего не пишется).
"""


class NegativeCache:
    """Помечает товары, не найденные в ходе текущей сессии.

    - record(spec_text): учитывает очередную неудачу поиска товара.
    - is_blocked(spec_text): True, если товар не найден дважды (>= NOT_FOUND_LIMIT).
    - Ключ — нормализованный spec_text (lowercase, обрезка, схлопывание пробелов).
    - Сброс выполняется созданием нового экземпляра (сессия = жизнь объекта).
    """

    NOT_FOUND_LIMIT = 2

    def __init__(self, limit: int | None = None):
        self._limit = limit or self.NOT_FOUND_LIMIT
        self._counts: dict[str, int] = {}

    @staticmethod
    def _normalize(spec_text: str) -> str:
        return " ".join((spec_text or "").lower().split())

    def record(self, spec_text: str) -> int:
        """Учесть неудачный поиск товара. Возвращает новый счётчик."""
        key = self._normalize(spec_text)
        if not key:
            return 0
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        return count

    def is_blocked(self, spec_text: str) -> bool:
        """True, если товар не найден достаточно раз и его можно пропустить."""
        key = self._normalize(spec_text)
        if not key:
            return False
        return self._counts.get(key, 0) >= self._limit

    def count(self, spec_text: str) -> int:
        key = self._normalize(spec_text)
        return self._counts.get(key, 0)

    def blocked_count(self) -> int:
        """Сколько товаров помечено «не найден» в текущей сессии."""
        return sum(1 for c in self._counts.values() if c >= self._limit)

    def reset(self) -> None:
        self._counts.clear()

    def __len__(self) -> int:
        return len(self._counts)
