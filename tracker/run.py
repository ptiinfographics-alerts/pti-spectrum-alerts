"""The tracker. Checks Spectrum for new NEWSALERTs and emails them.

    python -m tracker.run                   one check, then stop
    python -m tracker.run --loop 340        check every minute for 340 minutes
    python -m tracker.run --dry-run         one check, print, send and save nothing
    python -m tracker.run --test-email      send one sample alert to MAIL_TO

On GitHub it runs as one long job that checks every minute for most of the
six hours GitHub allows a job, then hands over to the next run (see
.github/workflows/alerts.yml).

Nothing from the wire is ever printed or saved on GitHub: the repository and
its logs are public. The log shows counts and times only, and state.json
holds nothing but Spectrum's story ids.
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

import requests

from . import mailer, spectrum
from .spectrum import IST

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state.json"
ON_GITHUB = bool(os.environ.get("GITHUB_ACTIONS"))

EVERY_SECONDS = 60             # Spectrum's own page refreshes once a minute
FULL_LISTING_EVERY = 30 * 60   # the heavier safety-net check
SIGN_IN_EVERY = 60 * 60        # a fresh session every hour
SIGN_IN_RETRY = 5 * 60         # after a refused login: slow, not every minute
MAX_AGE_HOURS = 3              # after an outage, older alerts are not sent late
REMEMBER_DAYS = 4
SAVE_EVERY = 10 * 60           # how often progress is pushed to GitHub

# Gmail allows about 500 recipients a day. Each email counts once per person.
DAILY_LIMIT = 500
BATCH_ABOVE = 350              # past this, alerts are grouped every 15 minutes
BATCH_MINUTES = 15
HOLD_ABOVE = 470               # past this, hold until the count falls

DOWN_NOTICE_AFTER = 15 * 60    # Spectrum unreachable this long -> tell the admin
NOTICE_REPEAT = 6 * 60 * 60


def log(message: str) -> None:
    print(f"{dt.datetime.now(IST):%d %b %H:%M:%S} {message}", flush=True)


# ------------------------------------------------------------------ state

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def push_state(reason: str) -> None:
    """Commits state.json so the next run carries on from here. GitHub only."""
    if not ON_GITHUB:
        return
    try:
        subprocess.run(["git", "add", "state.json"], check=True, cwd=ROOT)
        if subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=ROOT).returncode == 0:
            return
        subprocess.run(["git", "commit", "--quiet", "-m", f"seen up to {dt.datetime.now(dt.timezone.utc):%FT%TZ}"],
                       check=True, cwd=ROOT)
        # Only this run ever writes; a failed push is simply tried again later.
        subprocess.run(["git", "push", "--quiet"], check=True, cwd=ROOT, timeout=60)
        log(f"progress saved ({reason})")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log(f"! could not save progress to GitHub ({exc}); will try again")


def recipients_today(state: dict, now: float) -> int:
    state["sent"] = [e for e in state.get("sent", []) if e["at"] > now - 86400]
    return sum(e["n"] for e in state["sent"])


# ---------------------------------------------------------------- notices

def notify_admin(state: dict, kind: str, headline: str, paragraphs: list, dry_run: bool) -> None:
    """A maintenance email, at most once per NOTICE_REPEAT for each kind."""
    now = time.time()
    notices = state.setdefault("notices", {})
    if now - notices.get(kind, 0) < NOTICE_REPEAT:
        return
    if dry_run:
        log(f"(dry run) would email the admin: {headline}")
        return
    try:
        n = mailer.send(f"PTI Alerts: {headline}", mailer.build_notice(headline, paragraphs),
                        env=mailer.ADMIN_ENV, name=mailer.MAINTENANCE_NAME)
        state.setdefault("sent", []).append({"at": int(now), "n": n})
        notices[kind] = now
        log(f"emailed the admin: {headline}")
    except Exception as exc:                                     # noqa: BLE001
        log(f"! could not email the admin ({exc})")


# ------------------------------------------------------------------ check

class Tracker:
    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self.state = load_state()
        self.site = spectrum.Spectrum(os.environ.get("SPECTRUM_USER", "").strip(),
                                      os.environ.get("SPECTRUM_PASSWORD", ""))
        self.last_full = 0.0
        self.last_sign_in_try = 0.0
        self.last_saved = time.time()
        self.dirty = False

    def ensure_signed_in(self) -> bool:
        now = time.time()
        fresh = self.site.signed_in_at and \
            (dt.datetime.now(dt.timezone.utc) - self.site.signed_in_at).total_seconds() < SIGN_IN_EVERY
        if fresh:
            return True
        if not self.site.signed_in_at and now - self.last_sign_in_try < SIGN_IN_RETRY:
            return False                         # refused recently; wait before retrying
        self.last_sign_in_try = now
        try:
            self.site.sign_in()
            if self.state.pop("signin_refused", None):
                log("signed in again after earlier refusals")
            return True
        except spectrum.SignInFailed as exc:
            self.site.signed_in_at = None
            self.state["signin_refused"] = int(now)
            log(f"! {exc}")
            notify_admin(self.state, "signin", "Spectrum refused the login", [
                "The tracker could not sign in to PTI Spectrum, so <strong>no alerts are "
                "being sent</strong>. It will keep trying every five minutes.",
                f"Spectrum said: {exc}",
                "If the password for the account has changed, update the "
                "<code>SPECTRUM_PASSWORD</code> secret in the GitHub repository "
                "(Settings &rarr; Secrets and variables &rarr; Actions)."], self.dry_run)
            return False

    def collect(self, now: dt.datetime) -> dict:
        found = {}
        for day in spectrum.days_to_search(now):
            for alert in self.site.search(day):
                found[alert.id] = alert
        if time.time() - self.last_full >= FULL_LISTING_EVERY:
            before = len(found)
            for edition in spectrum.editions_to_list(now):
                for alert in self.site.edition(edition):
                    found.setdefault(alert.id, alert)
            self.last_full = time.time()
            if len(found) > before:
                log(f"  the full listing found {len(found) - before} alert(s) search had missed")
        return found

    def check(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        if not self.ensure_signed_in():
            return
        found = self.collect(now)
        if self.state.pop("down_since", None):
            self.back_up()

        seen = self.state.setdefault("seen", {})
        first_ever = not seen and not self.state.get("started")
        new = [a for a in found.values() if a.id not in seen]
        if first_ever and not self.dry_run:
            # Everything is "new" the first time. Note it and stay quiet, or
            # the first email would be the whole day's alerts at once.
            log(f"first run: noting {len(new)} alert(s) already on the wire, sending nothing")
            for a in new:
                seen[a.id] = int(time.time())
            self.state["started"] = int(time.time())
            self.dirty = True
            return
        if first_ever:
            log("first run, dry: previewing what is listed now. A real first run sends nothing.")

        cutoff = now - dt.timedelta(hours=MAX_AGE_HOURS)
        stale = [a for a in new if a.filed < cutoff]
        new = [a for a in new if a.filed >= cutoff]
        for a in stale:
            seen[a.id] = int(time.time())
        if stale:
            log(f"  {len(stale)} alert(s) older than {MAX_AGE_HOURS}h noted, not sent")
            self.dirty = True
        if not new:
            log(f"checked: {len(found)} alert(s) today, nothing new")
            return

        ready = []
        for a in new:
            try:
                self.site.read(a)
                ready.append(a)
            except Exception as exc:                             # noqa: BLE001
                log(f"  ! could not read one alert ({str(exc)[:100]}); next check will retry")
        if not ready:
            return
        log(f"{len(ready)} new alert(s)")
        if not ON_GITHUB:
            for a in sorted(ready, key=lambda a: a.filed):
                log(f"  {a.priority} {a.filed:%H:%M} {a.dateline}: {a.text}")
        self.send(ready)

    def send(self, alerts: list) -> None:
        now = time.time()
        used = recipients_today(self.state, now)
        planned = len(mailer.recipients()) or 1
        if used + planned > HOLD_ABOVE:
            log(f"  holding: {used} of Gmail's {DAILY_LIMIT} daily recipients already used")
            return
        if used + planned > BATCH_ABOVE and now - self.state.get("last_email", 0) < BATCH_MINUTES * 60:
            log(f"  grouping: {used} recipients used today, next email in under {BATCH_MINUTES} min")
            return
        subject, body = mailer.build_alerts(alerts)
        if self.dry_run:
            log(f"  (dry run) would email {len(alerts)} alert(s)"
                + ("" if ON_GITHUB else f": {subject}"))
            return
        try:
            n = mailer.send(subject, body)
        except Exception as exc:                                 # noqa: BLE001
            # Not marked as seen, so the next check sends them.
            log(f"  ! the email could not be sent ({str(exc)[:160]}); retrying next check")
            return
        self.state.setdefault("sent", []).append({"at": int(now), "n": n})
        self.state["last_email"] = int(now)
        for a in alerts:
            self.state["seen"][a.id] = int(now)
        self.dirty = True
        lag = max((dt.datetime.now(dt.timezone.utc) - a.filed).total_seconds() / 60 for a in alerts)
        log(f"  emailed {len(alerts)} alert(s) to {n} recipient(s); oldest was filed {lag:.0f} min ago")
        if recipients_today(self.state, now) > BATCH_ABOVE and not self.state.get("warned_budget"):
            self.state["warned_budget"] = int(now)
            notify_admin(self.state, "budget", "Gmail's daily limit is getting close", [
                f"{recipients_today(self.state, now)} of Gmail's roughly {DAILY_LIMIT} "
                "recipients a day have been used in the last 24 hours.",
                f"Until the count falls, alerts are grouped into one email every "
                f"{BATCH_MINUTES} minutes. Past {HOLD_ABOVE} they are held back.",
                "To avoid this, send to a single Google Group address instead of "
                "several people: a group counts as one recipient."], False)

    def unreachable(self, exc: Exception) -> None:
        now = time.time()
        since = self.state.setdefault("down_since", int(now))
        self.dirty = True
        log(f"! Spectrum could not be reached: {str(exc)[:160]}")
        if now - since >= DOWN_NOTICE_AFTER:
            notify_admin(self.state, "down", "Spectrum cannot be reached", [
                f"Every check since {dt.datetime.fromtimestamp(since, IST):%H:%M IST, %d %b} "
                "has failed, so <strong>no alerts are being sent</strong>. The tracker "
                "keeps checking every minute and will pick up anything it missed from "
                f"the last {MAX_AGE_HOURS} hours as soon as Spectrum answers again.",
                f"The last error: <code>{str(exc)[:300]}</code>"], self.dry_run)

    def back_up(self) -> None:
        """Only if the admin was told it was down."""
        notices = self.state.get("notices", {})
        if notices.pop("down", None):
            notices.pop("up", None)
            notify_admin(self.state, "up", "Spectrum is answering again", [
                "Alerts are flowing again. Anything filed while it was unreachable "
                f"(up to {MAX_AGE_HOURS} hours back) goes out in the next email."], self.dry_run)

    def tidy(self) -> None:
        cutoff = time.time() - REMEMBER_DAYS * 86400
        self.state["seen"] = {k: v for k, v in self.state.get("seen", {}).items() if v >= cutoff}
        if self.state.get("warned_budget", 0) < time.time() - 86400:
            self.state.pop("warned_budget", None)
        self.state["last_check"] = int(time.time())

    def save(self, force: bool = False) -> None:
        if self.dry_run:
            return
        self.tidy()
        save_state(self.state)
        if self.dirty and (force or time.time() - self.last_saved >= SAVE_EVERY):
            push_state("final" if force else "periodic")
            self.last_saved = time.time()
            self.dirty = False


def hand_over() -> None:
    """Queues the next GitHub run, which starts the moment this one ends.

    Called only after this run has completed a check, so a run that crashes
    on start-up cannot chain into an endless series of crashing runs. If the
    chain ever breaks, keepalive.yml restarts it.
    """
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (ON_GITHUB and token and repo):
        return
    try:
        response = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/alerts.yml/dispatches",
            json={"ref": os.environ.get("GITHUB_REF_NAME") or "main"}, timeout=30,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        log("next run queued" if response.status_code == 204
            else f"! could not queue the next run ({response.status_code}); keepalive will")
    except requests.RequestException as exc:
        log(f"! could not queue the next run ({exc}); keepalive will")


def run(loop_minutes: float, dry_run: bool) -> int:
    tracker = Tracker(dry_run)
    if not tracker.site.username or not tracker.site.password:
        log("SPECTRUM_USER and SPECTRUM_PASSWORD are not set -- nothing to do.")
        return 1
    stop_at = time.monotonic() + loop_minutes * 60
    handed_over = False
    while True:
        started = time.monotonic()
        try:
            tracker.check()
        except Exception as exc:                                  # noqa: BLE001
            # Unreachable, or something unexpected such as a change to the
            # page: reported the same way. The loop must keep going, not die
            # on one bad answer.
            tracker.unreachable(exc)
        tracker.save()
        if not handed_over and loop_minutes:
            hand_over()
            handed_over = True
        if time.monotonic() + EVERY_SECONDS > stop_at:
            break
        time.sleep(max(1.0, EVERY_SECONDS - (time.monotonic() - started)))
    tracker.save(force=True)
    log("done")
    return 0


def test_email() -> int:
    now = dt.datetime.now(IST)
    sample = [spectrum.Alert(id="test", slug="DEL000-NEWSALERT-TEST", priority="URG", filed=now,
                             dateline="NEW DELHI",
                             text="Test: the PTI alerts tracker can send email to this address.")]
    subject, body = mailer.build_alerts(sample)
    n = mailer.send(subject, body)
    log(f"sent a test email to {n} recipient(s). Check every inbox on the list.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", type=float, default=0, metavar="MINUTES",
                        help="keep checking every minute for this many minutes")
    parser.add_argument("--dry-run", action="store_true", help="print only; send and save nothing")
    parser.add_argument("--test-email", action="store_true", help="send one sample alert and stop")
    args = parser.parse_args()
    if args.test_email:
        return test_email()
    return run(args.loop, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
