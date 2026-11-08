#!/usr/bin/env python3
"""Write the update feed (`latest.json`) for a release, from its signed installers.

    python scripts/updater_manifest.py --version 0.2.0 \\
        --base-url https://github.com/Derric01/CurveVision/releases/download/v0.2.0 \\
        --notes "What changed" artifacts/ > latest.json

The desktop application checks the feed its release build was given
(`CURVEVISION_UPDATE_URL`, by default this repository's latest release's `latest.json`) and
installs a newer version only if the download's signature verifies against the public key
built into it (`desktop/shell/src-tauri/src/updates.rs`). A release built with the signing
key (`TAURI_SIGNING_PRIVATE_KEY`, `.github/workflows/desktop.yml`) carries a `.sig` beside
each installer the updater can use; this collects them into the feed:

* Linux — the AppImage (``linux-x86_64``)
* Windows — the NSIS installer (``windows-x86_64``)
* macOS — the ``.app.tar.gz``, one per architecture (``darwin-aarch64``, ``darwin-x86_64``)

Publishing the release, with the installers, their signatures and this file, is the
maintainers' step.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

#: Which file is each platform's update. The two Macs are built separately
#: (`.github/workflows/desktop.yml`), which names each archive after its architecture as
#: Tauri already names the .dmg.
PLATFORMS = (
    ("linux-x86_64", ".AppImage"),
    ("windows-x86_64", "-setup.exe"),
    ("darwin-aarch64", "_aarch64.app.tar.gz"),
    ("darwin-x86_64", "_x64.app.tar.gz"),
)


def manifest(files: list[Path], version: str, base_url: str, notes: str, now: str) -> dict:
    """The feed for ``files``: each platform's signed installer, and its signature."""
    signed = {f.name.removesuffix(".sig"): f for f in files if f.name.endswith(".sig")}
    platforms: dict[str, dict[str, str]] = {}
    for platform, suffix in PLATFORMS:
        matches = sorted(name for name in signed if name.endswith(suffix))
        if len(matches) > 1:
            raise SystemExit(f"more than one signed {suffix} for {platform}: {matches}")
        if matches:
            platforms[platform] = {
                "signature": signed[matches[0]].read_text().strip(),
                "url": f"{base_url.rstrip('/')}/{matches[0]}",
            }
    return {"version": version, "notes": notes, "pub_date": now, "platforms": platforms}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifacts", type=Path, help="a folder holding the installers and .sig files"
    )
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--base-url", required=True, help="where the release's files are downloaded from"
    )
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    files = [path for path in args.artifacts.rglob("*") if path.is_file()]
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    feed = manifest(files, args.version.lstrip("v"), args.base_url, args.notes, now)
    if not feed["platforms"]:
        print(
            "no signed installers found: was the release built with the signing key?",
            file=sys.stderr,
        )
        return 1
    json.dump(feed, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
