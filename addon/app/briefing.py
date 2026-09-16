import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.logging_config import logger
from app.settings import settings

# Per-sender daily briefing schedule: what local time to send it, and the date it
# was last sent (so a once-a-minute loop fires it exactly once per day, and a late
# add-on restart still catches today's briefing instead of skipping it).


@dataclass
class BriefingConfig:
    sender: str
    hour: int
    minute: int
    enabled: bool = True
    last_sent_date: str | None = None  # ISO date, YYYY-MM-DD


def _load() -> list[BriefingConfig]:
    path = Path(settings.briefing_config_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [BriefingConfig(**c) for c in data]
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


def get_config(sender: str) -> BriefingConfig | None:
    for c in _load():
        if c.sender == sender:
            return c
    return None


def all_enabled() -> list[BriefingConfig]:
    return [c for c in _load() if c.enabled]


def mark_sent(sender: str, date: str) -> None:
    configs = _load()
    for i, c in enumerate(configs):
        if c.sender == sender:
            configs[i] = replace(c, last_sent_date=date)
            _save(configs)
            return
