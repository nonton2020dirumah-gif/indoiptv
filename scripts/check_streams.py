#!/usr/bin/env python3
"""Verify public Indonesian FTA HLS streams and rebuild playlist.m3u + README status."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "sources.json"
PLAYLIST_PATH = ROOT / "playlist.m3u"
README_PATH = ROOT / "README.md"

USER_AGENT = "Mozilla/5.0 (compatible; IndoIPTV-FTA-Checker/1.0)"
TIMEOUT_SECONDS = 8
MIN_PASSING_STREAMS = 3
MAX_FAILURE_RATIO = 0.60


def fetch_bytes(url: str, limit: int = 8192) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.getcode(), resp.read(limit)


def verify_hls(url: str) -> tuple[bool, str]:
    try:
        code, raw = fetch_bytes(url, 8192)
        text = raw.decode("utf-8", errors="replace")
        if code != 200 or "#EXTM3U" not in text:
            return False, f"HTTP {code} (invalid M3U)"
        sublines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
        if not sublines:
            return False, "empty playlist"
        nxt = urllib.parse.urljoin(url, sublines[0])
        c2, b2 = fetch_bytes(nxt, 4096)
        if c2 != 200:
            return False, f"child HTTP {c2}"
        if ".m3u8" in nxt:
            t2 = b2.decode("utf-8", errors="replace")
            segs = [l.strip() for l in t2.splitlines() if l.strip() and not l.startswith("#")]
            if not segs:
                return False, "empty variant playlist"
            seg_url = urllib.parse.urljoin(nxt, segs[0])
            c3, b3 = fetch_bytes(seg_url, 512)
            if c3 != 200 or not b3:
                return False, f"segment HTTP {c3}"
        elif not b2:
            return False, "empty segment"
        return True, "ok"
    except Exception as exc:
        return False, str(exc)[:80]


def build_playlist(active_channels: list[dict]) -> str:
    lines = [
        "#EXTM3U",
        "# Playlist FTA Publik Indonesia — diperbarui otomatis oleh nonton2020dirumah-gif/indoiptv",
        "# Butuh lebih lengkap + web player langsung? Trial gratis 4 jam: https://indoiptv.site/trial/?utm_source=github&utm_medium=m3u&utm_campaign=indoiptv_m3u",
        "",
    ]
    for ch in active_channels:
        lines.append(
            f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-name="{ch["name"]}" group-title="{ch["group"]}",{ch["name"]}'
        )
        lines.append(ch["url"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def update_readme(active_channels: list[dict], total: int, date_wib: str) -> None:
    if not README_PATH.exists():
        return
    content = README_PATH.read_text(encoding="utf-8")

    status_block = (
        "<!-- STATUS_START -->\n"
        f"**Terakhir dicek:** `{date_wib} WIB` — **{len(active_channels)}/{total} saluran FTA publik aktif**\n"
        "<!-- STATUS_END -->"
    )
    content = re.sub(
        r"<!-- STATUS_START -->.*?<!-- STATUS_END -->",
        status_block,
        content,
        flags=re.DOTALL,
    )

    by_group: dict[str, list[str]] = {}
    for ch in active_channels:
        by_group.setdefault(ch["group"], []).append(ch["name"])

    list_lines = ["<!-- CHANNELS_START -->"]
    for group, names in by_group.items():
        list_lines.append(f"- **{group}:** {', '.join(names)}")
    list_lines.append("<!-- CHANNELS_END -->")
    channels_block = "\n".join(list_lines)

    content = re.sub(
        r"<!-- CHANNELS_START -->.*?<!-- CHANNELS_END -->",
        channels_block,
        content,
        flags=re.DOTALL,
    )
    README_PATH.write_text(content, encoding="utf-8")


def main() -> int:
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        checks = list(pool.map(lambda ch: (ch, *verify_hls(ch["url"])), sources))

    active = [ch for ch, ok, _ in checks if ok]
    failed = [(ch["name"], reason) for ch, ok, reason in checks if not ok]

    print(f"Checked {len(sources)} streams: {len(active)} active, {len(failed)} failed")
    for name, reason in failed:
        print(f"  FAIL: {name} -> {reason}")

    fail_ratio = len(failed) / max(len(sources), 1)
    if len(active) < MIN_PASSING_STREAMS or fail_ratio > MAX_FAILURE_RATIO:
        print(
            "Guard triggered (too few streams passed — likely runner network/geo issue). "
            "Keeping existing playlist.m3u untouched.",
            file=sys.stderr,
        )
        return 2

    wib_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))
    date_wib = wib_now.strftime("%Y-%m-%d")

    PLAYLIST_PATH.write_text(build_playlist(active), encoding="utf-8")
    update_readme(active, len(sources), date_wib)
    print(f"Updated playlist.m3u ({len(active)} channels) and README.md ({date_wib} WIB).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
