from dataclasses import dataclass, replace

from app._store import Store
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


_store: Store[BriefingConfig] = Store(lambda: settings.briefing_config_path, BriefingConfig)
_from_dict = _store._from_dict


def _load() -> list[BriefingConfig]:
    return _store.load_list()


def _save(configs: list[BriefingConfig]) -> None:
    _store.save_list(configs)


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
