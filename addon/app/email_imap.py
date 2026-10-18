"""IMAP implementation of the EmailBackend protocol.

Uses the Python stdlib's `imaplib` + `email` parser so the add-on container
doesn't gain a new dependency. All IMAP calls are blocking, so each public
method wraps the work in `asyncio.to_thread` to keep the event loop free.

Design notes:
  - Login is attempted only when address AND password are set. The agent-side
    tool handlers already short-circuit when either is empty; the backend
    also rejects the blank-credential case defensively so the IMAP server
    never sees a half-filled login.
  - Attachments larger than MAX_ATTACHMENT_BYTES keep their metadata but
    drop the payload (`content=None`). Items 20/21 need the payload under
    the cap for receipt / iCal auto-extract, so under-cap attachments load
    their bytes by default.
  - HTML-only bodies are stripped with a minimal regex (no BeautifulSoup
    dependency). Mixed multipart/alternative prefers text/plain.
  - Headers can be absent or malformed in the wild (RFC 5322 is permissive);
    missing Subject → "", missing / unparseable Date → datetime.now(UTC).
  - Connection failures log the IMAP host and a MASKED address — never the
    password, never the full address.
  - Every operation opens its own connection and closes it in a `finally`
    so a mid-flight exception can't leak a half-authenticated socket.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from app.email_backend import (
    MAX_ATTACHMENT_BYTES,
    EmailAttachment,
    EmailMessage,
)
from app.settings import settings

logger = logging.getLogger(__name__)

# Snippet is the first ~200 chars of the plain-text body, with runs of
# whitespace collapsed to one space. Keeps the agent-visible preview small
# (a long body in a list_recent result would eat context) while still giving
# the model enough to decide whether to call read_email for the full text.
_SNIPPET_LEN = 200
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _mask_address(addr: str) -> str:
    """Returns 'ab…@domain' so logs never leak the full address."""
    if not addr or "@" not in addr:
        return "(no address)"
    local, _, domain = addr.partition("@")
    prefix = local[:2] if local else ""
    return f"{prefix}…@{domain}"


def _decode_header(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _parse_date(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return datetime.now(timezone.utc)
    if dt is None:
        return datetime.now(timezone.utc)
    # Normalise to tz-aware so callers can compare freely.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parts(msg: Message) -> list[Message]:
    """Flatten a (possibly nested multipart) message into its leaf parts."""
    if msg.is_multipart():
        out: list[Message] = []
        for part in msg.walk():
            if not part.is_multipart():
                out.append(part)
        return out
    return [msg]


def _is_attachment(part: Message) -> bool:
    disposition = (part.get("Content-Disposition") or "").lower()
    if "attachment" in disposition:
        return True
    # Treat inline files with a filename as attachments (e.g. receipt PDFs
    # mailed with Content-Disposition: inline).
    if "inline" in disposition and part.get_filename():
        return True
    return False


def _collect_bodies(parts: list[Message]) -> tuple[str, str]:
    """Returns (plain_text, html_text) joined across all matching parts."""
    plain_chunks: list[str] = []
    html_chunks: list[str] = []
    for part in parts:
        if _is_attachment(part):
            continue
        ctype = (part.get_content_type() or "").lower()
        if ctype == "text/plain":
            plain_chunks.append(_decode_part_text(part))
        elif ctype == "text/html":
            html_chunks.append(_decode_part_text(part))
    return "\n".join(c for c in plain_chunks if c), "\n".join(c for c in html_chunks if c)


def _decode_part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        # Fall back to the already-decoded str payload for the rare case
        # imaplib hands us a non-bytes body.
        text = part.get_payload()
        return text if isinstance(text, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    # Minimal regex strip — intentionally no BeautifulSoup to keep the add-on
    # dependency surface small. Collapses tags; whitespace normalisation is
    # done by _make_snippet / caller.
    return _HTML_TAG_RE.sub(" ", html)


def _body_text(msg: Message) -> str:
    plain, html = _collect_bodies(_parts(msg))
    if plain.strip():
        return plain
    if html.strip():
        return _html_to_text(html)
    return ""


def _make_snippet(body_text: str) -> str:
    if not body_text:
        return ""
    collapsed = _WS_RE.sub(" ", body_text).strip()
    return collapsed[:_SNIPPET_LEN]


def _to_addrs(msg: Message) -> list[str]:
    tos = msg.get_all("To", []) or []
    ccs = msg.get_all("Cc", []) or []
    return [addr for _, addr in getaddresses(tos + ccs) if addr]


def _from_addr(msg: Message) -> str:
    raw = msg.get("From", "") or ""
    pairs = getaddresses([raw])
    if pairs and pairs[0][1]:
        return pairs[0][1]
    return _decode_header(raw)


def _collect_attachments(
    msg: Message, *, load_content: bool
) -> list[EmailAttachment]:
    out: list[EmailAttachment] = []
    for part in _parts(msg):
        if not _is_attachment(part):
            continue
        filename = _decode_header(part.get_filename() or "")
        content_type = (part.get_content_type() or "application/octet-stream").lower()
        payload = part.get_payload(decode=True) or b""
        size = len(payload)
        if load_content and size <= MAX_ATTACHMENT_BYTES:
            content: bytes | None = payload
        else:
            content = None
        out.append(
            EmailAttachment(
                filename=filename,
                content_type=content_type,
                size=size,
                content=content,
            )
        )
    return out


def _build_message(
    uid: str, raw: bytes, *, with_body: bool, with_attachments: bool
) -> EmailMessage:
    msg = email.message_from_bytes(raw)
    body_text = _body_text(msg) if with_body else ""
    attachments = (
        _collect_attachments(msg, load_content=with_attachments)
        if with_attachments
        else []
    )
    return EmailMessage(
        uid=uid,
        from_addr=_from_addr(msg),
        to_addrs=_to_addrs(msg),
        subject=_decode_header(msg.get("Subject")),
        date=_parse_date(msg.get("Date")),
        snippet=_make_snippet(body_text if with_body else _body_text(msg)),
        body_text=body_text,
        attachments=attachments,
    )


class ImapBackend:
    """IMAP read-only backend.

    One instance per add-on process is fine; it holds no persistent socket.
    Every public method opens, uses, and closes its own IMAP connection, so
    an intermittent network blip only affects the in-flight call.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        address: str | None = None,
        password: str | None = None,
    ) -> None:
        # Snapshot settings at construction so a later settings change doesn't
        # make one tool call land on a different host than another.
        self._host = host if host is not None else settings.email_imap_host
        self._port = port if port is not None else settings.email_imap_port
        self._address = address if address is not None else settings.email_address
        self._password = password if password is not None else settings.email_password

    # ------------------------------------------------------------------ sync

    def _reject_if_blank(self) -> None:
        if not self._address or not self._password:
            raise RuntimeError(
                "IMAP credentials are blank; refusing to attempt login. "
                "Set EMAIL_ADDRESS and EMAIL_PASSWORD."
            )

    def _connect(self) -> imaplib.IMAP4_SSL:
        self._reject_if_blank()
        try:
            conn = imaplib.IMAP4_SSL(self._host, self._port)
            conn.login(self._address, self._password)
            conn.select("INBOX", readonly=True)
            return conn
        except Exception:
            logger.exception(
                "IMAP connect failed host=%s address=%s",
                self._host,
                _mask_address(self._address),
            )
            raise

    @staticmethod
    def _close(conn: imaplib.IMAP4_SSL | None) -> None:
        if conn is None:
            return
        try:
            try:
                conn.close()
            except Exception:
                # 'close' is only valid when a mailbox is selected; ignore
                # the "command CLOSE illegal in state AUTH" case.
                pass
            conn.logout()
        except Exception:
            logger.debug("IMAP logout failed; ignoring", exc_info=True)

    def _list_recent_sync(self, limit: int, since_hours: int) -> list[EmailMessage]:
        conn = None
        try:
            conn = self._connect()
            since_date = (
                datetime.now(timezone.utc) - timedelta(hours=max(since_hours, 1))
            ).strftime("%d-%b-%Y")
            typ, data = conn.search(None, f'(SINCE "{since_date}")')
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()
            # Newest-first, bounded by limit.
            selected = list(reversed(uids))[: max(limit, 0)]
            messages: list[EmailMessage] = []
            for uid_bytes in selected:
                uid = uid_bytes.decode("ascii")
                typ, msg_data = conn.fetch(uid_bytes, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = _extract_rfc822(msg_data)
                if raw is None:
                    continue
                messages.append(
                    _build_message(uid, raw, with_body=False, with_attachments=False)
                )
            return messages
        finally:
            self._close(conn)

    def _fetch_sync(self, uid: str) -> EmailMessage | None:
        conn = None
        try:
            conn = self._connect()
            typ, msg_data = conn.fetch(uid.encode("ascii"), "(RFC822)")
            if typ != "OK" or not msg_data:
                return None
            raw = _extract_rfc822(msg_data)
            if raw is None:
                return None
            return _build_message(
                uid, raw, with_body=True, with_attachments=True
            )
        finally:
            self._close(conn)

    def _search_sync(self, query: str, limit: int) -> list[EmailMessage]:
        if not query.strip():
            return []
        conn = None
        try:
            conn = self._connect()
            # IMAP SEARCH TEXT is a server-side substring search on the
            # whole message. Simpler than SUBJECT/BODY combinations and
            # matches what the user intuitively means by "search my email".
            encoded = query.replace("\\", "\\\\").replace('"', '\\"')
            typ, data = conn.search(None, "TEXT", f'"{encoded}"')
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()
            selected = list(reversed(uids))[: max(limit, 0)]
            messages: list[EmailMessage] = []
            for uid_bytes in selected:
                uid = uid_bytes.decode("ascii")
                typ, msg_data = conn.fetch(uid_bytes, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = _extract_rfc822(msg_data)
                if raw is None:
                    continue
                messages.append(
                    _build_message(uid, raw, with_body=False, with_attachments=False)
                )
            return messages
        finally:
            self._close(conn)

    # ----------------------------------------------------------------- async

    async def list_recent(
        self, limit: int = 10, since_hours: int = 24
    ) -> list[EmailMessage]:
        return await asyncio.to_thread(self._list_recent_sync, limit, since_hours)

    async def fetch(self, uid: str) -> EmailMessage | None:
        return await asyncio.to_thread(self._fetch_sync, uid)

    async def search(self, query: str, limit: int = 10) -> list[EmailMessage]:
        return await asyncio.to_thread(self._search_sync, query, limit)

    # ---------------------------------------------------------- Item 22 writes
    #
    # The spam scanner needs to move messages to the provider's Spam folder.
    # Writes stay gated at the SCANNER level by `settings.email_write_enabled`
    # — the backend here just offers the primitives. Everything is MOVE (to
    # Spam / Trash / arbitrary folder); hard-delete is deliberately not
    # exposed. Spam-folder recovery on Gmail / Yahoo / iCloud is ~30 days,
    # which is the safety margin that lets the scanner go live without a
    # mistake becoming an unrecoverable mail loss.

    def _detect_spam_folder_sync(self) -> str:
        """Walks the server's folder list for a plausible Spam/Junk folder.
        Returns the first match, else '' so the caller can fall back to the
        explicit `email_spam_folder` setting (or report misconfig)."""
        conn = None
        try:
            conn = self._connect_rw()
            typ, data = conn.list()
            if typ != "OK" or not data:
                return ""
            # Common provider spellings. Order matters — more specific first.
            candidates = [
                "[Gmail]/Spam", "[Google Mail]/Spam",
                "Bulk Mail", "Bulk",       # Yahoo
                "Junk", "Junk E-mail",     # iCloud / Outlook
                "Spam",                    # generic
            ]
            available: list[str] = []
            for row in data:
                if row is None:
                    continue
                line = row.decode("utf-8", errors="replace") if isinstance(row, (bytes, bytearray)) else str(row)
                # IMAP LIST reply is '(\\flag...) "/" "FolderName"'; a loose
                # endswith is enough to pick the folder name reliably without
                # a quoted-string parser.
                available.append(line)
            for cand in candidates:
                for line in available:
                    # Match on quoted name OR as a plain suffix after a space
                    if f'"{cand}"' in line or line.rstrip().endswith(f" {cand}"):
                        return cand
            return ""
        except Exception:
            logger.exception("IMAP spam-folder detection failed")
            return ""
        finally:
            self._close(conn)

    def _connect_rw(self) -> imaplib.IMAP4_SSL:
        """Like `_connect` but selects INBOX in read/write so STORE/COPY/EXPUNGE
        succeed. Scanner-only; the read tools stay on the read-only session."""
        self._reject_if_blank()
        try:
            conn = imaplib.IMAP4_SSL(self._host, self._port)
            conn.login(self._address, self._password)
            conn.select("INBOX", readonly=False)
            return conn
        except Exception:
            logger.exception(
                "IMAP connect(rw) failed host=%s address=%s",
                self._host,
                _mask_address(self._address),
            )
            raise

    def _move_to_folder_sync(self, uid: str, folder: str) -> bool:
        """Copy to `folder`, then mark the original \\Deleted and EXPUNGE.
        Returns True on success. Tries IMAP MOVE first (RFC 6851) and falls
        back to COPY+DELETE for servers that don't support it."""
        if not uid or not folder:
            return False
        conn = None
        try:
            conn = self._connect_rw()
            uid_b = uid.encode("ascii")
            folder_q = f'"{folder}"'
            # Prefer atomic MOVE where available.
            try:
                typ, _ = conn.uid("MOVE", uid, folder)  # type: ignore[attr-defined]
                if typ == "OK":
                    return True
            except Exception:
                pass
            # Fallback: COPY + \\Deleted + EXPUNGE
            typ, _ = conn.uid("COPY", uid, folder_q)
            if typ != "OK":
                logger.warning("IMAP COPY uid=%s -> %s returned %s", uid, folder, typ)
                return False
            typ, _ = conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            if typ != "OK":
                logger.warning("IMAP STORE +Deleted uid=%s returned %s", uid, typ)
                return False
            conn.expunge()
            return True
        except Exception:
            logger.exception("IMAP move_to_folder uid=%s folder=%s failed", uid, folder)
            return False
        finally:
            self._close(conn)

    def _mark_read_sync(self, uid: str) -> bool:
        if not uid:
            return False
        conn = None
        try:
            conn = self._connect_rw()
            typ, _ = conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            return typ == "OK"
        except Exception:
            logger.exception("IMAP mark_read uid=%s failed", uid)
            return False
        finally:
            self._close(conn)

    async def detect_spam_folder(self) -> str:
        return await asyncio.to_thread(self._detect_spam_folder_sync)

    async def move_to_folder(self, uid: str, folder: str) -> bool:
        return await asyncio.to_thread(self._move_to_folder_sync, uid, folder)

    async def mark_read(self, uid: str) -> bool:
        return await asyncio.to_thread(self._mark_read_sync, uid)


def _extract_rfc822(msg_data: list[Any]) -> bytes | None:
    """imaplib returns a list of parts; the RFC822 body is the second
    element of the first non-trivial tuple. Walk the structure defensively.
    """
    for item in msg_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None
