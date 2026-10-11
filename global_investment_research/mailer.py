"""Gmail SMTP delivery for alert and report emails, with a ledger so retries do not resend.

Credentials come only from the environment: MAIL_USERNAME (the Gmail address) and
MAIL_APP_PASSWORD (a Google app password). MAIL_TO is optional and defaults to MAIL_USERNAME.
Secrets are never printed, logged, stored in the ledger or included in error messages.
SMTP has no idempotency key: a crash between Gmail accepting a message and the ledger being
written can still duplicate one send; ordinary workflow retries cannot.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Mapping

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
_ADDRESS = re.compile(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+")


class MailError(RuntimeError):
    """Delivery refused or failed; the message never contains a secret."""


class MailNotConfigured(MailError):
    """MAIL_USERNAME or MAIL_APP_PASSWORD is not set."""


def configured(env: Mapping[str, str] = os.environ) -> bool:
    """True when both Gmail credentials are present (empty strings count as unset)."""
    return bool((env.get("MAIL_USERNAME") or "").strip() and (env.get("MAIL_APP_PASSWORD") or "").strip())


def recipients(to: str | list[str] | None, env: Mapping[str, str] = os.environ) -> list[str]:
    """Explicit `to`, else MAIL_TO, else MAIL_USERNAME; comma/semicolon separated lists allowed."""
    raw = to if to else (env.get("MAIL_TO") or "").strip() or (env.get("MAIL_USERNAME") or "").strip()
    items = raw if isinstance(raw, list) else re.split(r"[,;]", raw or "")
    out = [x.strip() for x in items if x and x.strip()]
    bad = [x for x in out if not _ADDRESS.fullmatch(x)]
    if bad or not out:
        raise MailError("No valid recipient address (set MAIL_TO or pass to=)")
    return out


def ledger_key(subject: str, day: str) -> str:
    return f"{day}|{subject}"


def _load_ledger(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"schema_version": 1, "sent": {}}


def _save_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)


def send(subject: str, html: str, text: str, to: str | list[str] | None = None,
         env: Mapping[str, str] = os.environ, smtp_factory: Callable = smtplib.SMTP_SSL,
         ledger: str | Path | None = None, day: str | None = None) -> dict:
    """Send one multipart (text + HTML) email via Gmail SMTP over SSL.

    With `ledger`, a message already recorded for the same subject and UTC day is not sent
    again (returns status "already sent"). Raises MailNotConfigured when credentials are
    missing and MailError when Gmail refuses; neither message contains a secret.
    """
    day = day or datetime.now(timezone.utc).date().isoformat()
    key = ledger_key(subject, day)
    ledger_path = Path(ledger) if ledger else None
    book = _load_ledger(ledger_path) if ledger_path else None
    if book is not None and key in book["sent"]:
        return {"status": "already sent", "key": key, "sent_at": book["sent"][key]["sent_at"]}
    if not configured(env):
        raise MailNotConfigured("Email not sent: MAIL_USERNAME and MAIL_APP_PASSWORD must both be set "
                                "(GitHub Actions secrets); no message was submitted")
    user = env["MAIL_USERNAME"].strip()
    to_list = recipients(to, env)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to_list), subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtp_factory(SMTP_HOST, SMTP_PORT, timeout=45, context=ssl.create_default_context()) as smtp:
            smtp.login(user, env["MAIL_APP_PASSWORD"].strip())
            refused = smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("Gmail rejected the login for MAIL_USERNAME; check the app password") from None
    except Exception as exc:
        raise MailError(f"Email submission failed ({type(exc).__name__}); no credentials were logged") from None
    if refused:
        raise MailError(f"Gmail refused {len(refused)} recipient(s)")
    sent_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if ledger_path is not None:
        book["sent"][key] = {"subject": subject, "day": day, "sent_at": sent_at, "recipients": len(to_list)}
        _save_ledger(ledger_path, book)
    return {"status": "sent", "key": key, "sent_at": sent_at, "recipients": len(to_list)}


def send_if_configured(subject: str, html: str, text: str, to: str | list[str] | None = None,
                       env: Mapping[str, str] = os.environ, smtp_factory: Callable = smtplib.SMTP_SSL,
                       ledger: str | Path | None = None, day: str | None = None) -> dict:
    """Like `send`, but returns status "skipped" ("email skipped: ...") instead of raising when unconfigured."""
    if not configured(env):
        return {"status": "skipped", "reason": "email skipped: MAIL_USERNAME/MAIL_APP_PASSWORD not set"}
    return send(subject, html, text, to, env=env, smtp_factory=smtp_factory, ledger=ledger, day=day)
