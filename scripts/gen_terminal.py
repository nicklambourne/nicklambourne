#!/usr/bin/env python3
"""Render assets/terminal.svg — a neofetch-style terminal card for the profile
README.

Live `reading` / `playing` lines come from https://ndl.au/api/public/now — a
public, no-auth endpoint on ndl.au built for this card, returning
`{playing, reading, degraded?}`. It replaced the card's original sources
(/api/spotify, deleted; /api/books, admin-gated) whose disappearance left this
card stale for weeks, so the contract here is: degrade gracefully, but NEVER
fail silently.

  - Each line refreshes independently. The endpoint reports a half-outage as
    `degraded: ["playing"]` rather than failing the whole request, so one
    dead source can't hold up the other line.
  - Last-good values persist in assets/live.json. A failed line keeps its
    last-good value on the card instead of a placeholder.
  - "Legitimately empty" is distinguished from "broken": a null field is a
    real answer (nothing in progress), a null field named in `degraded` is an
    outage. Only the latter is an error.
  - Any failure exits nonzero with ::error:: annotations and a
    refresh-errors.md summary, so the workflow goes red and files an issue —
    while still writing the best card it can.

Stdlib only, so the GitHub Action needs no install step. The ndl.au logo is
pre-rasterised to ASCII (see LOGO) so this script never needs an image
library at runtime.
"""

import html
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

# --- ndl.au enso logo, rasterised to a density ramp (monospace-safe glyphs) --
LOGO = (
    "       -=+*++++++*++-\n"
    "    .+*+=:.       :-+*+:\n"
    "   +*=.           .::.=*+\n"
    "  *+      -=++::+░░▒░   +░.\n"
    " *+    .+***▒▒░*+-░▒-    +░\n"
    "+*   .+░+::░▒*-  +▒:      *+\n"
    "░-  =*+.  ▒▒-   *▒:       -░ -=\n"
    "▒: :=   .░░:   +▒:        :++=.\n"
    "░-     :▒░    =▒-       -+++\n"
    "+*    :▒*     ▒*    :=++=:*+\n"
    " *+  -▒*     .░░+++*+=:  =░\n"
    " .*-=▒+       .==-:.    +░.\n"
    "   *░=               .=*+\n"
    "  ++ =*+=:       .:=+*+:\n"
    "       -=+*++++++*+=-"
).split("\n")

# --- colours (GitHub dark terminal palette) ---------------------------------
BG, BORDER, BAR = "#010409", "#30363d", "#161b22"
FG, DIM, MUTED = "#c9d1d9", "#484f58", "#8b949e"
GREEN, BLUE, CYAN = "#3fb950", "#58a6ff", "#39c5cf"
PURPLE, LABEL, RULE = "#a371f7", "#bc8cff", "#30363d"
SWATCHES = ["#f05138", "#e3b341", "#3fb950", "#39c5cf", "#58a6ff", "#bc8cff"]

# --- static copy ------------------------------------------------------------
NOW = (
    "Engineering Manager (AI) at Canva. I founded and lead the Evaluation "
    "Platform team — the tooling Canva uses to judge its generative AI, "
    'including a head-to-head "arena" across text, image, video, audio and 3D. '
    "Bringing the ergonomics of pytest to non-deterministic AI."
)
EARLIER = [
    "Senior ML Engineer at Canva building out the ML platform, and a "
    "software-engineering intern before that.",
    "Research engineer at UQ, where I architected Elpis.",
    "SRE intern at Atlassian (SLO reporting).",
    "Studied at UQ: Computer Science (thesis: Quantum Finite Automata), "
    "Finance, and Psychology.",
]
MADE = [
    ("slackblocks", "ergonomic Slack Block Kit for Python · 2.8M+ downloads"),
    ("wavebg", "audio-reactive Metal live-wallpaper app for macOS"),
    ("rules_latex", "reproducible LaTeX builds in Bazel / Tectonic"),
    ("elpis", "speech recognition for low-resource & Indigenous languages"),
    ("hermes", "builds language-teaching resources from ELAN analyses"),
]

# --- layout metrics ---------------------------------------------------------
PAD, TB = 20, 30
FS, LH, CW = 14, 20, 8.4          # body font
AFS, ALH, ACW = 11, 14, 6.6       # logo font
GAP, WRAP = 22, 74

# --- live source ------------------------------------------------------------
NOW_URL = "https://ndl.au/api/public/now"
UA = "ndl-profile-readme (github.com/nicklambourne/nicklambourne)"
DASH = "—"

