"""LLM orchestrator for the pdf2spec pipeline.

Runs the deterministic core, then uses LLM to verify ambiguous cases,
fix edge cases, and iteratively improve results. Methodology from
Hermes spec-pdf-csv SKILL.md.
"""
import json
import logging
import re
from pathlib import Path

from src.pdf2spec.extract import extract_records
from src.pdf2spec.row_classify import classify
from src.pdf2spec.fullname import resolve_mother_child
from src.pdf2spec.qa import qa
from src.pdf2spec.clean import clean_name, clean_text

logger = logging.getLogger("pricer.pdf2spec.orchestrator")

RULES_DIR = Path("data/pdf2spec/rules")


def _load_rules() -> dict:
    """Load runtime rules from JSON files."""
    rules = {"splits": [], "headers": [], "diameters": []}
    if RULES_DIR.exists():
        for f in RULES_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for k in rules:
                    if k in data:
                        rules[k].extend(data[k])
            except (json.JSONDecodeError, OSError):
                pass
    return rules


def _save_rules(rules: dict):
    """Persist runtime rules to JSON."""
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    path = RULES_DIR / "runtime_rules.json"
    path.write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")


def run_deterministic(pdf_path: str) -> dict:
    """Run the full deterministic pipeline without LLM.

    Returns dict with keys: rows, log, issues, report, template, records.
    """
    records, report, template = extract_records(pdf_path)
    rows, log = classify(records)
    rows, log = resolve_mother_child(rows, log)
    issues = qa(rows, log)

    return {
        "rows": rows,
        "log": log,
        "issues": issues,
        "report": report,
        "template": template,
        "records": records,
    }


def _build_review_prompt(result: dict) -> str:
    """Build a structured prompt for LLM review.

    Based on Hermes methodology: classify ambiguous rows, discover new rules,
    verify QA cleanliness.
    """
    issues = result["issues"]
    log = result["log"]
    rows = result["rows"]

    orphans = [l for l in log if l.get("type") in ("ORPHAN", "EMPTY_NAME")]
    items_no_qty = issues.get("items_no_qty", [])
    word_splits = issues.get("word_splits", [])

    parts = [
        "Ты — эксперт по спецификациям ОВ/ВК. Проанализируй результат извлечения.",
        f"Всего строк: {issues.get('total_rows', 0)}",
        f"Роли: {issues.get('role_counts', {})}",
        "",
    ]

    if orphans:
        parts.append("Потерянные строки (ORPHAN/EMPTY_NAME):")
        for o in orphans[:15]:
            raw = o.get("raw", o)
            name = raw.get("name", "") if isinstance(raw, dict) else ""
            typ = raw.get("type", "") if isinstance(raw, dict) else ""
            qty = raw.get("qty", "") if isinstance(raw, dict) else ""
            parts.append(
                f"  - type={o.get('type')}, page={o.get('page', '?')}, "
                f"name={name!r}, type_col={typ!r}, qty={qty!r}"
            )
        parts.append("")

    if items_no_qty:
        parts.append("Позиции без количества (возможно, заголовки):")
        for n in items_no_qty[:10]:
            parts.append(f"  - {n[:120]}")
        parts.append("")

    if word_splits:
        parts.append("Остатки split-слов:")
        for s in word_splits:
            parts.append(f"  - {s}")
        parts.append("")

    parts.extend([
        "Задача:",
        "1. Определи роль для каждой ORPHAN/EMPTY_NAME строки: item/header/component/ignore",
        "2. Определи, являются ли items_no_qty заголовками (header) или это позиции без qty",
        "3. Если обнаружены новые split-слова — добавь в new_splits",
        "4. Если обнаружены новые префиксы заголовков — добавь в new_headers",
        "",
        "Формат ответа — строго JSON:",
        '{',
        '  "fixes": [',
        '    {"page": N, "name": "...", "role": "item|header|component|ignore",',
        '     "reason": "краткое объяснение"}',
        '  ],',
        '  "new_splits": [["испорчено", "исправлено"], ...],',
        '  "new_headers": ["префикс1", "префикс2", ...]',
        '}',
    ])

    return "\n".join(parts)


def _apply_fixes(result: dict, review: dict) -> dict:
    """Apply LLM review fixes to the result."""
    rows = result["rows"]
    log = result["log"]
    fixes = review.get("fixes", [])

    applied = 0
    for fix in fixes:
        if not isinstance(fix, dict):
            continue
        page = fix.get("page")
        name = fix.get("name", "")
        new_role = fix.get("role", "")
        if not name or not new_role or new_role == "ignore":
            continue

        for row in rows:
            if row.get("page") == page and row.get("name") == name:
                old_role = row.get("role", "")
                if old_role != new_role:
                    row["role"] = new_role
                    log.append({
                        "type": "LLM_FIX",
                        "name": name[:50],
                        "old_role": old_role,
                        "new_role": new_role,
                        "reason": fix.get("reason", ""),
                    })
                    applied += 1
                break

    new_splits = review.get("new_splits", [])
    new_headers = review.get("new_headers", [])

    rules = _load_rules()
    for split in new_splits:
        if isinstance(split, (list, tuple)) and len(split) == 2:
            from src.pdf2spec.clean import SPLITS
            if list(split) not in [list(s) for s in SPLITS]:
                SPLITS.append(tuple(split))
                rules["splits"].append(list(split))
                logger.info("New split: %s -> %s", split[0], split[1])

    for h in new_headers:
        if isinstance(h, str) and h:
            from src.pdf2spec.row_classify import HEADER_PREFIXES
            if h not in HEADER_PREFIXES:
                HEADER_PREFIXES.append(h)
                rules["headers"].append(h)
                logger.info("New header prefix: %s", h)

    if new_splits or new_headers:
        _save_rules(rules)

    logger.info("Applied %d LLM fixes, %d new splits, %d new headers",
                applied, len(new_splits), len(new_headers))

    return result


async def llm_review(result: dict, llm_client) -> dict:
    """Run LLM review on ambiguous cases.

    Uses chat() interface consistent with the rest of the application.
    """
    prompt = _build_review_prompt(result)

    try:
        response = await llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            force_json=True,
            temperature=0.1,
            max_tokens=2048,
        )

        content = ""
        if isinstance(response, dict):
            choices = response.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
        elif isinstance(response, str):
            content = response

        if not content:
            logger.warning("LLM review returned empty response")
            return result

        try:
            review = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("LLM review returned non-JSON: %s", content[:200])
            return result

        result = _apply_fixes(result, review)
        result["llm_review"] = review
        return result

    except Exception as e:
        logger.warning("LLM review failed: %s", e)
        return result


async def run_full(pdf_path: str, llm_client=None, max_iterations: int = 3) -> dict:
    """Run the full pipeline with optional LLM review loop.

    Args:
        pdf_path: path to the PDF file
        llm_client: optional LLM client for review (None = deterministic only)
        max_iterations: max LLM review iterations

    Returns:
        dict with rows, log, issues, report, template
    """
    result = run_deterministic(pdf_path)

    if llm_client and max_iterations > 0:
        for iteration in range(max_iterations):
            issues = result["issues"]
            orphans = issues.get("orphans", [])
            items_no_qty = issues.get("items_no_qty", [])

            if not orphans and not items_no_qty:
                logger.info("QA clean after %d iterations", iteration)
                break

            logger.info(
                "LLM review iteration %d: %d orphans, %d items_no_qty",
                iteration + 1, len(orphans), len(items_no_qty),
            )
            result = await llm_review(result, llm_client)
            result["issues"] = qa(result["rows"], result["log"])

    return result
