#!/usr/bin/env python3
"""
claude_monitor.py — Makes a minimal Anthropic API call, reads the
rate-limit utilization headers, and saves structured data to
~/.claude-usage/status.json.

The OAuth token is read from ~/.claude/.credentials.json (written by
Claude Code itself). If the token is expired, Claude Code will refresh it
automatically the next time it runs.

Usage:
    python3 claude_monitor.py               # run once
    python3 claude_monitor.py --loop 900    # refresh every 15 minutes
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
STATUS_FILE = Path.home() / ".claude-usage" / "status.json"

API_URL = "https://api.anthropic.com/v1/messages"
PROBE_PAYLOAD = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "x"}],
}


def load_token() -> str:
    """Read the OAuth access token written by Claude Code."""
    try:
        creds = json.loads(CREDENTIALS_FILE.read_text())
        oauth = creds.get("claudeAiOauth", {})
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        sys.exit(f"[claude_monitor] Cannot read credentials: {exc}")

    token = oauth.get("accessToken")
    if not token:
        sys.exit("[claude_monitor] No accessToken found in credentials file.")

    expires_ms = oauth.get("expiresAt", 0)
    if expires_ms and expires_ms < time.time() * 1000:
        print(
            "[claude_monitor] Warning: OAuth token appears expired. "
            "Run Claude Code once to refresh it.",
            file=sys.stderr,
        )

    return token


def _fmt_reset(ts: int, include_date: bool = False) -> str:
    if not ts:
        return "unknown"
    dt = datetime.fromtimestamp(ts)
    if include_date:
        return dt.strftime("%H:%M on %d %b")
    return dt.strftime("%H:%M")


def fetch_usage(token: str) -> dict | None:
    """Call the API, return parsed utilization dict or None on error."""
    try:
        resp = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json=PROBE_PAYLOAD,
            timeout=20,
        )
    except requests.RequestException as exc:
        print(f"[claude_monitor] Network error: {exc}", file=sys.stderr)
        return None

    if resp.status_code not in (200, 429):
        print(f"[claude_monitor] API error {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None

    h = resp.headers
    util_5h = float(h.get("anthropic-ratelimit-unified-5h-utilization", 0))
    util_7d = float(h.get("anthropic-ratelimit-unified-7d-utilization", 0))
    reset_5h = int(h.get("anthropic-ratelimit-unified-5h-reset", 0))
    reset_7d = int(h.get("anthropic-ratelimit-unified-7d-reset", 0))
    status_5h = h.get("anthropic-ratelimit-unified-5h-status", "unknown")
    status_7d = h.get("anthropic-ratelimit-unified-7d-status", "unknown")

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "limit_5h": {
            "percent_left": round((1.0 - util_5h) * 100),
            "utilization": round(util_5h, 4),
            "resets": _fmt_reset(reset_5h),
            "status": status_5h,
        },
        "limit_weekly": {
            "percent_left": round((1.0 - util_7d) * 100),
            "utilization": round(util_7d, 4),
            "resets": _fmt_reset(reset_7d, include_date=True),
            "status": status_7d,
        },
    }


def fetch_and_save(token: str) -> dict | None:
    """Fetch utilization and persist to STATUS_FILE."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("[claude_monitor] Fetching Claude usage …", flush=True)
    data = fetch_usage(token)
    if not data:
        return None

    STATUS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    pct_5h = data["limit_5h"]["percent_left"]
    pct_7d = data["limit_weekly"]["percent_left"]
    reset_5h = data["limit_5h"]["resets"]
    reset_7d = data["limit_weekly"]["resets"]
    print(
        f"[claude_monitor] Saved → 5h: {pct_5h}% left (resets {reset_5h})"
        f" | 7d: {pct_7d}% left (resets {reset_7d})"
    )
    return data


def main() -> None:
    global STATUS_FILE

    parser = argparse.ArgumentParser(
        description="Fetch Claude Code rate-limit utilization and save to JSON."
    )
    parser.add_argument(
        "--loop", type=int, default=0, metavar="SECONDS",
        help="Refresh interval in seconds; 0 = run once (default: 0)",
    )
    parser.add_argument(
        "--output", default=str(STATUS_FILE), metavar="FILE",
        help=f"Output JSON path (default: {STATUS_FILE})",
    )
    args = parser.parse_args()
    STATUS_FILE = Path(args.output)

    token = load_token()

    if args.loop > 0:
        print(f"[claude_monitor] Loop mode: refreshing every {args.loop}s")
        while True:
            fetch_and_save(token)
            time.sleep(args.loop)
            token = load_token()  # re-read in case Claude Code refreshed it
    else:
        data = fetch_and_save(token)
        if data:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
