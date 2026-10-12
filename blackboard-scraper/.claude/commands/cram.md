---
description: Scrape Blackboard, find anything I missed, and build a cram plan against due dates
argument-hint: "[optional: course filter or focus, e.g. 'CS 3305 midterm']"
allowed-tools: Bash(python bbscrape.py:*), Bash(python3 bbscrape.py:*), Read, Glob, Grep, Write, mcp__playwright
---

You are my study triage assistant. Today is !`date "+%A %B %d %Y, %I:%M %p %Z"`.
Extra focus from me (may be empty): $ARGUMENTS

## 1. Get fresh data
Run `python3 bbscrape.py scrape` (add `--course <x>` if my focus names a course).
If it says I'm not logged in, stop and tell me to run `python3 bbscrape.py login`.

## 2. Read everything — don't skim
- `bb_data/latest/digest.md` first (deadlines, possibly-missed items, new announcements).
- Every `bb_data/latest/courses/*.md` in full, including the "Crawled pages" section.
- The syllabus and any study guide / exam review / rubric files under `bb_data/latest/files/`
  (PDFs, docx, pptx — read them). Syllabi give grade weights, exam dates and the topic schedule.

## 3. Fill gaps by clicking through Blackboard yourself
If something is ambiguous — an exam date only mentioned in passing, an assignment with no
due date, a module whose content didn't come through, a "see attached" with no attachment,
an "API refused" line in the scrape output — use the Playwright browser tools (`mcp__playwright__*`)
to open the course in Blackboard and check. The browser shares my logged-in profile.
Rules: read only. Never submit, start a test/quiz attempt, post, or change settings.

## 4. Write `CRAM_PLAN.md` (overwrite) with these sections
1. **🚨 Missed / at risk** — overdue unsubmitted work, announcements I likely didn't see that change
   something (date moved, new requirement, room change, extra credit, quiz announced in class),
   items whose status is "attempt started, NOT submitted". Say whether late submission still seems possible.
2. **Next 14 days** — table: due date (with weekday), hours/days left, course, item, weight
   (% of final grade from the syllabus if known, else points), status, estimated effort.
3. **What to cram, per exam/quiz** — for every upcoming exam/quiz: exact topics/chapters/lectures/modules
   it covers (cite the syllabus schedule, module titles, and announcements), the files to study,
   and a ranked list of highest-yield topics.
4. **Day-by-day plan** from today until the last item in the 14-day window: concrete blocks
   ("Tue: 2h — CS3305 Module 6 slides + practice problems, 1h — finish HW5 Q3–5"),
   prioritising by (weight × urgency ÷ effort), leaving buffer before each due date.
5. **Later this term** — anything big beyond 14 days worth starting early.
6. **Things to verify** — anything I should double-check with the instructor because the data conflicts.

Be specific and cite where each fact came from (course + announcement/module/file name).
Finish by printing the "Missed / at risk" section and the next 3 days of the plan in chat.
