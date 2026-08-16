import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class StuckLevel(Enum):
    OK = "ok"
    WARNING = "warning"       # Повторяющиеся действия
    CRITICAL = "critical"     # Полный тупик
    BLOCKED = "blocked"       # Обнаружена блокировка


@dataclass
class ActionRecord:
    action_type: str
    target: str
    result: str  # "success", "error", "no_change"
    timestamp: float


class StuckDetector:
    def __init__(self, window_size: int = 5, repeat_threshold: int = 3):
        self.history = deque(maxlen=window_size)
        self.repeat_threshold = repeat_threshold
        self.no_progress_count = 0

    def record_action(self, action_type: str, target: str, result: str):
        self.history.append(ActionRecord(
            action_type=action_type,
            target=target,
            result=result,
            timestamp=time.time()
        ))

        if result == "no_change":
            self.no_progress_count += 1
        else:
            self.no_progress_count = 0

    def detect(self) -> StuckLevel:
        if len(self.history) < self.repeat_threshold:
            return StuckLevel.OK

        # Проверка на блокировку (CAPTCHA, 403, Cloudflare)
        if self._detect_block():
            return StuckLevel.BLOCKED

        # Проверка на циклические действия
        recent = list(self.history)[-self.repeat_threshold:]
        action_signatures = [f"{a.action_type}:{a.target}" for a in recent]

        if len(set(action_signatures)) == 1:
            return StuckLevel.CRITICAL

        # Проверка на отсутствие прогресса
        if self.no_progress_count >= self.repeat_threshold:
            return StuckLevel.WARNING

        return StuckLevel.OK

    def reset(self):
        self.history.clear()
        self.no_progress_count = 0

    def _detect_block(self) -> bool:
        """Обнаружение блокировки сайта"""
        block_indicators = [
            "captcha", "verify", "blocked", "access denied",
            "403", "cloudflare", "attention required"
        ]
        for record in self.history:
            if any(ind in record.result.lower() for ind in block_indicators):
                return True
        return False

    def suggest_recovery(self, level: StuckLevel) -> List[str]:
        """Предложения по выходу из тупика"""
        strategies = {
            StuckLevel.WARNING: [
                "REFRESH_PAGE",
                "TRY_ALTERNATIVE_SELECTOR",
                "SCROLL_AND_RETRY"
            ],
            StuckLevel.CRITICAL: [
                "SWITCH_SITE",
                "ASK_USER_HINT",
                "SKIP_PRODUCT"
            ],
            StuckLevel.BLOCKED: [
                "WAIT_AND_RETRY",      # Подождать 30-60 сек
                "SWITCH_SITE",         # Немедленно сменить сайт
                "REPORT_BLOCK"         # Записать в граф как блокировку
            ]
        }
        return strategies.get(level, [])