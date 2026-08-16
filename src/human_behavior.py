import asyncio
import random

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
]


class HumanBehavior:
    """Имитация человеческого поведения при работе с браузером."""

    @staticmethod
    async def human_click(selector: str, mcp_bridge) -> str:
        """Клик со случайной точкой внутри элемента + эмуляция mousemove.

        browser_mouse_move не существует в @playwright/mcp — движение
        эмулируем через browser_evaluate, сам клик через browser_click.
        """
        bbox = await mcp_bridge.call_tool(
            "browser_evaluate",
            {"function": f"""
                (() => {{
                    const el = document.querySelector({selector!r});
                    if (!el) return null;
                    const rect = el.getBoundingClientRect();
                    return {{x: rect.x, y: rect.y, w: rect.width, h: rect.height}};
                }})()
            """},
        )

        if not bbox or "error:" in str(bbox):
            return await mcp_bridge.call_tool("browser_click", {"target": selector})

        try:
            import json
            info = json.loads(str(bbox))
        except Exception:
            return await mcp_bridge.call_tool("browser_click", {"target": selector})

        x = info["x"] + random.uniform(info["w"] * 0.2, info["w"] * 0.8)
        y = info["y"] + random.uniform(info["h"] * 0.2, info["h"] * 0.8)

        await mcp_bridge.call_tool("browser_evaluate", {
            "function": f"""
                (() => {{
                    const el = document.elementFromPoint({x:.1f}, {y:.1f});
                    if (el) el.dispatchEvent(new MouseEvent('mousemove', {{bubbles: true}}));
                }})()
            """,
        })

        await asyncio.sleep(random.uniform(0.1, 0.4))
        return await mcp_bridge.call_tool("browser_click", {"target": selector})

    @staticmethod
    async def human_type(selector: str, text: str, mcp_bridge) -> str:
        """Человеческая печать с переменной скоростью (символ за символом)."""
        first = await mcp_bridge.call_tool("browser_click", {"target": selector})
        for char in text:
            await mcp_bridge.call_tool("browser_type", {
                "target": selector,
                "text": char,
            })
            delay = random.uniform(0.05, 0.2)
            if random.random() < 0.03:
                delay += random.uniform(0.3, 1.0)
            await asyncio.sleep(delay)
        return first

    @staticmethod
    async def human_scroll(direction: str = "down",
                           distance: int = 300,
                           mcp_bridge=None) -> str:
        """Скролл рывками с финальной паузой."""
        steps = random.randint(3, 7)
        step_distance = max(1, distance // steps)
        last = "ok"
        for _ in range(steps):
            delta = step_distance if direction == "down" else -step_distance
            last = await mcp_bridge.call_tool("browser_evaluate", {
                "function": f"() => window.scrollBy(0, {delta})",
            })
            await asyncio.sleep(random.uniform(0.05, 0.15))
        await asyncio.sleep(random.uniform(0.5, 1.5))
        return last

    @staticmethod
    async def random_pause(min_sec: float = 0.5, max_sec: float = 2.0):
        """Случайная пауза между действиями."""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    @staticmethod
    def get_random_viewport() -> dict:
        """Случайное разрешение экрана из набора типовых."""
        return random.choice(VIEWPORTS)
