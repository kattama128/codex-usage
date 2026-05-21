#!/usr/bin/env python3
"""
codex_tray.py — System tray widget that reads ~/.codex-usage/status.json
and ~/.claude-usage/status.json and shows Codex + Claude usage bars.

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

STATUS_FILE        = Path.home() / ".codex-usage"  / "status.json"
CLAUDE_STATUS_FILE = Path.home() / ".claude-usage" / "status.json"
MONITOR_SCRIPT        = Path(__file__).parent / "codex_monitor.py"
CLAUDE_MONITOR_SCRIPT = Path(__file__).parent / "claude_monitor.py"
ICON_SIZE = 64
FILE_POLL_INTERVAL = 30   # seconds between re-reads of status files
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


def make_icon(
    cdx_5h: int | None, cdx_wk: int | None,
    cla_5h: int | None, cla_7d: int | None,
) -> Image.Image:
    """Draw a 64×64 RGBA icon with four usage bars (Codex + Claude)."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([0, 0, ICON_SIZE - 1, ICON_SIZE - 1],
                           radius=10, fill=COLOUR_BG)

    bar_x = 5
    bar_w = ICON_SIZE - 10
    bar_h = 8   # smaller bars to fit four rows

    def draw_bar(top: int, pct: int | None, label: str) -> None:
        draw.text((bar_x, top - 9), label, fill=COLOUR_LABEL)
        draw.rounded_rectangle([bar_x, top, bar_x + bar_w, top + bar_h],
                               radius=2, fill=COLOUR_BAR_BG)
        if pct is not None and pct > 0:
            fill_w = max(2, int(bar_w * pct / 100))
            draw.rounded_rectangle([bar_x, top, bar_x + fill_w, top + bar_h],
                                   radius=2, fill=_bar_colour(pct))
        if pct is not None:
            txt = f"{pct}%"
            tx = bar_x + (bar_w - len(txt) * 5) // 2
            draw.text((tx, top), txt, fill=COLOUR_TEXT)

    # Codex rows
    draw_bar(12, cdx_5h, "C5h")
    draw_bar(27, cdx_wk, "Cwk")
    # Separator dot
    draw.ellipse([bar_x + bar_w // 2 - 1, 36, bar_x + bar_w // 2 + 1, 38],
                 fill=COLOUR_LABEL)
    # Claude rows
    draw_bar(42, cla_5h, "L5h")
    draw_bar(57, cla_7d, "L7d")

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

def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_status() -> dict | None:
    return _read_json(STATUS_FILE)


def read_claude_status() -> dict | None:
    return _read_json(CLAUDE_STATUS_FILE)


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


def fmt_tooltip(codex: dict | None, claude: dict | None) -> str:
    lines = ["Codex + Claude Usage"]

    if codex:
        l5 = codex.get("limit_5h")
        lw = codex.get("limit_weekly")
        lines.append("── Codex ──")
        if l5:
            lines.append(f"  5h:     {l5['percent_left']}% left  (resets {l5['resets']})")
        if lw:
            lines.append(f"  Weekly: {lw['percent_left']}% left  (resets {lw['resets']})")
        age = data_age_minutes(codex)
        if age is not None:
            age_str = f"{int(age)}m ago" + (" ⚠ stale" if age >= STALE_WARN_MINUTES else "")
            lines.append(f"  Updated: {age_str}")
    else:
        lines.append("── Codex ──")
        lines.append("  No data — run codex_monitor.py first")

    if claude:
        l5 = claude.get("limit_5h")
        lw = claude.get("limit_weekly")
        lines.append("── Claude ──")
        if l5:
            lines.append(f"  5h:  {l5['percent_left']}% left  (resets {l5['resets']})")
        if lw:
            lines.append(f"  7d:  {lw['percent_left']}% left  (resets {lw['resets']})")
        age = data_age_minutes(claude)
        if age is not None:
            age_str = f"{int(age)}m ago" + (" ⚠ stale" if age >= STALE_WARN_MINUTES else "")
            lines.append(f"  Updated: {age_str}")
    else:
        lines.append("── Claude ──")
        lines.append("  No data — run claude_monitor.py first")

    return "\n".join(lines)


# ── tray application ──────────────────────────────────────────────────────────

class CodexTray:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._codex: dict | None = read_status()
        self._claude: dict | None = read_claude_status()
        self._refreshing = False

        self._icon = pystray.Icon(
            "codex-usage",
            self._render_icon(),
            title=fmt_tooltip(self._codex, self._claude),
            menu=self._build_menu(),
        )

    # ── icon rendering ────────────────────────────────────────────────────────

    def _render_icon(self) -> Image.Image:
        if not self._codex and not self._claude:
            return make_icon_unknown()
        cdx_5h = (self._codex  or {}).get("limit_5h", {}).get("percent_left")
        cdx_wk = (self._codex  or {}).get("limit_weekly", {}).get("percent_left")
        cla_5h = (self._claude or {}).get("limit_5h", {}).get("percent_left")
        cla_7d = (self._claude or {}).get("limit_weekly", {}).get("percent_left")
        return make_icon(cdx_5h, cdx_wk, cla_5h, cla_7d)

    # ── menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        items: list = []

        # ── Codex section ──
        items.append(Item("── Codex ──", None, enabled=False))
        if not self._codex:
            items.append(Item("  No data — run codex_monitor.py", None, enabled=False))
        else:
            l5 = self._codex.get("limit_5h")
            lw = self._codex.get("limit_weekly")
            if l5:
                bar = self._ascii_bar(l5["percent_left"])
                items.append(Item(
                    f"  5h:     {bar}  {l5['percent_left']}% left  (resets {l5['resets']})",
                    None, enabled=False,
                ))
            if lw:
                bar = self._ascii_bar(lw["percent_left"])
                items.append(Item(
                    f"  Weekly: {bar}  {lw['percent_left']}% left  (resets {lw['resets']})",
                    None, enabled=False,
                ))
            if self._codex.get("model"):
                items.append(Item(f"  Model:  {self._codex['model']}", None, enabled=False))
            age = data_age_minutes(self._codex)
            if age is not None:
                age_str = f"{int(age)}m ago" + (" ⚠ stale" if age >= STALE_WARN_MINUTES else "")
                items.append(Item(f"  Updated: {age_str}", None, enabled=False))

        items.append(pystray.Menu.SEPARATOR)

        # ── Claude section ──
        items.append(Item("── Claude Code ──", None, enabled=False))
        if not self._claude:
            items.append(Item("  No data — run claude_monitor.py", None, enabled=False))
        else:
            l5 = self._claude.get("limit_5h")
            lw = self._claude.get("limit_weekly")
            if l5:
                bar = self._ascii_bar(l5["percent_left"])
                items.append(Item(
                    f"  5h: {bar}  {l5['percent_left']}% left  (resets {l5['resets']})",
                    None, enabled=False,
                ))
            if lw:
                bar = self._ascii_bar(lw["percent_left"])
                items.append(Item(
                    f"  7d: {bar}  {lw['percent_left']}% left  (resets {lw['resets']})",
                    None, enabled=False,
                ))
            age = data_age_minutes(self._claude)
            if age is not None:
                age_str = f"{int(age)}m ago" + (" ⚠ stale" if age >= STALE_WARN_MINUTES else "")
                items.append(Item(f"  Updated: {age_str}", None, enabled=False))

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
            # Run both monitors in parallel
            procs = [
                subprocess.Popen([sys.executable, str(MONITOR_SCRIPT)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE),
                subprocess.Popen([sys.executable, str(CLAUDE_MONITOR_SCRIPT)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE),
            ]
            for p in procs:
                try:
                    _, err = p.communicate(timeout=60)
                    if p.returncode != 0 and err:
                        print(f"[codex_tray] Monitor error: {err.decode()[:200]}", file=sys.stderr)
                except subprocess.TimeoutExpired:
                    p.kill()
                    print("[codex_tray] Monitor timed out.", file=sys.stderr)
            self._reload()
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
            self._codex = read_status()
            self._claude = read_claude_status()
        self._icon.icon = self._render_icon()
        self._icon.title = fmt_tooltip(self._codex, self._claude)
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
