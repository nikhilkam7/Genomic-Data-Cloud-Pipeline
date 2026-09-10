"""Extract stage - fetch a raw ClinVar release into the local landing zone.

This module does exactly one job: get an untouched copy of a ClinVar
``variant_summary`` file and record what we got. It never parses or cleans
the data - that is the Transform stage's responsibility. Keeping the two
separate means a bug in the cleaning code never forces us to re-download
440+ MB, and we always keep an auditable record of what the source said on
a given day.

CLI:
    python -m genepulse.extract                # current release
    python -m genepulse.extract --release 2026-06
    python -m genepulse.extract --force        # re-download even if cached
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from genepulse import __version__
from genepulse.config import (
    DOWNLOAD_CHUNK_BYTES,
    DOWNLOAD_TIMEOUT_SECONDS,
    RAW_DIR,
    clinvar_url,
)


@dataclass
class ExtractResult:
    """Everything we know about one extracted file."""

    source: str
    release_label: str
    release_arg: str | None
    source_url: str
    local_file: str
    size_bytes: int
    sha256: str
    server_last_modified: str | None
    downloaded_at: str
    tool_version: str
    from_cache: bool

    @property
    def local_path(self) -> Path:
        return RAW_DIR / self.local_file

    @property
    def manifest_path(self) -> Path:
        return RAW_DIR / f"{self.local_file}.manifest.json"


def _head(url: str) -> tuple[int | None, str | None]:
    """Ask the server about the file without downloading it.

    Returns (content_length_bytes, last_modified_header). A HEAD request
    fetches only the response headers, so this is cheap.
    """
    resp = requests.head(url, timeout=DOWNLOAD_TIMEOUT_SECONDS, allow_redirects=True)
    resp.raise_for_status()
    length = resp.headers.get("Content-Length")
    return (int(length) if length else None), resp.headers.get("Last-Modified")


def _resolve_release_label(release_arg: str | None, last_modified: str | None) -> str:
    """Pick a stable, filename-safe label for this release.

    For an archive request we already have it ("2026-06"). For the current
    file we derive YYYY-MM-DD from the server's Last-Modified date so the
    cached filename actually tells you which snapshot it is.
    """
    if release_arg is not None:
        return release_arg
    if last_modified:
        return parsedate_to_datetime(last_modified).date().isoformat()
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _sha256_file(path: Path) -> str:
    """Hash the file in chunks so we never load 440 MB into memory at once.

    The hash is a fingerprint: if two files have the same SHA-256 they are
    byte-for-byte identical. We use it to detect corruption and to tell
    whether a "new" monthly release actually changed.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(DOWNLOAD_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, expected_bytes: int | None) -> None:
    """Stream ``url`` to ``dest``, printing progress to stderr.

    We download to a temporary ``.part`` file and rename it only on success,
    so an interrupted run can never leave a half-file that looks complete.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    downloaded = 0
    last_pct = -1

    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                fh.write(chunk)
                downloaded += len(chunk)
                if expected_bytes:
                    pct = downloaded * 100 // expected_bytes
                    if pct != last_pct and pct % 5 == 0:
                        print(f"  {pct:3d}%  ({downloaded / 1e6:,.0f} MB)", file=sys.stderr)
                        last_pct = pct

    tmp.replace(dest)  # atomic on the same filesystem
    print(f"  done  ({downloaded / 1e6:,.0f} MB)", file=sys.stderr)


def extract(release: str | None = None, *, force: bool = False) -> ExtractResult:
    """Fetch one ClinVar release into RAW_DIR and write its manifest.

    Parameters
    ----------
    release : "YYYY-MM" for an archived monthly snapshot, or None for the
        current file.
    force : re-download even if a cached copy already exists.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    url = clinvar_url(release)
    expected_bytes, last_modified = _head(url)
    label = _resolve_release_label(release, last_modified)

    local_file = f"variant_summary_{label}.txt.gz"
    dest = RAW_DIR / local_file
    manifest = RAW_DIR / f"{local_file}.manifest.json"

    if dest.exists() and manifest.exists() and not force:
        print(f"Using cached {local_file}", file=sys.stderr)
        cached = json.loads(manifest.read_text())
        cached["from_cache"] = True
        return ExtractResult(**cached)

    print(f"Downloading {url}", file=sys.stderr)
    _download(url, dest, expected_bytes)

    result = ExtractResult(
        source="clinvar",
        release_label=label,
        release_arg=release,
        source_url=url,
        local_file=local_file,
        size_bytes=dest.stat().st_size,
        sha256=_sha256_file(dest),
        server_last_modified=last_modified,
        downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        tool_version=__version__,
        from_cache=False,
    )

    payload = asdict(result)
    payload.pop("from_cache")  # a property of *this run*, not of the file
    manifest.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {dest}\nWrote {manifest}", file=sys.stderr)
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a raw ClinVar release.")
    parser.add_argument(
        "--release",
        metavar="YYYY-MM",
        help="Archived monthly snapshot (e.g. 2026-06). Omit for the current file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a cached copy exists.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    result = extract(args.release, force=args.force)
    # Machine-readable summary on stdout; human progress went to stderr.
    print(json.dumps({**asdict(result), "local_path": str(result.local_path)}, indent=2))


if __name__ == "__main__":
    main()
