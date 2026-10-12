#!/usr/bin/env python3
"""Blackboard Learn scraper -> a Markdown digest Claude Code can turn into a cram plan.

Two passes per course, both riding on your real (SSO/Duo) browser session:

1. REST pass   - Blackboard's own /learn/api/public endpoints, called with the
                 cookies from your logged-in browser profile. Gives clean,
                 structured announcements, the full content tree (modules,
                 folders, syllabus, assignments), gradebook columns with due
                 dates, your own grade/submission status, and calendar items.
2. Click pass  - Headless Chromium opens every course page, expands every
                 collapsed folder/module, follows in-course links and dumps the
                 visible text. Catches anything the REST pass can't see (or
                 that your school's API permissions block).

Usage:
    python bbscrape.py login  --url https://blackboard.myschool.edu
    python bbscrape.py scrape [--course CS101 --course MATH2] [--no-crawl]

Output lands in ./bb_data/latest/ (digest.md is the entry point).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlencode, urljoin, urlparse

HERE = Path(__file__).resolve().parent
HOME = Path.home() / ".bbscrape"
PROFILE_DIR = HOME / "profile"
CONFIG_FILE = HOME / "config.json"
DATA_DIR = HERE / "bb_data"
STATE_FILE = DATA_DIR / "seen.json"

API = "/learn/api/public"
MAX_CONTENT_DEPTH = 12


# --------------------------------------------------------------------------- helpers


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    """Blackboard bodies are HTML; flatten to readable text and keep link targets."""
    if not raw:
        return ""
    parser = _TextExtractor()
    parser.feed(raw)
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    links = [l for l in dict.fromkeys(parser.links) if l.startswith("http")]
    if links:
        text += "\n\nLinks: " + ", ".join(links)
    return text


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_dt(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%a %b %d %Y %I:%M %p") if dt else "-"


def days_until(dt: datetime, now: datetime) -> str:
    delta = dt - now
    days = delta.total_seconds() / 86400
    if days < 0:
        return f"{-days:.1f}d OVERDUE"
    if days < 1:
        return f"{delta.total_seconds() / 3600:.0f}h"
    return f"{days:.1f}d"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:80] or "item"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# --------------------------------------------------------------------------- REST pass

Fetch = Callable[[str], tuple[int, Any]]


class BlackboardApi:
    """Thin client over Blackboard's public REST API.

    `fetch(path)` returns (status, parsed_json_or_None). In production it is
    backed by Playwright's request context (shares cookies with the logged-in
    browser profile); in tests it is a fake.
    """

    def __init__(self, fetch: Fetch, log: Callable[[str], None] = print):
        self.fetch = fetch
        self.log = log
        self.blocked: set[str] = set()

    def get(self, path: str, **params) -> Any | None:
        if params:
            path = f"{path}?{urlencode(params)}"
        status, body = self.fetch(path)
        if status == 200:
            return body
        if status in (401, 403):
            kind = re.sub(r"/[_\d]+[^/]*", "/{id}", path.split("?")[0])
            if kind not in self.blocked:
                self.log(f"  ! API refused {kind} ({status}); the click pass will cover it")
                self.blocked.add(kind)
        elif status != 404:
            self.log(f"  ! {path} -> HTTP {status}")
        return None

    def paged(self, path: str, **params) -> Iterator[dict]:
        body = self.get(path, **params)
        seen_pages = 0
        while body:
            yield from body.get("results", [])
            nxt = (body.get("paging") or {}).get("nextPage")
            seen_pages += 1
            if not nxt or seen_pages > 200:
                break
            body = self.get(nxt)

    # -- endpoints

    def me(self) -> dict | None:
        return self.get(f"{API}/v1/users/me")

    def my_courses(self, user_id: str) -> list[dict]:
        out = []
        for m in self.paged(f"{API}/v1/users/{user_id}/courses", expand="course"):
            course = m.get("course") or self.get(f"{API}/v3/courses/{m['courseId']}") or {}
            if not course:
                continue
            out.append({**course, "_membership": m})
        return out

    def announcements(self, cid: str) -> list[dict]:
        return list(self.paged(f"{API}/v1/courses/{cid}/announcements"))

    def content_tree(self, cid: str, parent: str | None = None, depth: int = 0) -> list[dict]:
        if depth > MAX_CONTENT_DEPTH:
            return []
        path = (
            f"{API}/v1/courses/{cid}/contents"
            if parent is None
            else f"{API}/v1/courses/{cid}/contents/{parent}/children"
        )
        items = []
        for item in self.paged(path):
            item["_depth"] = depth
            item["_children"] = (
                self.content_tree(cid, item["id"], depth + 1) if item.get("hasChildren") else []
            )
            handler = (item.get("contentHandler") or {}).get("id", "")
            if handler in ("resource/x-bb-file", "resource/x-bb-document", "resource/x-bb-assignment",
                           "resource/x-bb-syllabus", "resource/x-bb-lesson"):
                item["_attachments"] = list(
                    self.paged(f"{API}/v1/courses/{cid}/contents/{item['id']}/attachments")
                )
            items.append(item)
        return items

    def gradebook(self, cid: str, user_id: str) -> list[dict]:
        cols = list(self.paged(f"{API}/v2/courses/{cid}/gradebook/columns"))
        mine = {
            g.get("columnId"): g
            for g in self.paged(f"{API}/v2/courses/{cid}/gradebook/users/{user_id}")
        }
        for col in cols:
            grade = col["_grade"] = mine.get(col["id"])
            graded = grade and (grade.get("score") is not None or grade.get("text"))
            if (col.get("grading") or {}).get("due") and not graded:
                # None = API wouldn't tell us (unknown); [] = genuinely no attempts.
                body = self.get(f"{API}/v2/courses/{cid}/gradebook/columns/{col['id']}/attempts")
                col["_attempts"] = body.get("results", []) if body is not None else None
        return cols

    def calendar(self, start: datetime, end: datetime) -> list[dict]:
        # The API caps a single query at 16 weeks; walk the range in chunks.
        items, cur = [], start
        while cur < end:
            nxt = min(cur + timedelta(weeks=15), end)
            items += list(self.paged(
                f"{API}/v1/calendars/items",
                since=cur.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                until=nxt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            ))
            cur = nxt
        return items


# --------------------------------------------------------------------------- data model


@dataclass
class Deadline:
    course: str
    title: str
    due: datetime
    source: str
    points: float | None = None
    status: str = "unknown"
    details: str = ""

    @property
    def key(self) -> str:
        return f"{self.course}|{self.title}|{self.due.isoformat()}"


@dataclass
class CourseDump:
    id: str
    code: str
    name: str
    url: str
    announcements: list[dict] = field(default_factory=list)
    content: list[dict] = field(default_factory=list)
    gradebook: list[dict] = field(default_factory=list)
    crawled_pages: list[tuple[str, str]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def submission_status(col: dict) -> str:
    grade = col.get("_grade") or {}
    if grade.get("exempt"):
        return "exempt"
    if grade.get("score") is not None or grade.get("text"):
        score = grade.get("score")
        possible = (col.get("score") or {}).get("possible")
        return f"graded {score}/{possible}" if score is not None else f"graded {grade.get('text')}"
    st = (grade.get("status") or "").lower()
    if st == "needsgrading":
        return "submitted (awaiting grade)"
    attempts = col.get("_attempts")
    if attempts:
        if any((a.get("status") or "").lower() in ("needsgrading", "completed", "submitted") for a in attempts):
            return "submitted"
        return "attempt started, NOT submitted"
    if attempts == []:
        return "NOT submitted"
    return "unknown"


def collect_deadlines(courses: list[CourseDump], calendar: list[dict]) -> list[Deadline]:
    out: dict[str, Deadline] = {}
    by_id = {c.id: c for c in courses}
    for c in courses:
        for col in c.gradebook:
            due = parse_dt((col.get("grading") or {}).get("due"))
            if not due:
                continue
            d = Deadline(
                course=c.code, title=col.get("name", "?"), due=due, source="gradebook",
                points=(col.get("score") or {}).get("possible"), status=submission_status(col),
                details=html_to_text(col.get("description"))[:400],
            )
            out[d.key] = d

        def walk(items):
            for it in items:
                adaptive = (it.get("availability") or {}).get("adaptiveRelease") or {}
                due = parse_dt(it.get("dueDate") or adaptive.get("end"))
                if due and (it.get("contentHandler") or {}).get("id", "").endswith(("assignment", "test-link", "asmt-test-link")):
                    d = Deadline(c.code, it.get("title", "?"), due, "content")
                    out.setdefault(d.key, d)
                walk(it.get("_children", []))

        walk(c.content)

    for item in calendar:
        start = parse_dt(item.get("end") or item.get("start"))
        if not start:
            continue
        cal_course = by_id.get(item.get("calendarId"))
        code = cal_course.code if cal_course else item.get("calendarName") or item.get("type", "calendar")
        title = item.get("title", "?")
        # Gradebook items already show up via the gradebook; skip duplicates by title+day.
        if any(d.course == code and d.title == title and d.due.date() == start.date() for d in out.values()):
            continue
        d = Deadline(code, title, start, f"calendar:{item.get('type', '')}",
                     details=html_to_text(item.get("description"))[:400])
        out[d.key] = d
    return sorted(out.values(), key=lambda d: d.due)


# --------------------------------------------------------------------------- rendering


def render_content(items: list[dict], lines: list[str]) -> None:
    for it in items:
        indent = "  " * it.get("_depth", 0)
        handler = (it.get("contentHandler") or {}).get("id", "").replace("resource/x-bb-", "")
        avail = (it.get("availability") or {}).get("available", "Yes")
        flag = "" if avail in ("Yes", None) else f" _(availability: {avail})_"
        lines.append(f"{indent}- **{it.get('title', '?')}** `[{handler}]`{flag}")
        body = html_to_text(it.get("body") or it.get("description"))
        if body:
            for bl in body.splitlines():
                lines.append(f"{indent}  > {bl}" if bl.strip() else f"{indent}  >")
        for att in it.get("_attachments", []):
            local = att.get("_local")
            name = att.get("fileName", "?")
            lines.append(f"{indent}  - attachment: {name}" + (f" -> `{local}`" if local else ""))
        render_content(it.get("_children", []), lines)


def write_course(c: CourseDump, out: Path, seen: set[str]) -> None:
    lines = [f"# {c.code} — {c.name}", "", f"Course link: {c.url}", ""]

    lines += ["## Announcements (newest first)", ""]
    anns = sorted(c.announcements, key=lambda a: a.get("created") or "", reverse=True)
    if not anns:
        lines.append("_none returned by API — see crawled pages below_")
    for a in anns:
        new = "" if f"ann:{a.get('id')}" in seen else " 🆕 NEW"
        lines += [f"### {a.get('title', '?')}{new}",
                  f"_posted {fmt_dt(parse_dt(a.get('created')))}_", "", html_to_text(a.get("body")), ""]

    lines += ["## Gradebook", "", "| Item | Due | Points | Status |", "|---|---|---|---|"]
    for col in sorted(c.gradebook, key=lambda x: (x.get("grading") or {}).get("due") or "9999"):
        due = parse_dt((col.get("grading") or {}).get("due"))
        lines.append(f"| {col.get('name', '?')} | {fmt_dt(due)} | "
                     f"{(col.get('score') or {}).get('possible', '-')} | {submission_status(col)} |")
    lines.append("")

    lines += ["## Content tree (modules, folders, syllabus, assignments)", ""]
    render_content(c.content, lines)
    lines.append("")

    if c.files:
        lines += ["## Downloaded files (read these — syllabi, slides, study guides)", ""]
        lines += [f"- `{f}`" for f in c.files] + [""]

    if c.crawled_pages:
        lines += ["## Crawled pages (raw visible text from the click-through pass)", ""]
        for url, text in c.crawled_pages:
            lines += [f"### {url}", "", "```text", text[:20000], "```", ""]

    (out / f"{slug(c.code)}.md").write_text("\n".join(lines))


def write_digest(courses: list[CourseDump], deadlines: list[Deadline], out: Path,
                 seen: set[str], now: datetime) -> None:
    lines = [
        "# Blackboard digest",
        "",
        f"Scraped: {fmt_dt(now)} (local time). Courses: {', '.join(c.code for c in courses)}",
        "",
        "Per-course detail lives in `courses/<CODE>.md`; downloaded files under `files/`.",
        "",
    ]

    missed = [d for d in deadlines if d.due < now and "not submitted" in d.status.lower()]
    lines += ["## ⚠️ Possibly missed (past due, no submission found)", ""]
    lines += [f"- **{d.course}** — {d.title} (was due {fmt_dt(d.due)}, {d.points or '?'} pts)"
              for d in missed] or ["_nothing detected_"]
    lines.append("")

    upcoming = [d for d in deadlines if d.due >= now - timedelta(days=1)]
    lines += ["## Upcoming deadlines", "",
              "| Due | Time left | Course | Item | Pts | Status | Source |", "|---|---|---|---|---|---|---|"]
    for d in upcoming:
        new = " 🆕" if f"dl:{d.key}" not in seen else ""
        lines.append(f"| {fmt_dt(d.due)} | {days_until(d.due, now)} | {d.course} | {d.title}{new} | "
                     f"{d.points if d.points is not None else '-'} | {d.status} | {d.source} |")
    lines.append("")

    lines += ["## New announcements since last scrape", ""]
    any_new = False
    for c in courses:
        for a in sorted(c.announcements, key=lambda a: a.get("created") or "", reverse=True):
            if f"ann:{a.get('id')}" not in seen:
                any_new = True
                body = html_to_text(a.get("body"))
                lines += [f"### [{c.code}] {a.get('title', '?')} — {fmt_dt(parse_dt(a.get('created')))}",
                          "", body[:1500] + ("…" if len(body) > 1500 else ""), ""]
    if not any_new:
        lines += ["_none_", ""]

    lines += ["## Recent announcements (last 21 days, all courses)", ""]
    cutoff = now - timedelta(days=21)
    for c in courses:
        for a in c.announcements:
            created = parse_dt(a.get("created"))
            if created and created >= cutoff:
                lines.append(f"- [{c.code}] {fmt_dt(created)} — {a.get('title', '?')}")
    lines.append("")

    (out / "digest.md").write_text("\n".join(lines))
    (out / "deadlines.json").write_text(json.dumps(
        [{**d.__dict__, "due": d.due.isoformat()} for d in deadlines], indent=2))


# --------------------------------------------------------------------------- browser


def open_context(playwright, headless: bool):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    # BBSCRAPE_CHROME=/path/to/chrome uses a specific browser binary instead of Playwright's.
    exe = os.environ.get("BBSCRAPE_CHROME") or None
    try:
        return playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=headless, accept_downloads=True, executable_path=exe,
            viewport={"width": 1400, "height": 1000},
        )
    except Exception as exc:
        sys.exit(f"Could not open browser profile {PROFILE_DIR}: {exc}\n"
                 "If another browser (e.g. the Playwright MCP one) is using this profile, close it and retry; "
                 "if the browser is missing, run: python3 -m playwright install chromium")


def make_fetch(ctx, base: str) -> Fetch:
    def fetch(path: str) -> tuple[int, Any]:
        url = path if path.startswith("http") else urljoin(base, path)
        for attempt in range(3):
            try:
                r = ctx.request.get(url, headers={"Accept": "application/json"}, timeout=30000)
            except Exception:
                time.sleep(1 + attempt)
                continue
            if r.status == 429:
                time.sleep(2 ** attempt)
                continue
            try:
                return r.status, r.json() if r.ok else None
            except Exception:
                return (r.status if not r.ok else 599), None  # HTML login page instead of JSON
        return 599, None
    return fetch


def download(ctx, base: str, url_path: str, dest: Path) -> bool:
    try:
        r = ctx.request.get(urljoin(base, url_path), timeout=120000)
        if not r.ok:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.body())
        return True
    except Exception:
        return False


EXPAND_JS = """
() => {
  let n = 0;
  const sel = [
    'button[aria-expanded="false"]',
    '[role="button"][aria-expanded="false"]',
    'a[aria-expanded="false"]',
    'summary',
  ].join(',');
  for (const el of document.querySelectorAll(sel)) {
    const label = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
    if (/(log ?out|sign ?out|delete|remove|submit|start attempt|begin)/.test(label)) continue;
    if (el.closest('nav, header')) continue;
    try { el.click(); n++; } catch (e) {}
  }
  return n;
}
"""


def page_text(page) -> str:
    chunks = []
    for frame in page.frames:
        try:
            t = frame.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            continue
        if t and t.strip():
            chunks.append(t.strip())
    text = "\n\n".join(dict.fromkeys(chunks))
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text)


def crawl_course(ctx, base: str, course: CourseDump, max_pages: int, log) -> None:
    """Click-through pass: open course pages, expand everything, follow in-course links."""
    cid = course.id
    seeds = [
        f"{base}/ultra/courses/{cid}/outline",
        f"{base}/ultra/courses/{cid}/announcements",
        f"{base}/ultra/courses/{cid}/grades",
        f"{base}/webapps/blackboard/execute/announcement?method=search&context=course_entry&course_id={cid}",
        f"{base}/webapps/blackboard/execute/launcher?type=Course&id={cid}",
    ]
    host = urlparse(base).netloc
    skip = re.compile(r"(logout|logoff|signout|delete|remove|submit|takeTest|launchAssessment|"
                      r"uploadAssignment|\.(zip|mp4|mov|mp3)$)", re.I)
    queue, seen_urls, seen_text = list(seeds), set(), set()
    page = ctx.new_page()
    try:
        while queue and len(course.crawled_pages) < max_pages:
            url = queue.pop(0)
            norm = url.split("#")[0]
            if norm in seen_urls:
                continue
            seen_urls.add(norm)
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                continue
            if (resp and resp.status >= 400) or not page.url.startswith("http"):
                continue  # Ultra vs Original seed that doesn't exist on this server, or a dead link
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            for _ in range(4):  # expand nested folders level by level
                try:
                    clicked = page.evaluate(EXPAND_JS)
                except Exception:
                    break
                if not clicked:
                    break
                page.wait_for_timeout(1200)
            text = page_text(page)
            if len(text) < 20 or hash(text) in seen_text or "page not found" in text.lower()[:300]:
                continue
            seen_text.add(hash(text))
            course.crawled_pages.append((page.url, text))
            for frame in page.frames:
                try:
                    hrefs = frame.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                except Exception:
                    continue
                for h in hrefs:
                    if urlparse(h).netloc == host and cid in h and not skip.search(h) and h not in seen_urls:
                        queue.append(h)
        log(f"    crawled {len(course.crawled_pages)} page(s)")
    finally:
        page.close()


# --------------------------------------------------------------------------- commands


def resolve_base(args) -> str:
    cfg = load_config()
    base = (args.url or cfg.get("url") or "").rstrip("/")
    if not base:
        sys.exit("No Blackboard URL. Run: python bbscrape.py login --url https://blackboard.yourschool.edu")
    if not base.startswith("http"):
        base = "https://" + base
    if args.url:
        save_config({**cfg, "url": base})
    return base


def cmd_login(args) -> None:
    from playwright.sync_api import sync_playwright

    base = resolve_base(args)
    with sync_playwright() as p:
        ctx = open_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(base)
        print("Log in in the browser window (SSO / Duo etc). Waiting up to 10 minutes...")
        api = BlackboardApi(make_fetch(ctx, base), log=lambda *_: None)
        for _ in range(300):
            me = api.me()
            if me and me.get("id"):
                name = (me.get("name") or {}).get("given") or me.get("userName")
                print(f"✓ Logged in as {name}. Session saved to {PROFILE_DIR}")
                break
            time.sleep(2)
        else:
            print("Timed out waiting for login.")
        ctx.close()


def cmd_scrape(args) -> None:
    from playwright.sync_api import sync_playwright

    base = resolve_base(args)
    now = datetime.now(timezone.utc)
    stamp = now.astimezone().strftime("%Y%m%d-%H%M%S")
    out = DATA_DIR / stamp
    (out / "courses").mkdir(parents=True, exist_ok=True)
    seen: set[str] = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()

    with sync_playwright() as p:
        ctx = open_context(p, headless=not args.headed)
        api = BlackboardApi(make_fetch(ctx, base))
        me = api.me()
        if not me:
            ctx.close()
            sys.exit("Not logged in (session expired?). Run: python bbscrape.py login")
        uid = me["id"]
        print(f"Logged in as {me.get('userName')}")

        courses_raw = api.my_courses(uid)
        wanted = [w.lower() for w in args.course or []]
        courses: list[CourseDump] = []
        for c in courses_raw:
            code = c.get("courseId") or c.get("name") or c["id"]
            name = c.get("name", code)
            if wanted and not any(w in code.lower() or w in name.lower() for w in wanted):
                continue
            if not wanted and not args.all_courses:
                avail = (c.get("availability") or {}).get("available", "Yes")
                if avail == "No" or (c["_membership"].get("availability") or {}).get("available") == "No":
                    continue
                last = parse_dt(c["_membership"].get("lastAccessed")) or parse_dt(c.get("created"))
                if last and last < now - timedelta(days=args.stale_days):
                    continue
            courses.append(CourseDump(id=c["id"], code=code, name=name,
                                      url=c.get("externalAccessUrl") or f"{base}/ultra/courses/{c['id']}/outline"))
        if not courses:
            print("No courses matched. Try --all-courses or --course <code>.")
        print(f"{len(courses)} course(s): {', '.join(c.code for c in courses)}")

        for c in courses:
            print(f"\n[{c.code}] {c.name}")
            c.announcements = api.announcements(c.id)
            print(f"    {len(c.announcements)} announcement(s)")
            c.content = api.content_tree(c.id)
            c.gradebook = api.gradebook(c.id, uid)
            print(f"    {len(c.gradebook)} gradebook column(s)")

            if not args.no_files:
                def grab(items):
                    for it in items:
                        for att in it.get("_attachments", []):
                            dest = out / "files" / slug(c.code) / f"{slug(it.get('title', ''))}__{slug(att.get('fileName', att['id']))}"
                            if download(ctx, base, f"{API}/v1/courses/{c.id}/contents/{it['id']}"
                                                   f"/attachments/{att['id']}/download", dest):
                                att["_local"] = str(dest.relative_to(out))
                                c.files.append(att["_local"])
                        grab(it.get("_children", []))
                grab(c.content)
                print(f"    {len(c.files)} file(s) downloaded")

            if not args.no_crawl:
                crawl_course(ctx, base, c, args.max_pages, print)

        calendar = api.calendar(now - timedelta(days=14), now + timedelta(days=args.horizon_days))
        ctx.close()

    deadlines = collect_deadlines(courses, calendar)
    for c in courses:
        write_course(c, out / "courses", seen)
    write_digest(courses, deadlines, out, seen, now)
    (out / "raw.json").write_text(json.dumps(
        {"courses": [c.__dict__ for c in courses], "calendar": calendar}, default=str, indent=1))

    new_seen = seen | {f"ann:{a.get('id')}" for c in courses for a in c.announcements} \
                    | {f"dl:{d.key}" for d in deadlines}
    STATE_FILE.write_text(json.dumps(sorted(new_seen)))

    latest = DATA_DIR / "latest"
    if latest.is_symlink() or latest.is_file():
        latest.unlink()
    elif latest.is_dir():
        shutil.rmtree(latest)
    try:
        latest.symlink_to(out.name, target_is_directory=True)
    except OSError:  # Windows without symlink rights
        shutil.copytree(out, latest)
    print(f"\n✓ Done. {len(deadlines)} deadline(s). Read {latest / 'digest.md'}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("login", help="open a browser to log in once; the session is reused")
    lg.add_argument("--url", help="your Blackboard base URL, e.g. https://blackboard.myschool.edu")
    lg.set_defaults(func=cmd_login)

    sc = sub.add_parser("scrape", help="pull everything into bb_data/latest/")
    sc.add_argument("--url")
    sc.add_argument("--course", action="append", help="only courses whose code/name contains this (repeatable)")
    sc.add_argument("--all-courses", action="store_true", help="include old/unavailable courses")
    sc.add_argument("--stale-days", type=int, default=150, help="skip courses not accessed in N days (default 150)")
    sc.add_argument("--horizon-days", type=int, default=120, help="calendar look-ahead (default 120)")
    sc.add_argument("--max-pages", type=int, default=60, help="click-pass page budget per course (default 60)")
    sc.add_argument("--no-crawl", action="store_true", help="skip the click-through pass (REST only)")
    sc.add_argument("--no-files", action="store_true", help="don't download attachments")
    sc.add_argument("--headed", action="store_true", help="show the browser while scraping")
    sc.set_defaults(func=cmd_scrape)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
