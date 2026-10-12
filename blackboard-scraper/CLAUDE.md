# Blackboard cram assistant

- `bbscrape.py` scrapes Blackboard Learn (REST API via the user's logged-in browser session + a
  headless click-through pass) into `bb_data/latest/`. `digest.md` is the entry point.
- `/cram [focus]` (see `.claude/commands/cram.md`) runs the scrape and writes `CRAM_PLAN.md`.
- The `playwright` MCP server shares the scraper's browser profile (`~/.bbscrape/profile`), so you can
  open Blackboard pages directly when the scraped data is incomplete. Only one browser can use the
  profile at a time — let `bbscrape.py scrape` finish before using the MCP browser.
- Blackboard is read-only for you: never submit assignments, start test/quiz attempts, post to
  discussions, or change settings.
- Tests: `python3 -m pytest test_bbscrape.py` (offline, fake API).
