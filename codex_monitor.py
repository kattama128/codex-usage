#!/usr/bin/env python3
"""
codex_monitor.py — Spawns the Codex CLI, runs /status, parses the
progress bars and saves structured data to ~/.codex-usage/status.json.

Usage:
    python3 codex_monitor.py               # run once
    python3 codex_monitor.py --loop 1800   # refresh every 30 minutes
    python3 codex_monitor.py --codex /usr/local/bin/codex
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pexpect
except ImportError:
    sys.exit("Missing dependency: pip install pexpect")

STATUS_FILE = Path.home() / ".codex-usage" / "status.json"

# ── regex patterns ────────────────────────────────────────────────────────────

# Matches: "5h limit:   [████░░░░] 49% left (resets 16:59)"
_RE_5H = re.compile(
    r"5h\s+limit:\s+\[([█░\s]+)\]\s+(\d+)%\s+left\s+\(resets\s+([^)]+)\)",
    re.IGNORECASE,
)

# Matches: "Weekly limit:  [███████░░░] 92% left (resets 11:59 on 27 May)"
_RE_WEEKLY = re.compile(
    r"Weekly\s+limit:\s+\[([█░\s]+)\]\s+(\d+)%\s+left\s+\(resets\s+([^)]+)\)",
    re.IGNORECASE,
)

_RE_FIELDS = {
    "model":       re.compile(r"Model:\s+(.+)"),
    "account":     re.compile(r"Account:\s+(.+)"),
    "session":     re.compile(r"Session:\s+([0-9a-f-]{30,})"),
    "directory":   re.compile(r"Directory:\s+(.+)"),
    "permissions": re.compile(r"Permissions:\s+(.+)"),
    "agents_md":   re.compile(r"Agents\.md:\s+(.+)"),
    "collab_mode": re.compile(r"Collaboration\s+mode:\s+(.+)"),
}


def parse_status(raw: str) -> dict | None:
    """Return a dict of parsed values from the /status box, or None."""
    if not raw:
        return None

    result: dict = {"timestamp": datetime.now().isoformat(timespec="seconds")}

    m = _RE_5H.search(raw)
    if m:
        result["limit_5h"] = {
            "bar": m.group(1).strip(),
            "percent_left": int(m.group(2)),
            "resets": m.group(3).strip(),
        }

    m = _RE_WEEKLY.search(raw)
    if m:
        result["limit_weekly"] = {
            "bar": m.group(1).strip(),
            "percent_left": int(m.group(2)),
            "resets": m.group(3).strip(),
        }

    for key, pattern in _RE_FIELDS.items():
        m = pattern.search(raw)
        if m:
            result[key] = m.group(1).strip()

    # At least one limit must be present to count as valid
    if "limit_5h" not in result and "limit_weekly" not in result:
        return None

    return result


def get_raw_status(codex_cmd: str = "codex", timeout: int = 45) -> str | None:
    """
    Spawn the Codex CLI, send /status, return the raw captured text.
    Returns None on failure.
    """
    # Codex CLI typically shows a '>' prompt character
    prompt_pattern = r"[>►❯➤]\s*$"

    try:
        child = pexpect.spawn(
            codex_cmd,
            encoding="utf-8",
            timeout=timeout,
            echo=False,
        )

        # Wait for the initial prompt (CLI ready)
        idx = child.expect([prompt_pattern, pexpect.EOF, pexpect.TIMEOUT], timeout=timeout)
        if idx != 0:
            child.close(force=True)
            return None

        # Send /status command
        child.sendline("/status")

        # Collect output until prompt reappears
        idx = child.expect([prompt_pattern, pexpect.EOF, pexpect.TIMEOUT], timeout=30)
        raw = child.before or ""

        # Graceful exit
        try:
            child.sendline("/exit")
            child.expect(pexpect.EOF, timeout=5)
        except Exception:
            child.close(force=True)

        return raw

    except Exception as exc:
        print(f"[codex_monitor] pexpect error: {exc}", file=sys.stderr)
        return None


def fetch_and_save(codex_cmd: str = "codex") -> dict | None:
    """Get status, parse, persist to STATUS_FILE. Returns parsed dict or None."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"[codex_monitor] Spawning '{codex_cmd}' …", flush=True)
    raw = get_raw_status(codex_cmd)

    if not raw:
        print("[codex_monitor] No output captured.", file=sys.stderr)
        return None

    data = parse_status(raw)
    if not data:
        print("[codex_monitor] Could not parse /status output.", file=sys.stderr)
        print("[codex_monitor] Raw output was:\n", raw, file=sys.stderr)
        return None

    with open(STATUS_FILE, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

    pct_5h = data.get("limit_5h", {}).get("percent_left", "?")
    pct_wk = data.get("limit_weekly", {}).get("percent_left", "?")
    print(f"[codex_monitor] Saved → 5h: {pct_5h}% left | weekly: {pct_wk}% left")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Codex CLI /status and save to JSON.")
    parser.add_argument("--codex", default="codex", metavar="PATH",
                        help="Path to the codex executable (default: codex)")
    parser.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                        help="Refresh interval in seconds; 0 = run once (default: 0)")
    parser.add_argument("--output", default=str(STATUS_FILE), metavar="FILE",
                        help=f"Output JSON path (default: {STATUS_FILE})")
    args = parser.parse_args()

    global STATUS_FILE
    STATUS_FILE = Path(args.output)

    if args.loop > 0:
        print(f"[codex_monitor] Loop mode: refreshing every {args.loop}s")
        while True:
            fetch_and_save(args.codex)
            time.sleep(args.loop)
    else:
        data = fetch_and_save(args.codex)
        if data:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
