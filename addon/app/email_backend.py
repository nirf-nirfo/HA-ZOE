"""Email backend protocol + shared data model.

Item 19 introduces a read-only email surface for ZOE. The pluggable backend
shape (list_recent / fetch / search) is defined here so Items 20 and 21 can
slot in receipt / iCal extraction and an hourly watch loop without having to
re-shape the interface. The concrete IMAP implementation lives in
`email_imap.py`; this module intentionally has no I/O so tests of the data
shape stay fast and dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

# Hard cap on attachment payload bytes we pull into memory from a single
# message. Prevents an unexpectedly huge PDF (100+ MB) from OOMing the add-on
# while still letting Items 20/21 inspect normal receipts and iCal invites
# (both typically well under 1 MB). Attachments above this get `content=None`
# but keep their metadata (filename / content_type / size) so the UI and
# tool outputs can still describe them.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@dataclass
class EmailAttachment:
    filename: str
    content_type: str
    size: int
    content: bytes | None = None


@dataclass
class EmailMessage:
    uid: str
    from_addr: str
    to_addrs: list[str]
    subject: str
    date: datetime
    snippet: str
    body_text: str = ""
    attachments: list[EmailAttachment] = field(default_factory=list)


class EmailBackend(Protocol):
    """Pluggable email read surface.

    Three operations:
      - list_recent: lightweight headers + snippet for the last N messages
        within a time window. No body, no attachments.
      - fetch: full body + attachment payloads (subject to
        MAX_ATTACHMENT_BYTES) for one specific message by uid.
      - search: free-text substring search over the mailbox; same lightweight
        shape as list_recent.

    All methods are async because real-world backends (IMAP, Gmail API,
    Microsoft Graph) are network calls. The stdlib `imaplib` implementation
    wraps its blocking calls in `asyncio.to_thread`.
    """

    async def list_recent(
        self, limit: int = 10, since_hours: int = 24
    ) -> list[EmailMessage]: ...

    async def fetch(self, uid: str) -> EmailMessage | None: ...

    async def search(self, query: str, limit: int = 10) -> list[EmailMessage]: ...
