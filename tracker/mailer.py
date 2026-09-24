"""Builds and sends the alert email through Gmail."""

import html
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

SENDER_ENV = "GMAIL_USER"
PASSWORD_ENV = "GMAIL_APP_PASSWORD"
RECIPIENT_ENV = "MAIL_TO"
# "The tracker has a problem" mail goes to whoever looks after it, not to
# the desk. Until this secret exists it falls back to MAIL_TO.
ADMIN_ENV = "ADMIN_MAIL_TO"

ALERTS_NAME = "PTI Alerts"
MAINTENANCE_NAME = "PTI Alerts Maintenance"
FONT = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"


def recipients(env: str = RECIPIENT_ENV) -> list:
    value = os.environ.get(env, "").strip()
    if not value and env == ADMIN_ENV:
        value = os.environ.get(RECIPIENT_ENV, "").strip()
    # Several addresses may be given, separated by commas or new lines.
    return [a.strip() for a in value.replace("\n", ",").split(",") if a.strip()]


def send(subject: str, body_html: str, env: str = RECIPIENT_ENV, name: str = ALERTS_NAME) -> int:
    """Sends one email; returns how many people it went to."""
    sender = os.environ.get(SENDER_ENV, "").strip()
    # Google shows app passwords in groups of four; the spaces are not part of it.
    password = os.environ.get(PASSWORD_ENV, "").replace(" ", "").strip()
    to = recipients(env)
    missing = [n for n, v in ((SENDER_ENV, sender), (PASSWORD_ENV, password), (env, to)) if not v]
    if missing:
        raise RuntimeError(f"Missing email settings: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    shown_as = formataddr((name, sender))
    message["From"] = shown_as
    # Everyone is blind-copied, so no recipient sees the others' addresses.
    message["To"] = shown_as
    message["Bcc"] = ", ".join(to)
    message.set_content("This email needs an HTML-capable reader.")
    message.add_alternative(body_html, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(sender, password)
        # Only to the list: without to_addrs the sending account would get a
        # copy of every alert too, and each copy counts against Gmail's limit.
        server.send_message(message, to_addrs=to)
    return len(to)


def build_alerts(alerts: list) -> tuple:
    """One email for everything new in a scan, in the order PTI filed it,
    so follow-up alerts on the same story read in sequence."""
    alerts = sorted(alerts, key=lambda a: (a.filed, a.slug))
    latest = alerts[-1]
    lead = latest.text or latest.slug
    urgent = any(a.urgent for a in alerts)
    subject = lead if len(alerts) == 1 else f"{len(alerts)} alerts: {lead}"
    if urgent:
        subject = "URGENT: " + subject

    cards = []
    for a in alerts:
        badge = (f'<span style="background:#b91c1c;color:#fff;font:700 10px/1 {FONT};'
                 f'letter-spacing:.08em;padding:4px 7px;border-radius:3px;margin-right:8px;">URGENT</span>'
                 if a.urgent else "")
        where = f"{html.escape(a.dateline)} &middot; " if a.dateline else ""
        cards.append(f"""
        <div style="border:1px solid #e5e7eb;border-left:4px solid {'#b91c1c' if a.urgent else '#1d4ed8'};
                    border-radius:6px;padding:14px 16px;margin-bottom:12px;">
          <div style="margin-bottom:8px;font:400 12px/1.4 {FONT};color:#6b7280;">
            {badge}{where}{a.filed:%H:%M} IST, {a.filed:%d %b}
          </div>
          <div style="font:600 17px/1.4 {FONT};color:#111827;">{html.escape(a.text or a.slug)}</div>
          <div style="font:400 11px/1.4 ui-monospace,Menlo,monospace;color:#9ca3af;margin-top:8px;">
            {html.escape(a.slug)}</div>
        </div>""")

    body = f"""<div style="max-width:640px;margin:0 auto;padding:20px 16px;background:#fff;">
      <div style="font:600 11px/1 {FONT};letter-spacing:.12em;color:#6b7280;
                  text-transform:uppercase;margin-bottom:14px;">PTI news alerts</div>
      {''.join(cards)}
      <div style="color:#9ca3af;font:400 12px/1.5 {FONT};margin-top:16px;
                  border-top:1px solid #e5e7eb;padding-top:10px;">
        Every NEWSALERT filed on the PTI wire, as it appears on
        <a href="https://editorial.pti.in/spectrum/Login.aspx" style="color:#6b7280;">Spectrum</a>.
        The full story follows on the wire.
      </div>
    </div>"""
    return subject[:180], body


def build_notice(headline: str, paragraphs: list) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<div style="max-width:600px;margin:0 auto;padding:24px;
                font:400 15px/1.6 {FONT};color:#222;">
        <p><strong>{html.escape(headline)}</strong></p>{body}</div>"""
