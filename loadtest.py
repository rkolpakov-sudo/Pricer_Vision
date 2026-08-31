"""Нагрузочное тестирование agent_loop.process_row без GUI."""
import argparse
import asyncio
import sys
import time
import threading
import logging

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("loadtest")

TEST_SPECS = [
    "Труба стальная водогазопроводная Ду100 ГОСТ 10704-91",
    "Кран шаровой латунный Ду15 Ру16 G1/2",
    "Радиатор стальной панельный LEMAX Premium C10 500x600",
    "Насос циркуляционный Grundfos UPS 25-60",
]


async def run_test():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, default=None, help="run single test by index (0-3)")
    args, _ = parser.parse_known_args()

    from src.graph_engine import GraphEngine
    from src.memory_manager import MemoryManager
    from src.mcp_bridge import MCPBridge
    from src.llm_providers import create_llm_client
    from src.agent_loop import process_row
    from src.config_loader import load_settings

    settings = load_settings()

    graph = GraphEngine("data/pricer.db")
    mm = MemoryManager(graph)

    llm = create_llm_client(settings)

    bridge = MCPBridge()

    logger.info("Starting MCP bridge...")
    await bridge.start()
    logger.info("MCP bridge ready with %d tools", len(await bridge.list_tools()))

    await llm.__aenter__()
    logger.info("LLM connected: model=%s", llm.model)

    results = []
    specs = TEST_SPECS
    if args.only is not None:
        specs = [TEST_SPECS[args.only]]
    for spec in specs:
        logger.info("=" * 60)
        logger.info("TEST: %s", spec)
        t0 = time.monotonic()
        try:
            result = await process_row(
                spec_text=spec,
                llm_client=llm,
                mcp_bridge=bridge,
                graph_engine=graph,
                memory_manager=mm,
                stop_event=threading.Event(),
                fresh=True,
                use_approaches=True,
                use_site_ranking=True,
            )
            elapsed = time.monotonic() - t0
            price = result.get("price")
            conf = result.get("confidence", 0)
            rounds = result.get("rounds", "?")
            logger.info("RESULT: price=%s conf=%.2f elapsed=%.1fs rounds=%s",
                        price, conf, elapsed, rounds)
            results.append({"spec": spec, "price": price, "conf": conf,
                            "elapsed": elapsed, "rounds": rounds})
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error("ERROR: %s (%.1fs)", e, elapsed)
            results.append({"spec": spec, "error": str(e), "elapsed": elapsed})

    await bridge.stop()
    await llm.__aexit__(None, None, None)

    logger.info("=" * 60)
    logger.info("SUMMARY:")
    for r in results:
        if "error" in r:
            logger.info("  FAIL %s: %s (%.1fs)", r["spec"][:40], r["error"][:60], r["elapsed"])
        else:
            logger.info("  OK   %s: price=%s conf=%.2f elapsed=%.1fs rounds=%s",
                        r["spec"][:40], r["price"], r["conf"], r["elapsed"], r["rounds"])


if __name__ == "__main__":
    asyncio.run(run_test())
