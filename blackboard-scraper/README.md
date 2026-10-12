# Blackboard → Claude Code cram planner

Scrapes every course you're in on Blackboard Learn (announcements, modules/folders, syllabus,
attachments, gradebook due dates + your submission status, calendar), then has Claude Code read all of
it and write `CRAM_PLAN.md`: what you missed, what's due next, and what to study for each exam, day by day.

Runs **on your own machine** — it needs your browser login (SSO/Duo), which a cloud session can't do.

## Setup (once)

```bash
cd blackboard-scraper
pip install -r requirements.txt
python3 -m playwright install chromium

# Opens a real browser window. Log in normally (SSO, Duo, etc.). The session is saved
# to ~/.bbscrape/profile and reused, so you only redo this when Blackboard logs you out.
python3 bbscrape.py login --url https://blackboard.YOURSCHOOL.edu
```

## Use it

```bash
claude            # start Claude Code in this folder
> /cram           # scrape everything + build the plan
> /cram CS 3305 midterm     # focus on one course/exam
```

Or non-interactively (good for a daily cron job): `./cram.sh`

Scraper only: `python3 bbscrape.py scrape` → read `bb_data/latest/digest.md`.

## How it works

1. **REST pass** – calls Blackboard's own `/learn/api/public/...` endpoints with your browser's
   cookies: courses, announcements, the full content tree (recursing into every folder/module),
   attachment downloads, gradebook columns with due dates, your grades/attempts, calendar items.
2. **Click-through pass** – headless Chromium opens each course (Ultra and Original layouts),
   clicks open every collapsed module/folder, follows in-course links (skipping anything like
   submit / start attempt / delete / logout) and saves the visible text. This catches things the API
   hides or your school blocks for students.
3. **Missed-item detection** – past-due items with no attempt → "Possibly missed"; started-but-not-
   submitted attempts are flagged; announcements and deadlines not seen in a previous scrape get 🆕.
4. **`/cram`** – Claude reads the digest, every course file and the downloaded syllabi/study guides,
   uses the Playwright MCP browser (same login) to check anything ambiguous, and writes the plan.

| Flag | Meaning |
|---|---|
| `--course CS3305` | only matching courses (repeatable) |
| `--all-courses` | include old / unavailable courses (default skips courses untouched for 150 days) |
| `--no-crawl` | REST pass only (faster) |
| `--no-files` | skip attachment downloads |
| `--max-pages N` | click-pass budget per course (default 60) |
| `--headed` | watch the browser while it scrapes |

`BBSCRAPE_CHROME=/path/to/chrome` uses a specific browser binary (e.g. your installed Chrome).

## Notes
- Everything scraped stays local in `bb_data/` (gitignored). Nothing is sent anywhere except to
  Claude when you run `/cram`.
- Close the Playwright MCP browser before scraping — only one browser can use the profile at a time.
- Some schools restrict student API access; you'll see `API refused …` lines and the click-through
  pass fills in. Check your school's acceptable-use policy for automated access.
