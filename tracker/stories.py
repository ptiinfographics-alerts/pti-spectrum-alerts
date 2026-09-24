"""How a new alert relates to the alerts already on the wire today.

PTI's own numbering ("EC-CJP 2") is not reliable enough to state as fact --
numbers are skipped, repeated and dropped -- so nothing here counts alerts.
It only groups alerts that carry the same slug, and compares their words:

  repeat      exactly the same text as an alert already filed on this slug,
              at the same or lower priority. Not sent: it tells the desk
              nothing, and invites a double post. Anything that differs at
              all -- a comma, a quote mark, PRI raised to URG -- is sent,
              because the later version is the one that stands.
  correction  marked "(CORRECTED)" / "Eds: corrects", or re-filed under the
              same item number with different words (PTI often fixes a typo
              or a name this way without saying so). Sent, clearly marked,
              with the version it replaces.
  earlier     other alerts on the same slug, filed before. Shown underneath,
              clearly labelled as old.
"""

import difflib
import re

SIMILAR = 0.80          # this close in wording, on the same slug = a re-file
EARLIER_SHOWN = 3

_DESK = re.compile(r"^\s*([A-Z]{3})(\d+)\s*-\s*", re.I)
_CORRECTED = re.compile(r"\(\s*CORRECTED\s*\)", re.I)
_EDS = re.compile(r"\(\s*Eds?\s*:([^)]*)\)", re.I)
# "Bengaluru, Sep 22 (PTI)" -- where a full story's body begins.
_STORY_DATELINE = re.compile(r"\s[A-Z][A-Za-z. ]{1,30},\s+[A-Z][a-z]{2,8}\.?\s+\d{1,2}\s+\(PTI\)")


def desk(slug: str) -> tuple:
    """("DEL", 34) from DEL034-NEWSALERT-EC-CJP 2."""
    match = _DESK.match(slug)
    return (match[1].upper(), int(match[2])) if match else ("", 0)


def _tail(slug: str) -> str:
    """The slug without its desk code, NEWSALERT or a (CORRECTED) marker."""
    rest = _DESK.sub("", slug, count=1)
    rest = re.sub(r"^\s*NEWSALERT\s*-?\s*", "", rest, flags=re.I)
    return _CORRECTED.sub("", rest).strip(" -")


def key(slug: str) -> str:
    """The story an alert belongs to: its slug without the desk code, any
    trailing number, LD/2NDLD, or (CORRECTED). EC-CJP and EC-CJP 2 match."""
    k = re.sub(r"[\s-]+\d{1,2}$", "", _tail(slug).upper())
    k = re.sub(r"\b(\d+(ST|ND|RD|TH))?LD\b", "", k)
    return re.sub(r"[^A-Z0-9]", "", k)


def readable(slug: str) -> str:
    """DEL034 · EC-CJP 2 -- still searchable on the wire, easier to read."""
    code, number = desk(slug)
    tail = _tail(slug)
    marker = " (corrected)" if _CORRECTED.search(slug) else ""
    return f"{code}{number:03d} · {tail}{marker}" if code else tail + marker


def order(alert) -> tuple:
    return (alert.filed, desk(alert.slug)[1], alert.id)


def clean(text: str) -> tuple:
    """(copy, notes, was_story)

    copy       the alert's words alone, ready to paste after "News Alert!"
    notes      editors' notes from the wire, e.g. "CORRECTS TEAM"
    was_story  True when a full story was filed on an alert slug, in which
               case only its headline is kept
    """
    notes = [" ".join(n.split()) for n in _EDS.findall(text)]
    body = " ".join(_EDS.sub(" ", text).split())
    match = _STORY_DATELINE.search(" " + body)
    was_story = bool(match)
    if match:
        body = (" " + body)[:match.start()].strip()
    return body, notes, was_story


def _exact(text: str) -> str:
    return " ".join(clean(text)[0].split())


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", clean(text)[0].lower()))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _words(a), _words(b)).ratio()


def changed_words(old: str, new: str) -> list:
    """The old version as [(word, changed?)], so the email can highlight what
    the correction altered."""
    old_words, new_words = clean(old)[0].split(), clean(new)[0].split()
    marked = []
    matcher = difflib.SequenceMatcher(None, [w.lower() for w in old_words],
                                      [w.lower() for w in new_words])
    for op, i1, i2, _, _ in matcher.get_opcodes():
        marked += [(w, op != "equal") for w in old_words[i1:i2]]
    return marked


def is_marked_correction(alert) -> bool:
    return bool(_CORRECTED.search(alert.slug)) or \
        any(n.lower().startswith("correct") for n in clean(alert.text)[1])


def relate(alert, prior: list) -> str:
    """Sets alert.replaces and alert.earlier from the alerts filed before it
    on the same story (with their text already read). Returns "repeat",
    "correction" or "new"."""
    prior = sorted(prior, key=order)
    if any(_exact(p.text) == _exact(alert.text) and (p.urgent or not alert.urgent)
           for p in prior):
        return "repeat"
    marked = is_marked_correction(alert)
    # PTI re-files a fixed alert under the SAME item number (SPF038 twice);
    # a genuine update gets a new number. So an unmarked alert only counts as
    # a correction when it re-uses the number: a running score filed under
    # new numbers must never be called a correction of the one before.
    same = [p for p in prior if desk(p.slug) == desk(alert.slug)
            and _tail(p.slug).upper() == _tail(alert.slug).upper()]
    replaces = same[-1] if same else None
    if replaces is None and marked and prior:
        # Marked as a correction, but filed under a new number: the closest
        # wording on the same story is the one it corrects.
        best = max(prior, key=lambda p: similarity(p.text, alert.text))
        replaces = best if similarity(best.text, alert.text) >= SIMILAR else None
    alert.replaces = replaces
    alert.earlier = [p for p in prior if p is not replaces][-EARLIER_SHOWN:]
    return "correction" if (marked or replaces) else "new"
