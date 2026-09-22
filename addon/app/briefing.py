import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from app.logging_config import logger
from app.settings import settings

# Per-sender briefing schedule: morning (full agenda for today) at (hour, minute)
# and evening (short recap + tomorrow preview) at (evening_hour, evening_minute).
# last_sent_date / last_evening_sent_date deduplicate a once-a-minute loop so
# each briefing fires exactly once per day, and a late add-on restart still
# catches today's morning briefing instead of skipping it.


@dataclass
class BriefingConfig:
    sender: str
    hour: int
    minute: int
    enabled: bool = True
    last_sent_date: str | None = None
    evening_hour: int = 20
    evening_minute: int = 0
    evening_enabled: bool = True
    last_evening_sent_date: str | None = None


_FIELDS = {f.name for f in fields(BriefingConfig)}


def _from_dict(d: dict) -> BriefingConfig:
    # Ignore any legacy fields not on the current schema, so an old briefing_config.json
    # written before evening support was added still loads cleanly.
    return BriefingConfig(**{k: v for k, v in d.items() if k in _FIELDS})


def _load() -> list[BriefingConfig]:
    path = Path(settings.briefing_config_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [_from_dict(c) for c in data]
    except Exception:
        logger.warning("Could not load briefing config file, starting fresh")
        return []


def _save(configs: list[BriefingConfig]) -> None:
    path = Path(settings.briefing_config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(c) for c in configs], ensure_ascii=False),
        encoding="utf-8",
    )


def set_config(sender: str, hour: int, minute: int, enabled: bool = True) -> BriefingConfig:
    configs = _load()
    for i, c in enumerate(configs):
        if c.sender == sender:
            configs[i] = replace(c, hour=hour, minute=minute, enabled=enabled)
            _save(configs)
            return configs[i]
    new = BriefingConfig(sender=sender, hour=hour, minute=minute, enabled=enabled)
    configs.append(new)
    _save(configs)
    return new


def set_evening_config(sender: str, hour: int, minute: int, enabled: bool = True) -> BriefingConfig:
    configs = _load()
    for i, c in enumerate(configs):
        if c.sender == sender:
            configs[i] = replace(c, evening_hour=hour, evening_minute=minute, evening_enabled=enabled)
            _save(configs)
            return configs[i]
    # No morning config yet: create one with default morning 08:00 so evening can stand alone.
    new = BriefingConfig(
        sender=sender, hour=8, minute=0,
        evening_hour=hour, evening_minute=minute, evening_enabled=enabled,
    )
    configs.append(new)
    _save(configs)
    return new


def get_config(sender: str) -> BriefingConfig | None:
    for c in _load():
        if c.sender == sender:
            return c
    return None


def all_configs() -> list[BriefingConfig]:
    return _load()


def all_enabled() -> list[BriefingConfig]:
    return [c for c in _load() if c.enabled or c.evening_enabled]


def mark_sent(sender: str, date: str) -> None:
    configs = _load()
    for i, c in enumerate(configs):
        if c.sender == sender:
            configs[i] = replace(c, last_sent_date=date)
            _save(configs)
            return


def mark_evening_sent(sender: str, date: str) -> None:
    configs = _load()
    for i, c in enumerate(configs):
        if c.sender == sender:
            configs[i] = replace(c, last_evening_sent_date=date)
            _save(configs)
            return
