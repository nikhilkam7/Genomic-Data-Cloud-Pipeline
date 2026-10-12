#!/usr/bin/env bash
# Non-interactive run: scrape + have Claude Code write CRAM_PLAN.md.
# Cron example (7am daily):  0 7 * * * cd /path/to/blackboard-scraper && ./cram.sh >> cram.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
claude -p "/cram $*" \
  --allowedTools "Bash(python3 bbscrape.py:*)" Read Glob Grep Write "mcp__playwright"
