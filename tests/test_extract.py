"""Tests for the Extract stage.

These never hit the network. We point the module at a fake local server
(a tmp directory served in-process) or monkeypatch the HTTP helpers, so the
tests are fast and deterministic.
"""

from __future__ import annotations

import json

from genepulse import extract
from genepulse.config import clinvar_url


def test_clinvar_url_current():
    assert clinvar_url().endswith("/variant_summary.txt.gz")


def test_clinvar_url_archive():
    assert clinvar_url("2026-06").endswith("/archive/variant_summary_2026-06.txt.gz")


def test_resolve_release_label_prefers_arg():
    assert extract._resolve_release_label("2026-06", "anything") == "2026-06"


def test_resolve_release_label_uses_last_modified():
    label = extract._resolve_release_label(None, "Mon, 31 Aug 2026 12:35:23 GMT")
    assert label == "2026-08-31"


def test_sha256_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    # sha256("hello") is a known constant
    assert extract._sha256_file(f) == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_extract_writes_file_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "RAW_DIR", tmp_path)

    fake_bytes = b"\x1f\x8b\x08 fake gzip payload"
    monkeypatch.setattr(extract, "_head", lambda url: (len(fake_bytes), "Mon, 01 Jun 2026 00:00:00 GMT"))

    def fake_download(url, dest, expected_bytes):
        dest.write_bytes(fake_bytes)

    monkeypatch.setattr(extract, "_download", fake_download)

    result = extract.extract(release="2026-06")

    assert result.local_path.read_bytes() == fake_bytes
    assert result.release_label == "2026-06"
    assert result.from_cache is False

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["sha256"] == result.sha256
    assert "from_cache" not in manifest  # per-run, not persisted


def test_extract_uses_cache_on_second_call(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "RAW_DIR", tmp_path)
    monkeypatch.setattr(extract, "_head", lambda url: (5, "Mon, 01 Jun 2026 00:00:00 GMT"))

    calls = {"n": 0}

    def fake_download(url, dest, expected_bytes):
        calls["n"] += 1
        dest.write_bytes(b"hello")

    monkeypatch.setattr(extract, "_download", fake_download)

    first = extract.extract(release="2026-06")
    second = extract.extract(release="2026-06")

    assert calls["n"] == 1
    assert first.from_cache is False
    assert second.from_cache is True
