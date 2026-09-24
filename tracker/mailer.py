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


def _where(alert) -> str:
    place = alert.dateline.title() if alert.dateline else ""
    return f"{place + ' · ' if place else ''}{alert.filed:%H:%M} IST"


def _card(alert, show_earlier: bool = True) -> str:
    correction = alert.kind == "correction"
    tags = (_tag("URG", RED_WASH, RED_INK) if alert.urgent else "") + \
        (_tag("CORRECTION", AMBER_WASH, AMBER_INK) if correction else "")
    copy, notes, was_story = stories.clean(alert.text)
    rule = AMBER if correction else (RED if alert.urgent else NAVY)

    extra = ""
    if notes:
        extra += (f'<div style="font:400 12px/1.5 {FONT};color:{AMBER_INK};margin-top:6px;">'
                  f'PTI editor\'s note: {html.escape("; ".join(notes))}</div>')
    if was_story:
        extra += (f'<div style="font:400 12px/1.5 {FONT};color:{GREY};margin-top:6px;">'
                  f'Filed as a full story under an alert slug: only its headline is shown.</div>')

    if correction:
        old = alert.replaces
        if old is not None:
            marked = " ".join(
                f'<span style="background:#F7C1C1;color:{RED_INK};">{html.escape(w)}</span>'
                if changed else html.escape(w)
                for w, changed in stories.changed_words(old.text, alert.text))
            said = "PTI corrected" if stories.is_marked_correction(alert) else "PTI re-filed with changes"
            extra += f"""
          <div style="background:{AMBER_WASH};border-radius:6px;padding:10px 12px;margin-top:12px;
                      font:400 13px/1.5 {FONT};color:{AMBER_INK};">
            <strong>{said}: this replaces the {old.filed:%H:%M} alert.</strong>
            If the earlier version was posted, it needs fixing. What changed is highlighted:
            <div style="color:{GREY};margin-top:6px;text-decoration:line-through;">{marked}</div>
          </div>"""
        else:
            extra += f"""
          <div style="background:{AMBER_WASH};border-radius:6px;padding:10px 12px;margin-top:12px;
                      font:400 13px/1.5 {FONT};color:{AMBER_INK};">
            <strong>PTI marked this as a correction.</strong> The alert it corrects
            could not be found today; check the wire.</div>"""

    if alert.earlier and show_earlier:
        rows = "".join(
            f'<div style="margin-top:4px;"><span style="font-variant-numeric:tabular-nums;">'
            f'{e.filed:%H:%M}</span> &nbsp;{html.escape(stories.clean(e.text)[0])}</div>'
            for e in alert.earlier)
        extra += f"""
          <div style="border-top:1px dashed #d1d5db;margin-top:12px;padding-top:8px;
                      font:400 12px/1.5 {FONT};color:#9ca3af;">
            <div style="font-weight:600;letter-spacing:.04em;">OLDER ALERTS ON THIS STORY:
              NOT NEW, ALREADY ON THE WIRE</div>{rows}
          </div>"""

    return f"""
        <div style="border-left:3px solid {rule};padding:12px 14px;margin-bottom:14px;background:#fff;">
          <div style="font:400 12px/1.4 {FONT};color:{GREY};margin-bottom:8px;">{tags}{html.escape(_where(alert))}</div>
          <div style="font:600 17px/1.45 {FONT};color:#111827;">{html.escape("News Alert! " + copy)}</div>{extra}
          <div style="font:400 11px/1.4 ui-monospace,Menlo,monospace;color:#9ca3af;margin-top:10px;">
            {html.escape(stories.readable(alert.slug))}</div>
        </div>"""


def build_alerts(alerts: list) -> tuple:
    """One email for everything new in a check, in the order PTI filed it."""
    alerts = sorted(alerts, key=stories.order)
    latest = alerts[-1]
    lead = stories.clean(latest.text)[0] or latest.slug
    marks = ("URG" if any(a.urgent for a in alerts) else "",
             "CORRECTION" if any(a.kind == "correction" for a in alerts) else "")
    prefix = f"[{' · '.join(m for m in marks if m)}] " if any(marks) else ""
    subject = prefix + (lead if len(alerts) == 1 else f"{len(alerts)} alerts: {lead}")

    # The line Gmail shows after the subject in the inbox.
    if len(alerts) == 1:
        preview = _where(latest)
        if latest.kind == "correction" and latest.replaces is not None:
            preview += f" · replaces the {latest.replaces.filed:%H:%M} alert"
    else:
        preview = " | ".join(f"{a.filed:%H:%M} {stories.clean(a.text)[0]}" for a in reversed(alerts[:-1]))
    hidden = (f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
              f'{html.escape(preview)}{"&nbsp;&zwnj;" * 60}</div>')

    # Several new alerts on one story: its older alerts are listed once, under
    # the first of them, rather than repeated under each.
    cards, listed = [], set()
    for a in alerts:
        cards.append(_card(a, show_earlier=stories.key(a.slug) not in listed))
        listed.add(stories.key(a.slug))

    body = f"""{hidden}<div style="max-width:640px;margin:0 auto;padding:16px 12px;background:#fff;">
      {''.join(cards)}
    </div>"""
    return subject[:180], body


def build_notice(headline: str, paragraphs: list) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<div style="max-width:600px;margin:0 auto;padding:24px;
                font:400 15px/1.6 {FONT};color:#222;">
        <p><strong>{html.escape(headline)}</strong></p>{body}</div>"""