ASSETS = os.path.join(os.path.dirname(__file__), "..", "assets")
SVG_PATH = os.path.join(ASSETS, "terminal.svg")
STATE_PATH = os.path.join(ASSETS, "live.json")
# Written next to the checkout (not under assets/, so it is never committed);
# the workflow folds it into the failure issue.
ERRORS_PATH = os.path.join(os.path.dirname(__file__), "..", "refresh-errors.md")


class SourceError(Exception):
    """A live source failed, or answered with something we can't read."""


def fetch_now(url=None, attempts=3):
    """GET + parse /api/public/now, retrying transient failures with backoff.
    Raises after the final attempt so the caller can keep last-good values
    rather than publish placeholders. `url` resolves at call time so a test
    (or a staging run) can point this at another host."""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url or NOW_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.load(r)
            if not isinstance(payload, dict):
                raise SourceError(f"expected a JSON object, got {type(payload).__name__}")
            return payload
        except Exception as err:  # network/timeout/HTTP status/parse
            last = err
            if i < attempts - 1:
                time.sleep(2**i)  # 1s, 2s
    raise last


def trunc(s, n=46):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _field(payload, name):
    """One field of the /api/public/now response, validated. Returns the dict
    or None; raises SourceError when the endpoint flagged the source as
    degraded, or when the field isn't the shape the contract promises — both
    mean "don't overwrite the last-good value with this"."""
    degraded = payload.get("degraded") or []
    if isinstance(degraded, list) and name in degraded:
        raise SourceError(f"ndl.au reported its {name} source as degraded")
    value = payload.get(name)
    if value is None or isinstance(value, dict):
        return value
    raise SourceError(f"expected `{name}` to be an object or null, got {type(value).__name__}")


def format_playing(payload):
    """The playing line as "track — artist", or None when nothing is playing.
    A track with no name or artist is a broken record, not an empty one."""
    track = _field(payload, "playing")
    if track is None:
        return None
    name, artist = track.get("track"), track.get("artist")
    if not name or not artist:
        raise SourceError(f"`playing` is missing track or artist: {track!r}")
    return trunc(f"{name} {DASH} {artist}")


def format_reading(payload):
    """(reading, extra) as "title — surname" plus the count of other books in
    progress, or (None, 0) when nothing is being read."""
    book = _field(payload, "reading")
    if book is None:
        return None, 0
    title = book.get("title")
    if not title:
        raise SourceError(f"`reading` is missing its title: {book!r}")
    authors = book.get("authors") or []
    surname = authors[0].split()[-1] if authors and authors[0].strip() else ""
    extra = book.get("also_reading")
    if not isinstance(extra, int) or extra < 0:
        extra = 0  # a bad count shouldn't cost us an otherwise-good title
    return trunc(f"{title} {DASH} {surname}" if surname else title), extra


