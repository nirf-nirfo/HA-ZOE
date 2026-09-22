import json
from pathlib import Path
from typing import Any

import httpx

from app.logging_config import logger
from app.settings import settings

# Jewish + Israeli civic holidays via Hebcal's free JSON API. Results are cached
# per-year on disk so the briefing loop hits the network at most once per year.
# On any fetch failure the briefing continues silently — a missing holidays line
# must never break the whole message.

# Substring matches (lower-case) against Hebcal's English title. Kids are
# typically off from school on these — for the middle days of Sukkot/Pesach the
# whole week counts as a break, so a broad match is intentional.
_SCHOOL_OFF = [
    "rosh hashana",
    "yom kippur",
    "sukkot",
    "shmini atzeret",
    "simchat torah",
    "chanukah",
    "purim",
    "pesach",
    "yom haatzma",
    "shavuot",
]
_SCHOOL_CEREMONY = [
    "yom hazikaron",
    "yom hashoah",
]

# maj: major, min: minor, mod: modern (civic), mf: minor fasts, i=on: Israel schedule
_HEBCAL_URL = (
    "https://www.hebcal.com/hebcal?v=1&cfg=json"
    "&maj=on&min=on&mod=on&mf=on&i=on&year={year}"
)


async def _fetch_year(year: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(_HEBCAL_URL.format(year=year))
        r.raise_for_status()
        return r.json().get("items", [])


def _load_cache() -> dict[str, list[dict[str, Any]]]:
    path = Path(settings.holidays_cache_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not load holidays cache, starting fresh")
        return {}


def _save_cache(cache: dict) -> None:
    path = Path(settings.holidays_cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


async def _items_for_year(year: int) -> list[dict[str, Any]]:
    cache = _load_cache()
    key = str(year)
    if key in cache:
        return cache[key]
    try:
        items = await _fetch_year(year)
    except Exception:
        logger.exception("Failed to fetch Hebcal for %d", year)
        return []
    cache[key] = items
    _save_cache(cache)
    return items


def _school_status(title_en: str) -> str:
    t = title_en.lower()
    for pat in _SCHOOL_OFF:
        if pat in t:
            return "off"
    for pat in _SCHOOL_CEREMONY:
        if pat in t:
            return "ceremony"
    return "regular"


async def holidays_for_date(date: str) -> list[dict[str, str]]:
    """Returns Hebcal items falling on `date` (YYYY-MM-DD). Each entry has
    `title` (Hebrew), `title_en`, and `school_status` ('off'|'ceremony'|'regular')."""
    try:
        year = int(date[:4])
    except ValueError:
        return []
    items = await _items_for_year(year)
    out = []
    for it in items:
        if it.get("date", "").startswith(date):
            title_en = it.get("title") or ""
            title_he = it.get("hebrew") or title_en
            out.append(
                {
                    "title": title_he,
                    "title_en": title_en,
                    "school_status": _school_status(title_en),
                }
            )
    return out
