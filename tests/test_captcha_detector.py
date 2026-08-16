import pytest

from src.captcha_detector import CaptchaDetector, CaptchaType


def test_no_captcha():
    assert CaptchaDetector.detect("<html>normal page</html>") == CaptchaType.NONE


def test_recaptcha_v2_iframe():
    snap = '<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>'
    assert CaptchaDetector.detect(snap) == CaptchaType.RECAPTCHA_V2


def test_recaptcha_v2_class():
    assert CaptchaDetector.detect('<div class="g-recaptcha"></div>') == CaptchaType.RECAPTCHA_V2


def test_recaptcha_v3_api():
    assert CaptchaDetector.detect('grecaptcha') == CaptchaType.RECAPTCHA_V3


def test_hcaptcha():
    assert CaptchaDetector.detect('<div class="h-captcha"></div>') == CaptchaType.HCAPTCHA


def test_cloudflare_turnstile():
    assert CaptchaDetector.detect('<div class="cf-turnstile"></div>') == CaptchaType.CLOUDFLARE


def test_cloudflare_challenge():
    assert CaptchaDetector.detect('<form id="challenge-form">') == CaptchaType.CLOUDFLARE


def test_image_captcha():
    assert CaptchaDetector.detect('<img src="https://x.ru/captcha.png">') == CaptchaType.IMAGE


def test_keyword_unknown():
    assert CaptchaDetector.detect("verify you are human") == CaptchaType.UNKNOWN


def test_keyword_cyrillic():
    assert CaptchaDetector.detect("подтвердите что вы не робот") == CaptchaType.UNKNOWN


def test_case_insensitive():
    assert CaptchaDetector.detect('<DIV CLASS="G-RECAPTCHA">') == CaptchaType.RECAPTCHA_V2


def test_recommendations():
    assert CaptchaDetector.get_recommendation(CaptchaType.NONE) == "PROCEED"
    assert CaptchaDetector.get_recommendation(CaptchaType.RECAPTCHA_V2) == "SWITCH_SITE"
    assert CaptchaDetector.get_recommendation(CaptchaType.RECAPTCHA_V3) == "WAIT_AND_RETRY"
    assert CaptchaDetector.get_recommendation(CaptchaType.HCAPTCHA) == "SWITCH_SITE"
    assert CaptchaDetector.get_recommendation(CaptchaType.CLOUDFLARE) == "WAIT_60S_AND_RETRY"
    assert CaptchaDetector.get_recommendation(CaptchaType.IMAGE) == "ASK_USER"
    assert CaptchaDetector.get_recommendation(CaptchaType.UNKNOWN) == "SWITCH_SITE"
