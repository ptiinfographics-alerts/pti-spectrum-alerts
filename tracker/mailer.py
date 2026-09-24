"""Builds and sends the alert email through Gmail."""

import html
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from . import stories

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


NAVY = "#0C447C"
RED, RED_WASH, RED_INK = "#C62828", "#FCEBEB", "#791F1F"
AMBER, AMBER_WASH, AMBER_INK = "#B26A00", "#FFF4DF", "#633806"
GREY = "#6b7280"


def _tag(label: str, wash: str, ink: str) -> str:
    return (f'<span style="background:{wash};color:{ink};font:600 11px/1 {FONT};'
            f'padding:3px 6px;border-radius:4px;margin-right:6px;">{label}</span>')


def clock(when) -> str:
    """5:38 PM -- the 12-hour clock, without a leading zero."""
    return f"{when:%I:%M %p}".lstrip("0")


def _where(alert) -> str:
    place = alert.dateline.title() if alert.dateline else ""
    return f"{place + ' · ' if place else ''}{clock(alert.filed)} IST"


def label(alert) -> str:
    """What happened on the wire, in PTI's terms. "CORRECTED" only when PTI
    itself marked it so; "RE-FILED" when the same item number was filed
    again with different words. Neither says which version is right."""
    if alert.kind != "correction":
        return ""
    return "CORRECTED" if stories.is_marked_correction(alert) else "RE-FILED"


SUBJECT_CHARS = 80
URGENT_MARK = "\U0001F534"      # red circle, before an urgent alert's subject


def _short(text: str, limit: int = SUBJECT_CHARS) -> str:
    """At most `limit` characters, cut between words. The whole alert is
    always in the email itself."""
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rsplit(" ", 1)[0] if " " in text[:limit + 1] else text[:limit]
    return cut.rstrip(" ,;:-\u2013\u2014") + "\u2026"


def _card(alert, show_earlier: bool = True) -> str:
    kind = label(alert)
    tags = (_tag("URG", RED_WASH, RED_INK) if alert.urgent else "") + \
        (_tag(kind, AMBER_WASH, AMBER_INK) if kind else "")
    copy, notes, was_story = stories.clean(alert.text)
    rule = AMBER if kind else (RED if alert.urgent else NAVY)
    small = f"font:400 12px/1.5 {FONT};color:{GREY};margin-top:6px;"
    mono = "font:400 11px/1.4 ui-monospace,Menlo,monospace;"

    extra = "".join(f'<div style="{small}">(Eds: {html.escape(n)})</div>' for n in notes)
    if was_story:
        extra += f'<div style="{small}">Headline only. Filed on the wire as a full story.</div>'

    if kind:
        old = alert.replaces
        if old is not None:
            words = " ".join(
                f'<span style="background:#FFE08A;">{html.escape(w)}</span>' if changed else html.escape(w)
                for w, changed in stories.changed_words(old.text, alert.text))
            held = " · not emailed" if old.held else ""
            extra += f"""
          <div style="background:#F3F4F6;border-radius:6px;padding:10px 12px;margin-top:14px;
                      font:400 13px/1.5 {FONT};color:#374151;">
            <div style="font-size:12px;color:{GREY};margin-bottom:4px;">Earlier version ·
              {clock(old.filed)} IST · <span style="{mono}">{html.escape(stories.readable(old.slug))}</span>{held} ·
              differences highlighted</div>{words}
          </div>"""
        else:
            extra += f'<div style="{small}">Earlier version not found on today\'s wire.</div>'

    if alert.earlier and show_earlier:
        # Newest first, like the email itself. Each row shows its full slug
        # with PTI's number (DEL062 · RAHUL-PRESSER 4), so a repeated or
        # skipped number on the wire is plain to see.
        rows = "".join(
            f'<div style="padding:7px 0;border-top:1px solid #D1D5DB;">'
            f'<span style="font-weight:600;font-variant-numeric:tabular-nums;">{clock(e.filed)}</span>'
            f' &nbsp;<span style="{mono}">{html.escape(stories.readable(e.slug))}</span><br>'
            f'{html.escape(stories.clean(e.text)[0])}</div>'
            for e in sorted(alert.earlier, key=stories.order, reverse=True))
        extra += f"""
          <div style="background:#F3F4F6;border-radius:6px;margin-top:16px;padding:8px 12px 2px;
                      font:400 12px/1.5 {FONT};color:{GREY};">
            <div style="font-weight:600;letter-spacing:.04em;padding-bottom:6px;">EARLIER ALERTS, SAME SLUG</div>{rows}
          </div>"""

    return f"""
        <div style="border-left:3px solid {rule};padding:12px 14px;margin-bottom:18px;background:#fff;">
          <div style="font:400 12px/1.6 {FONT};color:{GREY};margin-bottom:8px;">{tags}{html.escape(_where(alert))}
            · <span style="{mono}">{html.escape(stories.readable(alert.slug))}</span></div>
          <div style="font:600 17px/1.45 {FONT};color:#111827;">{html.escape("News Alert! " + copy)}</div>{extra}
        </div>"""


def build_alerts(alerts: list) -> tuple:
    """One email for everything new in a check, newest first, as the wire reads."""
    alerts = sorted(alerts, key=stories.order, reverse=True)
    latest = alerts[0]
    lead = stories.clean(latest.text)[0] or latest.slug
    # Marks in front describe the headline they sit next to, never another
    # alert; what is further down the email is counted instead.
    marks = (URGENT_MARK + " " if latest.urgent else "") + (label(latest) + " · " if label(latest) else "")
    if len(alerts) > 1:
        others = alerts[1:]
        counts = [(sum(a.urgent for a in others), "URG")] + \
            [(sum(label(a) == k for a in others), k.lower()) for k in ("CORRECTED", "RE-FILED")]
        inside = [f"{n} {name}" for n, name in counts if n]
        marks = f"{len(alerts)} alerts{' (' + ', '.join(inside) + ' below)' if inside else ''}: " + marks
    # About 80 characters in all, marks included; the alert is cut to fit.
    subject = marks + _short(lead, max(SUBJECT_CHARS - len(marks), 40))

    # The line Gmail shows after the subject in the inbox.
    if len(alerts) == 1:
        preview = _where(latest)
        if latest.replaces is not None:
            preview += f" · earlier version {clock(latest.replaces.filed)}"
    else:
        preview = " | ".join(f"{clock(a.filed)} {stories.clean(a.text)[0]}" for a in alerts[1:])
    hidden = (f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
              f'{html.escape(preview)}{"&nbsp;&zwnj;" * 60}</div>')

    # Several new alerts on one story: its earlier alerts are listed once,
    # under the last card of that story (the oldest of the new ones).
    last_of_story = {stories.key(a.slug): a.id for a in alerts}
    cards = [_card(a, show_earlier=last_of_story[stories.key(a.slug)] == a.id) for a in alerts]

    body = f"""{hidden}<div style="max-width:640px;margin:0 auto;padding:16px 12px;background:#fff;">
      {''.join(cards)}
    </div>"""
    return subject, body


def build_notice(headline: str, paragraphs: list) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<div style="max-width:600px;margin:0 auto;padding:24px;
                font:400 15px/1.6 {FONT};color:#222;">
        <p><strong>{html.escape(headline)}</strong></p>{body}</div>"""
