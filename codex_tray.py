#!/usr/bin/env python3
"""
codex_tray.py — System tray widget that reads ~/.codex-usage/status.json
and shows Codex usage bars as a live indicator.

Left-click  → nothing (data visible in tooltip / menu)
Right-click → full detail menu + Refresh + Quit
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    from PIL import Image, ImageDraw
    import pystray
    from pystray import MenuItem as Item
except ImportError:
    sys.exit("Missing dependencies: pip install pystray Pillow")

# ── config ────────────────────────────────────────────────────────────────────

STATUS_FILE = Path.home() / ".codex-usage" / "status.json"
MONITOR_SCRIPT = Path(__file__).parent / "codex_monitor.py"
ICON_SIZE = 64
FILE_POLL_INTERVAL = 30   # seconds between re-reads of status.json
STALE_WARN_MINUTES = 60   # warn in tooltip when data is this old

# ── colour palette ────────────────────────────────────────────────────────────

COLOUR_BG       = (28, 28, 36, 230)
COLOUR_BAR_BG   = (60, 60, 70, 255)
COLOUR_GREEN    = (46, 204, 113, 255)
COLOUR_YELLOW   = (243, 156, 18, 255)
COLOUR_RED      = (231, 76, 60, 255)
COLOUR_TEXT     = (210, 210, 215, 255)
COLOUR_LABEL    = (140, 140, 155, 255)


def _bar_colour(pct: int | None) -> tuple:
    if pct is None:
        return COLOUR_LABEL
    if pct > 40:
        return COLOUR_GREEN
    if pct > 15:
        return COLOUR_YELLOW
    return COLOUR_RED


def make_icon(pct_5h: int | None, pct_wk: int | None) -> Image.Image:
    """Draw a 64×64 RGBA icon with two usage bars."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded background
    draw.rounded_rectangle([0, 0, ICON_SIZE - 1, ICON_SIZE - 1],
                           radius=10, fill=COLOUR_BG)

    bar_x = 5
    bar_w = ICON_SIZE - 10
    bar_h = 12

    def draw_bar(top: int, pct: int | None, label: str) -> None:
        # Label
        draw.text((bar_x, top - 11), label, fill=COLOUR_LABEL)
        # Background track
        draw.rounded_rectangle([bar_x, top, bar_x + bar_w, top + bar_h],
                               radius=3, fill=COLOUR_BAR_BG)
        # Fill
        if pct is not None and pct > 0:
            fill_w = max(3, int(bar_w * pct / 100))
            draw.rounded_rectangle([bar_x, top, bar_x + fill_w, top + bar_h],
                                   radius=3, fill=_bar_colour(pct))
        # Percentage text centred on bar
        if pct is not None:
            txt = f"{pct}%"
            # Estimate text width: ~5px per char at default font
            tx = bar_x + (bar_w - len(txt) * 5) // 2
            draw.text((tx, top + 1), txt, fill=COLOUR_TEXT)

    draw_bar(22, pct_5h, "5h")
    draw_bar(47, pct_wk, "wk")

    return img


