"""Resolves WhatsApp phone numbers to human names by scanning long-term memory
facts for 'the number X is Y' patterns. Called wherever a household-visible
message needs to display a sender by name instead of a raw phone.

Memory facts we recognize (case-insensitive, Hebrew or English):
- 'המספר +972... הוא של [name]'
- 'המספר +972... שייך ל[name]'
- 'the number +972... is [name]'
- '[name]'s phone is +972...'
- '+972... = [name]'
"""

import re

_PATTERNS_PHONE_FIRST = [
    re.compile(r"המספר\s+(\+?\d{9,15})\s+(?:הוא\s+של|שייך\s+ל)\s*(.+?)(?:\s*[\.\,]|$)", re.IGNORECASE),
    re.compile(r"the\s+number\s+(\+?\d{9,15})\s+is\s+(.+?)(?:\s*[\.\,]|$)", re.IGNORECASE),
    re.compile(r"(\+?\d{9,15})\s*=\s*(.+?)(?:\s*[\.\,]|$)"),
]
_PATTERN_NAME_FIRST = re.compile(r"(.+?)['’]s\s+phone\s+is\s+(\+?\d{9,15})", re.IGNORECASE)


def _canonical(phone: str) -> str:
    """Strips '+', keeps digits. WhatsApp payloads use no '+'."""
    return re.sub(r"\D", "", phone)


_cache: dict[str, str] | None = None


def _build_map() -> dict[str, str]:
    from app import memory  # lazy to avoid circular import
    m: dict[str, str] = {}
    for f in memory.all_facts():
        for pat in _PATTERNS_PHONE_FIRST:
            match = pat.search(f.text)
            if match:
                m[_canonical(match.group(1))] = match.group(2).strip()
        match = _PATTERN_NAME_FIRST.search(f.text)
        if match:
            m[_canonical(match.group(2))] = match.group(1).strip()
    return m


def resolve(phone: str) -> str:
    """Returns human name for a phone, or the phone unchanged if unknown.
    Cached per-process; call `refresh()` after memory changes."""
    global _cache
    if _cache is None:
        _cache = _build_map()
    return _cache.get(_canonical(phone), phone)


def refresh() -> None:
    """Invalidate the cache after memory changes."""
    global _cache
    _cache = None