def load_state(path=STATE_PATH):
    """Last-good live values. Missing/corrupt state is just 'no history'."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(values, path=STATE_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False, indent=2)
        f.write("\n")


def refresh(state):
    """Refresh both live lines from one /api/public/now call. Returns
    (values, errors): values always holds something renderable (fresh, else
    last-good, else DASH), and errors maps a line name to the exception that
    kept it stale.

    A request that fails outright stalls both lines; past that, each line is
    derived independently, so a `degraded` or malformed field costs only its
    own line."""
    extra = state.get("extra", 0)
    values = {
        "playing": state.get("playing") or DASH,
        "reading": state.get("reading") or DASH,
        "extra": extra if isinstance(extra, int) and extra >= 0 else 0,
        "updated": dict(state.get("updated") or {}),
    }
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        payload = fetch_now()
    except Exception as err:
        return values, {"playing": err, "reading": err}

    errors = {}
    try:
        values["playing"] = format_playing(payload) or DASH
        values["updated"]["playing"] = now
    except Exception as err:
        errors["playing"] = err

    try:
        reading, extra = format_reading(payload)
        values["reading"] = reading or DASH
        values["extra"] = extra
        values["updated"]["reading"] = now
    except Exception as err:
        errors["reading"] = err

    return values, errors


def wrap(text, width):
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


# --- svg primitives ---------------------------------------------------------
class Canvas:
    def __init__(self):
        self.els = []
        self.right = 0

    def line(self, x, y, spans, fs=FS, cw=CW):
        """spans: list of (text, colour). Renders one monospace line, tspans flow."""
        inner, col = [], 0
        for text, colour in spans:
            inner.append(f'<tspan fill="{colour}">{html.escape(text)}</tspan>')
            col += len(text)
        self.right = max(self.right, x + col * cw)
        self.els.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'xml:space="preserve">{"".join(inner)}</text>'
        )

    def rect(self, x, y, w, h, fill):
        self.els.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" fill="{fill}"/>')


def build(playing, reading, extra):
    c = Canvas()
    top = TB + 18

    # neofetch logo (left)
    for i, row in enumerate(LOGO):
        c.line(PAD, top + AFS + i * ALH, [(row, PURPLE)], fs=AFS, cw=ACW)
    art_w = max(len(r) for r in LOGO) * ACW
    art_h = len(LOGO) * ALH

    # neofetch info (right), vertically centred against the logo
    ix = PAD + art_w + GAP
    info = [
        [("nicklambourne", GREEN)],
        [("─" * 21, RULE)],
        [("role", LABEL), ("    Engineering Manager (AI) @ Canva", FG)],
        [("focus", LABEL), ("   measuring generative-AI quality at scale", FG)],
        [("based", LABEL), ("   Sydney, Australia", FG)],
        [("─ live " + "─" * 14, DIM)],
        [("reading ", CYAN), (reading, FG)] + ([(f"  +{extra}", DIM)] if extra > 0 else []),
        [("playing ", CYAN), (playing, FG)],
    ]
    iy = top + (art_h - len(info) * LH - LH) / 2
    for j, spans in enumerate(info):
        c.line(ix, iy + FS + j * LH, spans)
    # colour palette swatches
    py = iy + FS + len(info) * LH - FS + 2
    for k, sw in enumerate(SWATCHES):
        c.rect(ix + k * 20, py, 15, 11, sw)

    y = top + art_h + 16  # below the neofetch block

    def prompt(cmd):
        return [("nicholas@github", GREEN), (" ", FG), ("~", BLUE), (f" $ {cmd}", FG)]

    # cat now.md
    c.line(PAD, y, prompt("cat now.md"))
    y += LH
    for ln in wrap(NOW, WRAP):
        c.line(PAD, y, [(ln, FG)])
        y += LH

    # cat earlier.md
    y += 6
    c.line(PAD, y, prompt("cat earlier.md"))
    y += LH
    for bullet in EARLIER:
        rows = wrap(bullet, WRAP - 2)
        c.line(PAD, y, [("- " + rows[0], FG)])
        y += LH
        for cont in rows[1:]:
            c.line(PAD, y, [("  " + cont, FG)])
            y += LH

    # ls things-ive-made/
    y += 6
    c.line(PAD, y, prompt("ls things-ive-made/"))
    y += LH
    for name, desc in MADE:
        c.line(PAD, y, [(name.ljust(13), BLUE), (desc, MUTED)])
        y += LH

    w = int(max(c.right, ix + 47 * CW) + PAD)
    h = int(y + 6)

    bars = (
        f'<rect width="{w}" height="{h}" rx="8" fill="{BG}" stroke="{BORDER}"/>'
        f'<path d="M0 8 a8 8 0 0 1 8 -8 h{w - 16} a8 8 0 0 1 8 8 v22 h-{w} z" fill="{BAR}"/>'
        f'<line x1="0" y1="30" x2="{w}" y2="30" stroke="{BORDER}"/>'
        '<circle cx="18" cy="15" r="5.5" fill="#f05138"/>'
        '<circle cx="36" cy="15" r="5.5" fill="#e3b341"/>'
        '<circle cx="54" cy="15" r="5.5" fill="#3fb950"/>'
        f'<text x="72" y="19" font-size="12" fill="{MUTED}">nicholas@github: ~</text>'
    )
    body = "\n".join(c.els)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" '
        'font-family="ui-monospace,&quot;SF Mono&quot;,&quot;Cascadia Code&quot;,'
        'Menlo,Consolas,&quot;DejaVu Sans Mono&quot;,monospace">'
        "<title>nicklambourne</title>"
        "<desc>Terminal card: role, live reading and listening, projects.</desc>"
        f"{bars}\n{body}</svg>\n"
    )


def main(svg_path=SVG_PATH, state_path=STATE_PATH, errors_path=ERRORS_PATH):
    state = load_state(state_path)
    values, errors = refresh(state)

    # Always render the best card we have — a failed line keeps its last-good
    # value — and only then report failure, so partial updates still ship.
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(build(values["playing"], values["reading"], values["extra"]))
    save_state(values, state_path)
    print(f"wrote {os.path.normpath(svg_path)}")

    if not errors:
        return 0
    lines = []
    for name in sorted(errors):
        err = errors[name]
        last = values["updated"].get(name, "never")
        msg = " ".join(
            f"`{name}` refresh failed: {type(err).__name__}: {err} "
            f"(card keeps its last-good value, recorded {last})".split()
        )
        print(f"::error::{msg}")
        lines.append(f"- {msg}")
    with open(errors_path, "w", encoding="utf-8") as f:
        f.write(
            "The scheduled terminal-card refresh could not update every live line:\n\n"
            + "\n".join(lines)
            + "\n"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
