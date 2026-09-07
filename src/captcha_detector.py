from enum import Enum


class CaptchaType(Enum):
    NONE = "none"
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    CLOUDFLARE = "cloudflare"
    IMAGE = "image"
    UNKNOWN = "unknown"


DETECTION_MARKERS = {
    CaptchaType.RECAPTCHA_V2: [
        "g-recaptcha",
        "recaptcha/api2",
        "recaptcha anchor",
        "recaptcha/anchor",
    ],
    CaptchaType.RECAPTCHA_V3: [
        "grecaptcha",
        "recaptcha/api.js",
    ],
    CaptchaType.HCAPTCHA: [
        "h-captcha",
        "hcaptcha",
        "iframe[src*='hcaptcha']",
    ],
    CaptchaType.CLOUDFLARE: [
        "cf-turnstile",
        "challenge-form",
        "challenges.cloudflare.com",
        "ddos-guard",
        "hcheck",
    ],
    CaptchaType.IMAGE: [
        "captcha-image",
        "captcha.png",
        "captcha.jpg",
        "captcha.jpeg",
        "captcha img",
    ],
}

CAPTCHA_KEYWORDS = [
    "captcha",
    "verify you are human",
    "i'm not a robot",
    "подтвердите",
    "are you human",
    "turnstile",
    "js-check",
]


class CaptchaDetector:
    """Обнаруживает CAPTCHA и рекомендует действие.

    В v2.0 НЕ решает CAPTCHA автоматически — только детектирует.
    """

    @staticmethod
    def detect(page_snapshot: str) -> CaptchaType:
        snapshot_lower = (page_snapshot or "").lower()

        for captcha_type, markers in DETECTION_MARKERS.items():
            for marker in markers:
                if marker.lower() in snapshot_lower:
                    return captcha_type

        if any(kw in snapshot_lower for kw in CAPTCHA_KEYWORDS):
            return CaptchaType.UNKNOWN

        return CaptchaType.NONE

    @staticmethod
    def get_recommendation(captcha_type: CaptchaType) -> str:
        recommendations = {
            CaptchaType.NONE: "PROCEED",
            CaptchaType.RECAPTCHA_V2: "SWITCH_SITE",
            CaptchaType.RECAPTCHA_V3: "SWITCH_SITE",
            CaptchaType.HCAPTCHA: "SWITCH_SITE",
            # Cloudflare challenge НЕ решается ожиданием: это интерактивная
            # проверка. Ждать 30-60с на странице бесполезно (тратит раунды и
            # время) — нужно уйти на другой сайт. Регрессия: рекомендация
            # WAIT_60S_AND_RETRY заставляла агента вызывать browser_wait_for
            # на 30с+ и «висеть» на заблокированном сайте.
            CaptchaType.CLOUDFLARE: "SWITCH_SITE",
            CaptchaType.IMAGE: "ASK_USER",
            CaptchaType.UNKNOWN: "SWITCH_SITE",
        }
        return recommendations.get(captcha_type, "SWITCH_SITE")
