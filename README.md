# PTI Alerts

Watches the PTI wire on Spectrum around the clock and emails every **news
alert** to the social media desk, usually within a minute or two of PTI
filing it. Free to run, forever.

## What it sends

PTI files its alerts, the one-line flashes that run ahead of a story, with
`NEWSALERT` in the slug:

    DEL012-NEWSALERT-EXAMPLE-SLUG 2$URG 1213 24092026
    (listing line: slug, priority, time filed, date filed)

Every one of them is sent, and nothing else is. That is 60 to 80 a day. Each
check that finds something sends **one** email with everything new, in the
order PTI filed it.

What the desk sees. The email states what is on the wire and shows the
evidence; it does not say what anything means or what to do about it.

- **The copy line.** Each alert reads `News Alert! <the alert's words>` and
  nothing else, ready to paste. PTI's editors' notes, such as
  "(Eds: CORRECTS TEAM)", are shown verbatim underneath.
- **`URG`** in the email, and 🔴 at the start of the subject, when PTI filed
  the alert as urgent (`$URG` on the wire rather than `$PRI`). PTI's own
  priority, not the tracker's judgement.
- **`CORRECTED`** when PTI itself marked the alert `(CORRECTED)` or
  "Eds: corrects".
- **`RE-FILED`** when the same item number was filed again with different
  words, which is how PTI often fixes a typo or a name without marking it.
- For both, the **earlier version** is shown with its time and slug, and the
  words that differ are highlighted. "Not emailed" is added when the tracker
  held the earlier version back because both arrived in the same check.
- **Earlier alerts, same slug**, in a grey box underneath, newest first, up
  to ten. Each shows the time it was filed and its own item code (DEL060).
- The alert's own slug sits on the top line, beside its time.
- **The later version stands.** An alert and its re-file in the same check:
  only the later one is sent, with the earlier shown as above. An exact
  repeat (identical text, same or lower priority) is not sent. Any
  difference at all, even a comma or PRI raised to URG, is sent.
- **Newest first.** An email with several alerts lists the latest at the top.
- **Subjects run to about 80 characters**, cut between words. Marks belong to
  the headline they sit next to. Alerts further down are counted:
  "3 alerts (1 URG, 1 re-filed below): ...".

Nothing counts alerts ("2nd alert on this story"). PTI's numbering in slugs
skips, repeats and drops numbers too often to state as fact. Alerts are
grouped only by matching slugs (`stories.py`).

The sender's picture in Gmail is the profile photo of the sending Google
account. `assets/profile-photo.png` is the navy bell used for it.

## How it runs

| | |
|---|---|
| **Where** | GitHub Actions, in a *public* repository. Public repositories get unlimited free minutes; a private one would run out in a week. |
| **How often** | Every minute. That matches Spectrum's own page, which refreshes once a minute. |
| **How it stays on** | One run lasts just under six hours, GitHub's limit for a single job. A minute in, it queues its successor, which starts the moment it ends. `keepalive.yml` checks every ten minutes and restarts the chain if it ever breaks. |
| **What it remembers** | `state.json` holds Spectrum's story ids only, which alerts have already been sent. It is saved to the repository at most every ten minutes. |

**Nothing from the wire is ever made public.** The repository and its run
logs can be seen by anyone, so the logs show only counts and times, and
`state.json` holds only ids. The Spectrum and Gmail passwords are GitHub
secrets, which nobody can read, including the repository's owner.

## How a check works

1. Signs in to Spectrum with the account in the secrets (a fresh session
   every hour).
2. Asks Spectrum's own search for `NEWSALERT` on today's date. This takes
   about 20 KB and under a second. For the first half hour after midnight it
   also searches yesterday, so an alert filed at 23:59 is not missed.
3. Every 30 minutes it also reads the full day's listing as a safety net.
   Spectrum's search and its listing define "a day" differently: the
   listing's day runs from about 2 AM to 2 AM.
4. Opens each new alert for its text, then sends one email.
5. If an email fails, those alerts are not marked as sent. The next check
   tries again.

## When something goes wrong

The admin (`ADMIN_MAIL_TO`, or `MAIL_TO` if that is not set) is emailed,
at most once every six hours for each problem:

- **Spectrum refused the login.** The password has probably changed. Update
  the `SPECTRUM_PASSWORD` secret.
- **Spectrum cannot be reached** for 15 minutes or more. A second email
  follows when it comes back. Anything filed in the last three hours goes out
  then. Older alerts are dropped rather than sent hours late.
- **Gmail's daily limit is getting close.** Gmail allows about 500
  recipients a day, and an email to four people counts as four. Past 350,
  alerts are grouped into one email every 15 minutes. Past 470 they wait. A
  single Google Group address counts as one recipient, so a group avoids
  this entirely.

If a run crashes outright, keepalive starts a new one within about ten
minutes. The crashed run shows in red in the Actions tab. GitHub may not
email anyone about it, because these runs are started by GitHub's own bot
rather than by a person.

## Settings

Six secrets, in the repository's **Settings → Secrets and variables →
Actions → New repository secret**:

| Secret | What it is |
|---|---|
| `SPECTRUM_USER` | The Spectrum username |
| `SPECTRUM_PASSWORD` | The Spectrum password |
| `GMAIL_USER` | The Gmail address that sends the alerts |
| `GMAIL_APP_PASSWORD` | That account's 16-character app password (not its normal password) |
| `MAIL_TO` | Who gets the alerts. Several addresses can be given, with commas between them |
| `ADMIN_MAIL_TO` | Optional. Who hears about problems |

## Running it by hand

From the repository's **Actions** tab, open **PTI Alerts → Run workflow**:

- **test-email**: one sample alert to `MAIL_TO`. It proves the Gmail
  settings.
- **dry-run**: one real check. It prints how many alerts it would send and
  sends nothing.
- **normal**: starts the loop, if it is not running already.

To put a change live at once, rather than at the next six-hourly hand-over,
run `tools/deploy.sh` on the Mac. It pushes the code, stops the running loop
(which saves what it has sent first) and starts a fresh one.

On a Mac, `tools/try_local.sh` does a dry run from this computer and shows
the alerts themselves. The password is typed blind and is never stored.

## The files

| File | What it does |
|---|---|
| `tracker/spectrum.py` | Signs in to Spectrum and reads alerts |
| `tracker/stories.py` | Groups alerts by slug and spots corrections and repeats |
| `tracker/mailer.py` | Builds and sends the email |
| `tracker/run.py` | The loop: checks, remembers, sends, reports problems |
| `.github/workflows/alerts.yml` | Runs the loop on GitHub |
| `.github/workflows/keepalive.yml` | Restarts the loop if it stops |
| `state.json` | Which alerts have been sent. Written by the robot |
| `tools/probe_spectrum.py` | The one-off script used to learn Spectrum's layout |

## Worth knowing

- **If Spectrum ever refuses GitHub's servers** (some Indian sites refuse
  every data-centre connection), the fix is to run the same code on an
  always-on computer on an ordinary broadband line, using
  `python -m tracker.run --loop 1440` under launchd or cron.
- **GitHub's terms** expect Actions to be used for work related to the
  repository's software. The existing news tracker relies on the same
  arrangement, but GitHub could object to it. The broadband option above is
  the fallback.
- **Spectrum's data services answer without a login.** The tracker signs in
  anyway and uses its own session, so it keeps working if PTI closes this
  gap. PTI's IT team may want to know about it.
