"""One-off: signs in to PTI Spectrum and saves what it sees, so the real
tracker can be built from the site's actual layout.

    python3 tools/probe_spectrum.py

Asks for the username and password (the password is not shown as you type),
signs in, and saves the pages it lands on into probe_output/. It only READS:
it never follows a link that looks like logout, delete, save or send.
"""

import getpass
import json
import pathlib
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://editorial.pti.in/spectrum/"
LOGIN = BASE + "Login.aspx"
OUT = pathlib.Path(__file__).resolve().parent.parent / "probe_output"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
# Never followed: anything that could sign the account out or change something.
UNSAFE = re.compile(r"logout|log_out|signout|sign_out|logoff|delete|remove|save|send|"
                    r"password|javascript:|mailto:", re.I)
MAX_PAGES = 12


def hidden_fields(soup):
    return {i.get("name"): i.get("value", "") for i in soup.select("input[type=hidden]")
            if i.get("name")}


def describe(name, response, soup):
    """What a page is made of, without its wording: links, forms, frames,
    scripts, and any addresses the page's own scripts call for data."""
    scripts = " ".join(s.get_text() for s in soup.find_all("script"))
    return {
        "name": name,
        "url": response.url,
        "status": response.status_code,
        "title": soup.title.get_text(strip=True) if soup.title else "",
        "meta_refresh": [m.get("content") for m in soup.find_all("meta", attrs={"http-equiv": re.compile("refresh", re.I)})],
        "forms": [{"id": f.get("id"), "action": f.get("action"), "method": f.get("method"),
                   "fields": [(i.get("name"), i.get("type")) for i in f.find_all(["input", "select", "textarea"])
                              if i.get("name") and not i.get("name", "").startswith("__")]}
                  for f in soup.find_all("form")],
        "frames": [f.get("src") for f in soup.find_all(["iframe", "frame"])],
        "script_src": [s.get("src") for s in soup.find_all("script") if s.get("src")],
        "data_calls": sorted(set(re.findall(r"""['"]([^'"\s]*(?:\.asmx|\.ashx|\.svc|\.aspx/\w+|/api/)[^'"\s]*)['"]""", scripts))),
        "timers": re.findall(r"set(?:Interval|Timeout)\([^;]{0,120}", scripts)[:10],
        "links": [(a.get("href"), " ".join(a.get_text(" ", strip=True).split())[:80])
                  for a in soup.find_all("a") if a.get("href")][:300],
        "tables": [{"id": t.get("id"), "rows": len(t.find_all("tr"))} for t in soup.find_all("table")],
        "bytes": len(response.content),
    }


def main() -> int:
    user = input("Spectrum username: ").strip()
    password = getpass.getpass("Spectrum password (nothing appears as you type): ")
    OUT.mkdir(exist_ok=True)

    s = requests.Session()
    s.headers["User-Agent"] = UA
    page = s.get(LOGIN, timeout=30)
    page.raise_for_status()
    fields = hidden_fields(BeautifulSoup(page.text, "html.parser"))
    fields.update({"inputuser": user, "inputPassword": password})
    after = s.post(LOGIN, data=fields, timeout=30, headers={"Referer": LOGIN})
    soup = BeautifulSoup(after.text, "html.parser")
    wrong = soup.find(id="lblwrong")
    if "login.aspx" in after.url.lower() and soup.find(id="inputPassword"):
        print(f"Sign-in did not work. The page said: "
              f"{wrong.get_text(' ', strip=True) if wrong else '(nothing)'}")
        (OUT / "00_login_failed.html").write_text(after.text)
        return 1
    print(f"Signed in. Landed on {after.url}")

    report = [describe("00_landing", after, soup)]
    (OUT / "00_landing.html").write_text(after.text)
    report[-1]["cookies"] = sorted(c.name for c in s.cookies)
    report[-1]["history"] = [(r.status_code, r.headers.get("Location")) for r in after.history]

    # Then every page the landing page links to, plus any frames, on this site only.
    queue = [f for f in report[0]["frames"] if f] + [h for h, _ in report[0]["links"]]
    seen = {after.url}
    n = 1
    for href in queue:
        if n >= MAX_PAGES:
            break
        url = urljoin(after.url, href)
        if (urlparse(url).netloc != "editorial.pti.in" or url in seen
                or UNSAFE.search(url) or url.endswith(("#", ".css", ".js", ".png", ".jpg"))):
            continue
        seen.add(url)
        time.sleep(1)
        try:
            r = s.get(url, timeout=30)
        except requests.RequestException as exc:
            print(f"  could not open {url}: {exc}")
            continue
        name = f"{n:02d}_" + re.sub(r"[^A-Za-z0-9]+", "_", urlparse(url).path + urlparse(url).query)[-60:]
        (OUT / f"{name}.html").write_text(r.text)
        report.append(describe(name, r, BeautifulSoup(r.text, "html.parser")))
        print(f"  saved {name}  ({r.status_code}, {len(r.content):,} bytes)")
        n += 1

    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    print(f"\nDone. {n} page(s) saved in {OUT}")
    print("Nothing here signs the account out. Tell Claude it has finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