def make_icon_unknown() -> Image.Image:
    """Icon shown when no data file exists yet."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, ICON_SIZE - 1, ICON_SIZE - 1],
                           radius=10, fill=(50, 50, 60, 210))
    draw.text((20, 20), "??", fill=COLOUR_LABEL)
    return img


# ── status file helpers ───────────────────────────────────────────────────────

def read_status() -> dict | None:
    try:
        with open(STATUS_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def data_age_minutes(data: dict) -> float | None:
    ts = data.get("timestamp")
    if not ts:
        return None
    from datetime import datetime
    try:
        then = datetime.fromisoformat(ts)
        now = datetime.now()
        return (now - then).total_seconds() / 60
    except Exception:
        return None


def fmt_tooltip(data: dict | None) -> str:
    if not data:
        return "Codex Usage\nNo data — run codex_monitor.py first"

    lines = ["Codex Usage Monitor"]
    l5 = data.get("limit_5h")
    lw = data.get("limit_weekly")
    if l5:
        lines.append(f"5h:     {l5['percent_left']}% left  (resets {l5['resets']})")
    if lw:
        lines.append(f"Weekly: {lw['percent_left']}% left  (resets {lw['resets']})")
    if data.get("model"):
        lines.append(f"Model:  {data['model']}")
    if data.get("account"):
        lines.append(f"Account: {data['account']}")
    age = data_age_minutes(data)
    if age is not None:
        age_str = f"{int(age)}m ago"
        if age >= STALE_WARN_MINUTES:
            age_str += " ⚠ stale"
        lines.append(f"Updated: {age_str}")
    return "\n".join(lines)


# ── tray application ──────────────────────────────────────────────────────────

class CodexTray:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict | None = read_status()
        self._refreshing = False

        icon_img = self._render_icon()
        self._icon = pystray.Icon(
            "codex-usage",
            icon_img,
            title=fmt_tooltip(self._data),
            menu=self._build_menu(),
        )

    # ── icon rendering ────────────────────────────────────────────────────────

    def _render_icon(self) -> Image.Image:
        if not self._data:
            return make_icon_unknown()
        pct_5h = (self._data.get("limit_5h") or {}).get("percent_left")
        pct_wk = (self._data.get("limit_weekly") or {}).get("percent_left")
        return make_icon(pct_5h, pct_wk)

    # ── menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        items: list = []
        data = self._data

        if not data:
            items.append(Item("No data yet", None, enabled=False))
            items.append(Item("Run: python3 codex_monitor.py", None, enabled=False))
        else:
            l5 = data.get("limit_5h")
            lw = data.get("limit_weekly")

            if l5:
                bar = self._ascii_bar(l5["percent_left"])
                items.append(Item(
                    f"5h:     {bar}  {l5['percent_left']}% left  (resets {l5['resets']})",
                    None, enabled=False,
                ))
            if lw:
                bar = self._ascii_bar(lw["percent_left"])
                items.append(Item(
                    f"Weekly: {bar}  {lw['percent_left']}% left  (resets {lw['resets']})",
                    None, enabled=False,
                ))
            if l5 or lw:
                items.append(pystray.Menu.SEPARATOR)

            if data.get("model"):
                items.append(Item(f"Model:   {data['model']}", None, enabled=False))
            if data.get("account"):
                items.append(Item(f"Account: {data['account']}", None, enabled=False))
            if data.get("session"):
                short_sess = data["session"][:18] + "…"
                items.append(Item(f"Session: {short_sess}", None, enabled=False))

            age = data_age_minutes(data)
            if age is not None:
                age_str = f"{int(age)}m ago"
                if age >= STALE_WARN_MINUTES:
                    age_str += " ⚠ data may be stale"
                items.append(pystray.Menu.SEPARATOR)
                items.append(Item(f"Updated: {age_str}", None, enabled=False))

        items.append(pystray.Menu.SEPARATOR)
        items.append(Item("Refresh now", self._on_refresh))
        items.append(Item("Quit", self._on_quit))

        return pystray.Menu(*items)

    @staticmethod
    def _ascii_bar(pct: int, width: int = 10) -> str:
        filled = round(width * pct / 100)
        return "[" + "█" * filled + "░" * (width - filled) + "]"

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_refresh(self, icon=None, item=None) -> None:
        if self._refreshing:
            return
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self) -> None:
        self._refreshing = True
        try:
            result = subprocess.run(
                [sys.executable, str(MONITOR_SCRIPT)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                self._reload()
            else:
                print("[codex_tray] Monitor script failed:\n", result.stderr, file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("[codex_tray] Monitor timed out.", file=sys.stderr)
        except Exception as exc:
            print(f"[codex_tray] Refresh error: {exc}", file=sys.stderr)
        finally:
            self._refreshing = False

    def _on_quit(self, icon=None, item=None) -> None:
        self._icon.stop()

    # ── background polling ────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while True:
            time.sleep(FILE_POLL_INTERVAL)
            self._reload()

    def _reload(self) -> None:
        with self._lock:
            self._data = read_status()
        self._icon.icon = self._render_icon()
        self._icon.title = fmt_tooltip(self._data)
        self._icon.menu = self._build_menu()

    # ── entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        poller = threading.Thread(target=self._poll_loop, daemon=True)
        poller.start()
        self._icon.run()


def main() -> None:
    CodexTray().run()


if __name__ == "__main__":
    main()
