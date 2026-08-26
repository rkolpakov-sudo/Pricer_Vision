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


class SiteBlacklist:
    """Сессионный блэклист сайтов.

    Если агент несколько раз (лимит) безуспешно искал товар данного типа/бренда
    на сайте (таймаут, force-switch, max rounds) — сайт исключается из поиска
    на оставшийся прогон. Память живёт только в текущей сессии (объект в runner).

    Это решает проблему «каждая строка заново открывает, что на santech.ru нет
    радиаторов LEMAX и тратит 8–12 раундов впустую»: вторая строка уже не
    получит santech.ru в списке сайтов.
    """

    MAX_STRIKES = 2

    def __init__(self, limit: int | None = None):
        self._limit = limit or self.MAX_STRIKES
        self._strikes: dict[str, int] = {}

    @property
    def limit(self) -> int:
        return self._limit

    @staticmethod
    def _normalize(site_id: str) -> str:
        key = (site_id or "").strip().lower().rstrip("/")
        for prefix in ("https://", "http://", "www."):
            if key.startswith(prefix):
                key = key[len(prefix):]
        return key

    def strike(self, site_id: str) -> int:
        """Зафиксировать неудачу на сайте. Возвращает новый счётчик."""
        key = self._normalize(site_id)
        if not key:
            return 0
        count = self._strikes.get(key, 0) + 1
        self._strikes[key] = count
        return count

    def is_blocked(self, site_id: str) -> bool:
        key = self._normalize(site_id)
        if not key:
            return False
        return self._strikes.get(key, 0) >= self._limit

    def blocked_sites(self) -> set[str]:
        return {s for s, c in self._strikes.items() if c >= self._limit}

    def count(self, site_id: str) -> int:
        return self._strikes.get(self._normalize(site_id), 0)

    def reset(self) -> None:
        self._strikes.clear()

    def __len__(self) -> int:
        return len(self._strikes)
