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
import shlex
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

_RE_ANSI = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)

# Matches: "5h limit:   [████░░░░] 49% left (resets 16:59)"
_RE_5H = re.compile(
    r"5h\s+limit:\s+\[([█░\s]+)\]\s+(\d+)%\s+left(?:\s+\(resets\s+([^)]+)\))?",
    re.IGNORECASE,
)

# Matches: "Weekly limit:  [███████░░░] 92% left (resets 11:59 on 27 May)"
_RE_WEEKLY = re.compile(
    r"Weekly\s+limit:\s+\[([█░\s]+)\]\s+(\d+)%\s+left(?:\s+\(resets\s+([^)]+)\))?",
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


def clean_terminal_output(raw: str) -> str:
    """Strip terminal control codes and box characters from Codex TUI output."""
    text = _RE_ANSI.sub("", raw)
    text = text.replace("\r", "\n")

    lines = []
    for line in text.splitlines():
        line = line.translate(str.maketrans({
            "│": " ",
            "╭": " ",
            "╮": " ",
            "╰": " ",
            "╯": " ",
            "─": " ",
        }))
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)

    return "\n".join(lines)


def parse_status(raw: str) -> dict | None:
    """Return a dict of parsed values from the /status box, or None."""
    if not raw:
        return None

    raw = clean_terminal_output(raw)
    result: dict = {"timestamp": datetime.now().isoformat(timespec="seconds")}

    m = _RE_5H.search(raw)
    if m:
        result["limit_5h"] = {
            "bar": m.group(1).strip(),
            "percent_left": int(m.group(2)),
            "resets": (m.group(3) or "unknown").strip(),
        }

    m = _RE_WEEKLY.search(raw)
    if m:
        result["limit_weekly"] = {
            "bar": m.group(1).strip(),
            "percent_left": int(m.group(2)),
            "resets": (m.group(3) or "unknown").strip(),
        }

    for key, pattern in _RE_FIELDS.items():
        m = pattern.search(raw)
        if m:
            result[key] = m.group(1).strip()

    # At least one limit must be present to count as valid
    if "limit_5h" not in result and "limit_weekly" not in result:
        return None

    return result


def read_until_quiet(child: pexpect.spawn, timeout: int, quiet: float = 1.0, required=None) -> str:
    """Read TUI output until it stops changing or the timeout expires."""
    data = ""
    deadline = time.time() + timeout
    last_activity = time.time()
    saw_required = required is None

    while time.time() < deadline:
        try:
            data += child.read_nonblocking(size=4096, timeout=0.2)
            last_activity = time.time()
            if required is not None and required(clean_terminal_output(data)):
                saw_required = True
        except pexpect.TIMEOUT:
            if data and saw_required and time.time() - last_activity >= quiet:
                break
        except pexpect.EOF:
            break

    return data


def get_raw_status(codex_cmd: str = "codex --no-alt-screen", timeout: int = 45) -> str | None:
    """
    Spawn the Codex CLI, send /status, return the raw captured text.
    Returns None on failure.
    """
    try:
        argv = shlex.split(codex_cmd)
        child = pexpect.spawn(
            argv[0],
            argv[1:],
            encoding="utf-8",
            timeout=timeout,
            echo=False,
        )

        # Wait for the TUI to finish initial rendering/MCP startup.
        startup = read_until_quiet(
            child,
            timeout=timeout,
            required=lambda text: "OpenAI Codex" in text or "Press enter to continue" in text,
        )
        if "Press enter to continue" in clean_terminal_output(startup):
            child.send("\r\n")
            startup += read_until_quiet(
                child,
                timeout=timeout,
                required=lambda text: "OpenAI Codex" in text,
            )

        # Send /status command
        child.send("/status\r\n\r\n")

        raw = startup + read_until_quiet(
            child,
            timeout=30,
            required=lambda text: "5h limit:" in text or "Weekly limit:" in text,
        )
        cleaned = clean_terminal_output(raw)
        if "5h limit:" not in cleaned and "Weekly limit:" not in cleaned:
            child.close(force=True)
            return None

        # Graceful exit
        try:
            child.sendcontrol("c")
            child.sendcontrol("c")
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
    global STATUS_FILE

    parser = argparse.ArgumentParser(description="Fetch Codex CLI /status and save to JSON.")
    parser.add_argument("--codex", default="codex", metavar="PATH",
                        help="Path to the codex executable (default: codex)")
    parser.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                        help="Refresh interval in seconds; 0 = run once (default: 0)")
    parser.add_argument("--output", default=str(STATUS_FILE), metavar="FILE",
                        help=f"Output JSON path (default: {STATUS_FILE})")
    args = parser.parse_args()

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
