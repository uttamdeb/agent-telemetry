#!/usr/bin/env python3
"""Snapshot GitHub's cumulative per-asset download counts for a public release."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


API_ROOT = "https://api.github.com/repos"
USER_AGENT = "AgentTelemetry-release-asset-tracker/1"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


class TrackerError(RuntimeError):
    """An actionable release lookup or response error."""


def _release_endpoint(repository: str, tag: str | None) -> str:
    if not REPOSITORY_RE.fullmatch(repository):
        raise TrackerError("Repository must use the owner/repository format.")
    if tag:
        encoded_tag = urllib.parse.quote(tag, safe="")
        return f"{API_ROOT}/{repository}/releases/tags/{encoded_tag}"
    return f"{API_ROOT}/{repository}/releases/latest"


def fetch_release(
    repository: str,
    tag: str | None = None,
    token: str | None = None,
    *,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Fetch one public GitHub release. A token is optional and never returned."""
    endpoint = _release_endpoint(repository, tag)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(endpoint, headers=headers)
    open_url = urlopen or urllib.request.urlopen
    try:
        with open_url(request, timeout=20) as response:
            release = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 404:
            raise TrackerError("GitHub did not find that public release.") from exc
        if exc.code == 403:
            raise TrackerError(
                "GitHub refused the release lookup (HTTP 403); set GH_TOKEN or GITHUB_TOKEN "
                "if the unauthenticated API limit is exhausted."
            ) from exc
        raise TrackerError(f"GitHub release lookup failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise TrackerError(f"Could not reach the GitHub release API: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrackerError("GitHub returned an invalid release response.") from exc

    if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
        raise TrackerError("GitHub's release response did not contain an asset list.")
    return release


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TrackerError(f"GitHub returned an invalid {field} value.")
    return value


def make_snapshot(repository: str, release: dict[str, Any], fetched_at: dt.datetime | None = None) -> dict[str, Any]:
    assets = []
    for asset in release["assets"]:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise TrackerError("GitHub returned an invalid release asset.")
        assets.append({
            "name": asset["name"],
            "download_count": _nonnegative_int(asset.get("download_count"), "asset download count"),
            "size_bytes": _nonnegative_int(asset.get("size"), "asset size"),
            "updated_at": asset.get("updated_at"),
        })
    assets.sort(key=lambda item: item["name"].casefold())
    now = fetched_at or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise TrackerError("Snapshot time must include a timezone.")
    return {
        "schema_version": 1,
        "repository": repository,
        "fetched_at": now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "release": {
            "tag": release.get("tag_name") or "",
            "name": release.get("name") or release.get("tag_name") or "",
            "url": release.get("html_url") or "",
            "published_at": release.get("published_at"),
        },
        "asset_count": len(assets),
        "total_downloads": sum(asset["download_count"] for asset in assets),
        "assets": assets,
    }


def _markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_summary(snapshot: dict[str, Any]) -> str:
    release = snapshot["release"]
    release_label = _markdown_cell(release.get("name") or release.get("tag"))
    lines = [
        f"## Release asset downloads: {release_label}",
        "",
        f"Repository: `{_markdown_cell(snapshot['repository'])}`  ",
        f"Snapshot: `{_markdown_cell(snapshot['fetched_at'])}`  ",
        f"Cumulative downloads: **{snapshot['total_downloads']:,}** across {snapshot['asset_count']} assets.",
        "",
        "| Asset | Downloads | Size | Last updated |",
        "| --- | ---: | ---: | --- |",
    ]
    lines.extend(
        f"| {_markdown_cell(asset['name'])} | {asset['download_count']:,} | {asset['size_bytes']:,} bytes | "
        f"{_markdown_cell(asset.get('updated_at'))} |"
        for asset in snapshot["assets"]
    )
    lines.extend([
        "",
        "GitHub reports cumulative per-asset downloads. These counts are not unique people and do not identify where a download was discovered.",
        "",
    ])
    return "\n".join(lines)


def write_snapshot(snapshot: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_step_summary(summary: str, path: str | None) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"),
                        help="GitHub owner/repository (defaults to GITHUB_REPOSITORY).")
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG") or None,
                        help="Release tag to snapshot; defaults to GitHub's latest stable release.")
    parser.add_argument("--output", type=Path, default=Path("release-asset-downloads.json"),
                        help="JSON snapshot output path.")
    args = parser.parse_args(argv)
    if not args.repository:
        parser.error("--repository or GITHUB_REPOSITORY is required")

    try:
        release = fetch_release(args.repository, args.tag, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
        snapshot = make_snapshot(args.repository, release)
        write_snapshot(snapshot, args.output)
        summary = render_summary(snapshot)
        append_step_summary(summary, os.environ.get("GITHUB_STEP_SUMMARY"))
    except (OSError, TrackerError) as exc:
        print(f"Release asset tracker: {exc}", file=sys.stderr)
        return 1

    print(f"Recorded {snapshot['asset_count']} assets for {snapshot['release']['tag']}: "
          f"{snapshot['total_downloads']:,} cumulative downloads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
