"""Offline tests: the REST pass and digest rendering against a fake Blackboard API."""

from datetime import datetime, timedelta, timezone

import bbscrape as bb

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
API = bb.API


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


ROUTES = {
    f"{API}/v1/users/me": {"id": "_1_1", "userName": "student"},
    f"{API}/v1/users/_1_1/courses?expand=course": {
        "results": [{"courseId": "CS101", "course": {"id": "_10_1", "courseId": "CS101", "name": "Intro CS"}}],
    },
    f"{API}/v1/courses/_10_1/announcements": {
        "results": [{"id": "_a1", "title": "Midterm moved", "body": "<p>Midterm is now <b>Oct 20</b></p>",
                     "created": iso(NOW - timedelta(days=1))}],
        "paging": {"nextPage": f"{API}/v1/courses/_10_1/announcements?offset=1"},
    },
    f"{API}/v1/courses/_10_1/announcements?offset=1": {
        "results": [{"id": "_a2", "title": "Welcome", "body": "hi", "created": iso(NOW - timedelta(days=40))}],
    },
    f"{API}/v1/courses/_10_1/contents": {
        "results": [{"id": "_c1", "title": "Week 6", "hasChildren": True,
                     "contentHandler": {"id": "resource/x-bb-folder"}}],
    },
    f"{API}/v1/courses/_10_1/contents/_c1/children": {
        "results": [{"id": "_c2", "title": "Syllabus", "body": "<ul><li>Ch 1-4</li></ul>",
                     "contentHandler": {"id": "resource/x-bb-document"}}],
    },
    f"{API}/v1/courses/_10_1/contents/_c2/attachments": {"results": [{"id": "_f1", "fileName": "syllabus.pdf"}]},
    f"{API}/v2/courses/_10_1/gradebook/columns": {
        "results": [
            {"id": "_g1", "name": "HW4", "score": {"possible": 10}, "grading": {"due": iso(NOW - timedelta(days=2))}},
            {"id": "_g2", "name": "HW5", "score": {"possible": 10}, "grading": {"due": iso(NOW + timedelta(days=3))}},
            {"id": "_g3", "name": "HW3", "score": {"possible": 10}, "grading": {"due": iso(NOW - timedelta(days=9))}},
        ],
    },
    f"{API}/v2/courses/_10_1/gradebook/users/_1_1": {"results": [{"columnId": "_g3", "score": 9}]},
    f"{API}/v2/courses/_10_1/gradebook/columns/_g1/attempts": {"results": []},
    f"{API}/v2/courses/_10_1/gradebook/columns/_g2/attempts": {"results": [{"status": "InProgress"}]},
}


def fake_fetch(path):
    return (200, ROUTES[path]) if path in ROUTES else (403, None)


def test_rest_pass_and_digest(tmp_path):
    logs = []
    api = bb.BlackboardApi(fake_fetch, log=logs.append)
    assert api.me()["id"] == "_1_1"
    [course] = api.my_courses("_1_1")
    c = bb.CourseDump(id=course["id"], code="CS101", name="Intro CS", url="u")
    c.announcements = api.announcements(c.id)
    assert [a["id"] for a in c.announcements] == ["_a1", "_a2"]  # pagination followed

    c.content = api.content_tree(c.id)
    assert c.content[0]["_children"][0]["_attachments"][0]["fileName"] == "syllabus.pdf"

    c.gradebook = api.gradebook(c.id, "_1_1")
    status = {col["name"]: bb.submission_status(col) for col in c.gradebook}
    assert status == {"HW4": "NOT submitted", "HW5": "attempt started, NOT submitted", "HW3": "graded 9/10"}

    deadlines = bb.collect_deadlines([c], [{"title": "Midterm", "type": "Course", "calendarId": "_10_1",
                                            "start": iso(NOW + timedelta(days=8))}])
    assert [d.title for d in deadlines] == ["HW3", "HW4", "HW5", "Midterm"]

    (tmp_path / "courses").mkdir()
    seen = {"ann:_a2"}
    bb.write_course(c, tmp_path / "courses", seen)
    bb.write_digest([c], deadlines, tmp_path, seen, NOW)
    digest = (tmp_path / "digest.md").read_text()
    missed = digest.split("## Upcoming")[0]
    assert "HW4" in missed and "HW3" not in missed
    assert "Midterm moved" in digest.split("## New announcements")[1]
    assert "Welcome" not in digest.split("## New announcements")[1].split("## Recent")[0]
    course_md = (tmp_path / "courses" / "CS101.md").read_text()
    assert "Midterm is now Oct 20" in course_md and "- Ch 1-4" in course_md


def test_blocked_endpoint_logged_once():
    logs = []
    api = bb.BlackboardApi(lambda p: (403, None), log=logs.append)
    assert api.announcements("_1") == [] and api.announcements("_2") == []
    assert len(logs) == 1


def test_html_to_text_keeps_links():
    out = bb.html_to_text('<p>See <a href="https://x.edu/r">review</a></p><ul><li>A</li><li>B</li></ul>')
    assert "See review" in out and "- A" in out and "Links: https://x.edu/r" in out
