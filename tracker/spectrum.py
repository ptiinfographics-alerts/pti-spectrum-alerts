"""Reads PTI's wire from Spectrum (editorial.pti.in/spectrum).

Spectrum is one page, Details.aspx, that fills itself in by calling four
small data services on the same page:

    getstorysrch   stories whose slug or text contain a word, for one date
    getstorybycat  every story in one "edition", for one date
    getstorybystid one story's full text
    getdatabydate  the category list (not needed here)

Each listing line reads   SLUG$PRIORITY HHMM DDMMYYYY
for example               DEL012-NEWSALERT-EXAMPLE-SLUG 2$URG 1213 24092026

PTI files its alerts -- the one-line flashes that run ahead of the story --
with NEWSALERT in the slug. That word is what this tracker looks for.

Two kinds of "date" matter, and they differ:
  * search goes by CALENDAR date (midnight to midnight, IST);
  * the full listing goes by EDITION, which runs from about 2 AM to 2 AM,
    so an alert filed at 00:07 is in the previous day's edition.
Searching today's calendar date (plus yesterday's just after midnight)
catches everything; the full listing is a slower safety net on top.
"""

import dataclasses
import datetime as dt
import re
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://editorial.pti.in/spectrum/"
LOGIN = BASE + "Login.aspx"
DETAILS = BASE + "Details.aspx"
SERVICE = BASE + "DETAILS.aspx/"
# The publication code Spectrum puts in the page for this account.
PUBCODE = "2"
MARKER = "NEWSALERT"

IST = ZoneInfo("Asia/Kolkata")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
TIMEOUT = 30

_ROW = re.compile(r"storyidmatter\('([0-9a-fA-F]+)'\)")
_LINE = re.compile(r"^(?P<slug>.*)\$(?P<priority>[A-Z]{2,4})\s+(?P<hhmm>\d{4})\s+(?P<ddmmyyyy>\d{8})\s*$")


class SignInFailed(RuntimeError):
    """Spectrum refused the username or password."""


@dataclasses.dataclass
class Alert:
    id: str              # Spectrum's own story id, a 32-character hex string
    slug: str            # DEL012-NEWSALERT-EXAMPLE-SLUG 2
    priority: str        # URG or PRI
    filed: dt.datetime   # when PTI filed it, IST
    text: str = ""       # the alert itself, filled in by read()
    dateline: str = ""   # KOLKATA
    # Set by stories.relate(): the alert this one corrects, and older
    # alerts on the same story.
    replaces: "Alert | None" = None
    earlier: list = dataclasses.field(default_factory=list)
    kind: str = "new"    # new, correction or repeat
    held: bool = False   # not emailed: a re-file arrived in the same check

    @property
    def urgent(self) -> bool:
        return self.priority == "URG"


class Spectrum:
    def __init__(self, username: str, password: str):
        self.username, self.password = username, password
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.signed_in_at = None
        # Texts already read in this run, by story id. Memory only: nothing
        # from the wire is ever written to disk on GitHub.
        self._texts = {}

    # ----------------------------------------------------------- signing in

    def sign_in(self) -> None:
        """The ordinary login form: its hidden ASP.NET fields plus the two
        boxes. Success is a redirect to Details.aspx; failure comes back to
        the login page with the form still on it."""
        self.session.cookies.clear()
        page = self.session.get(LOGIN, timeout=TIMEOUT)
        page.raise_for_status()
        form = {i.get("name"): i.get("value", "")
                for i in BeautifulSoup(page.text, "html.parser").select("input[type=hidden]")
                if i.get("name")}
        form.update({"inputuser": self.username, "inputPassword": self.password})
        after = self.session.post(LOGIN, data=form, timeout=TIMEOUT, headers={"Referer": LOGIN})
        after.raise_for_status()
        if "login.aspx" in after.url.lower() or 'id="inputPassword"' in after.text:
            said = BeautifulSoup(after.text, "html.parser").find(id="lblwrong")
            said = said.get_text(" ", strip=True) if said else ""
            raise SignInFailed(f"Spectrum did not accept the login{': ' + said if said else ''}")
        self.signed_in_at = dt.datetime.now(dt.timezone.utc)

    def _call(self, service: str, body: str) -> str:
        """One data service. They take a JSON-ish body and answer {"d": html}."""
        response = self.session.post(
            SERVICE + service, data=body, timeout=TIMEOUT,
            headers={"Content-Type": "application/json; charset=utf-8",
                     "X-Requested-With": "XMLHttpRequest", "Referer": DETAILS})
        response.raise_for_status()
        try:
            return response.json()["d"] or ""
        except (ValueError, KeyError, TypeError):
            raise RuntimeError(f"{service} did not answer with data "
                               f"(HTTP {response.status_code}, {len(response.content)} bytes)")

    # --------------------------------------------------------------- lists

    @staticmethod
    def _rows(fragment: str) -> list:
        """Every alert line in a listing, as Alerts without their text yet."""
        found = []
        for div in BeautifulSoup(fragment, "html.parser").find_all("div", onclick=_ROW):
            match = _LINE.match(div.get_text(" ", strip=True))
            if not match or MARKER not in match["slug"].upper():
                continue
            filed = dt.datetime.strptime(match["ddmmyyyy"] + match["hhmm"], "%d%m%Y%H%M")
            found.append(Alert(id=_ROW.search(div["onclick"]).group(1).lower(),
                               slug=" ".join(match["slug"].split()),
                               priority=match["priority"],
                               filed=filed.replace(tzinfo=IST)))
        return found

    def search(self, day: dt.date) -> list:
        """Alerts on one calendar day, by Spectrum's own search. Small and fast."""
        return self._rows(self._call(
            "getstorysrch",
            f"{{ Param1: '{day:%Y-%m-%d}',Param2: '{MARKER}' ,Param3: 'All Category',Param4: '{PUBCODE}'}}"))

    def edition(self, day: dt.date) -> list:
        """Alerts in one whole edition, from the full listing. Several hundred
        kilobytes by evening, so it is the safety net rather than the main check."""
        return self._rows(self._call(
            "getstorybycat", f"{{ Param1: '{day:%Y-%m-%d}',Param2: '', Param3: '{PUBCODE}'}}"))

    # ---------------------------------------------------------------- text

    def read(self, alert: Alert) -> None:
        """Fills in the alert's own words.

        A story comes back as the raw wire message plus two extra fields:
            ZCZC / URG GEN NAT / .KOLKATA CAL12 / <slug> / <text> PTI BSM / ... / NNNN
            ~$head$~<listing line>~$head$~<word count>
        """
        if alert.id not in self._texts:
            raw = self._call("getstorybystid",
                             f"{{ Param1: '{alert.filed:%Y-%m-%d}',Param2: '{alert.id}',Param3: '{PUBCODE}'}}")
            message = BeautifulSoup(raw.split("~$head$~")[0], "html.parser").get_text("\n")
            if len(self._texts) > 2000:
                self._texts.clear()
            self._texts[alert.id] = parse_message(message, alert.slug)
        alert.text, alert.dateline = self._texts[alert.id]


