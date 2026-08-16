import asyncio
import json
import logging

logger = logging.getLogger("pricer.analyzer")

SPA_INDICATORS = [
    "window.__NUXT__",
    "window.__NEXT_DATA__",
    "window.__NUXT_DATA__",
    "window.__GATSBY",
    "ng-version",
    "data-reactroot",
    "__vue__",
    "id='root'",
    "id='app'",
]

ANTIBOT_INDICATORS = [
    "cloudflare",
    "cf-browser-verification",
    "captcha",
    "recaptcha",
    "hcaptcha",
    "datadome",
    "perimeterx",
    "px-captcha",
    "challenge-form",
    "ddos-guard",
]


class SiteAnalyzer:
    """Определяет тип сайта (SPA/SSR) и наличие антибот-защиты.

    Профиль хранится в памяти (JSON на диске не обязателен) — в таблицах
    БД нет полей под эти данные.
    """

    SPA_INDICATORS = SPA_INDICATORS
    ANTIBOT_INDICATORS = ANTIBOT_INDICATORS

    def __init__(self):
        self.profiles: dict[str, dict] = {}

    async def analyze_site(self, page_url: str, mcp_bridge) -> dict:
        """Анализирует сайт и возвращает профиль. Домен — ключ кэша."""
        domain = self._domain_of(page_url)
        await mcp_bridge.call_tool("browser_navigate", {"url": page_url})
        await asyncio.sleep(2)

        is_spa = await self._detect_spa(mcp_bridge)
        has_antibot = await self._detect_antibot(mcp_bridge)
        dom_stats = await self._get_dom_stats(mcp_bridge)

        profile = {
            "url": page_url,
            "domain": domain,
            "is_spa": is_spa,
            "has_antibot": has_antibot,
            "dom_depth": dom_stats.get("max_depth", 10),
            "dom_elements": dom_stats.get("total_elements", 0),
            "recommended_strategy": self._recommend_strategy(is_spa, has_antibot),
        }
        self.profiles[domain] = profile
        logger.info("Site %s: spa=%s antibot=%s strategy=%s",
                    domain, is_spa, has_antibot, profile["recommended_strategy"])
        return profile

    async def _detect_spa(self, mcp_bridge) -> bool:
        for indicator in self.SPA_INDICATORS:
            if indicator.startswith("window."):
                check = f"() => typeof {indicator} !== 'undefined'"
            else:
                escaped = indicator.replace("'", "\\'")
                check = f"() => !!document.querySelector('{escaped}')"
            try:
                result = await mcp_bridge.call_tool("browser_evaluate", {"function": check})
                if str(result).strip() == "true":
                    return True
            except Exception as e:
                logger.debug("SPA check failed for %s: %s", indicator, e)
        return False

    async def _detect_antibot(self, mcp_bridge) -> bool:
        page_source = await mcp_bridge.call_tool("browser_snapshot", {})
        page_lower = str(page_source).lower()
        return any(ind in page_lower for ind in self.ANTIBOT_INDICATORS)

    async def _get_dom_stats(self, mcp_bridge) -> dict:
        result = await mcp_bridge.call_tool("browser_evaluate", {
            "function": """
                (() => {
                    function getDepth(el) {
                        let depth = 0;
                        while (el.parentElement) {
                            depth++;
                            el = el.parentElement;
                        }
                        return depth;
                    }
                    const allElements = document.querySelectorAll('*');
                    let maxDepth = 0;
                    for (let el of allElements) {
                        maxDepth = Math.max(maxDepth, getDepth(el));
                    }
                    return { total_elements: allElements.length, max_depth: maxDepth };
                })()
            """,
        })
        if not result or "error:" in str(result):
            return {"total_elements": 0, "max_depth": 0}
        try:
            parsed = json.loads(str(result))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return {"total_elements": 0, "max_depth": 0}

    def _recommend_strategy(self, is_spa: bool, has_antibot: bool) -> str:
        if has_antibot:
            return "CAUTIOUS"
        elif is_spa:
            return "SPA_AWARE"
        return "STANDARD"

    @staticmethod
    def _domain_of(url: str) -> str:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return host.removeprefix("www.") if host else url

    def get_profile(self, url: str) -> dict | None:
        return self.profiles.get(self._domain_of(url))