# When in doubt, the text is kept. A stray "PTI GK" at the end costs the desk
# a second to delete; a word cut from the alert may never be noticed. So
# each rule below removes something only where it cannot be the alert.

# A line holding only the sign-off or desk initials: "PTI GMS SSK", "GK".
_SIGN_OFF_LINE = re.compile(r"(PTI\s+)?[A-Z]{1,5}(\s+[A-Z]{1,5})*")
# Where a sentence has plainly ended.
_CLOSED = re.compile(r"[.!?'\"\u2019\u201d)]$")
# The sign-off at the very end of the last line, straight after the
# sentence has ended: "... Adani. PTI BSM", "... reports AP. PTI",
# "... early trade. PTI DRR." Because it must follow closing punctuation,
# "... Minister told PTI" or "... to PTI." at the end of an alert is left
# alone -- there a word, not a full stop, comes before PTI.
_SIGN_OFF_END = re.compile(r"(?<=[.!?'\"\u2019\u201d)])\s+PTI(\s+[A-Z]{1,5})*\.?\s*$")


def _finished(line: str) -> bool:
    """Has the text plainly ended by the end of this line?"""
    return bool(_CLOSED.search(line) or _SIGN_OFF_END.search(line)
                or re.fullmatch(r"PTI(\s+[A-Z]{1,5})*\.?", line))


def parse_message(message: str, slug: str = "") -> tuple:
    """(text, dateline) from one raw wire message.

    Every alert message has the same shape, and the text is found by that
    shape, not by looking for the word PTI inside it:

        ZCZC
        URG GEN NAT                    priority and category
        .KOLKATA CAL12                 dateline
        NEWSALERT-WB-...               slug
        <the alert>. PTI BSM           text, usually ending in the sign-off
        ACD                            desk initials (sometimes PTI XX alone)
        09241213                       time filed
        NNNN                           end of message
    """
    lines = [line.strip() for line in message.replace("\r", "\n").split("\n")]
    lines = [line for line in lines if line]
    dateline = ""
    body = []
    slug_tail = slug.split("-", 1)[-1].strip().upper() if slug else ""
    for line in lines:
        upper = line.upper()
        if upper == "ZCZC":
            continue
        if upper == "NNNN":
            break
        if re.fullmatch(r"(URG|PRI|FLS|BLN)\b.*", line) and not body:
            continue                                   # priority / category header
        if line.startswith(".") and not body:
            dateline = re.sub(r"\s+[A-Z]{2,4}\d+$", "", line[1:]).strip()
            continue                                   # .KOLKATA CAL12
        if not body and (MARKER in upper or (slug_tail and upper == slug_tail)):
            continue                                   # the slug again
        body.append(line)
    # The time filed: an 8-digit line, only when it is the last before NNNN.
    if len(body) > 1 and re.fullmatch(r"\d{8}", body[-1]):
        body.pop()
    # Initials and sign-off lines ("RD", "PTI GMS SSK") after the last line
    # of text -- dropped only if that line has plainly ended. A short
    # capitalised line after an unfinished one could be the end of the
    # alert itself ("... refers the matter to" / "CBI"), so it stays.
    last = max((i for i, line in enumerate(body) if not _SIGN_OFF_LINE.fullmatch(line)), default=0)
    if body and _finished(body[last]):
        del body[last + 1:]
    if body:
        body[-1] = _SIGN_OFF_END.sub("", body[-1]).rstrip()
    text = " ".join(" ".join(body).split())
    return text, DATELINES.get(dateline.upper(), dateline.upper())


# Datelines the wire runs together.
DATELINES = {"NEWDELHI": "NEW DELHI", "NAVIMUMBAI": "NAVI MUMBAI"}


def days_to_search(now: dt.datetime) -> list:
    """Today's calendar date, plus yesterday's for the first half hour after
    midnight, so an alert filed at 23:59 is still picked up."""
    today = now.astimezone(IST).date()
    if now.astimezone(IST).time() < dt.time(0, 30):
        return [today - dt.timedelta(days=1), today]
    return [today]


def editions_to_list(now: dt.datetime) -> list:
    """The edition(s) that could hold a recent alert. Editions turn over at
    about 2 AM, so until 3 AM the previous one is read as well."""
    local = now.astimezone(IST)
    today = local.date()
    if local.hour < 3:
        return [today - dt.timedelta(days=1), today]
    return [today]
