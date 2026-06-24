#!/usr/bin/env python3
"""Render assets/terminal.svg — a neofetch-style terminal card for the profile
README.

Live `reading` / `playing` lines come from two public ndl.au endpoints (no
auth, no secrets): https://ndl.au/api/spotify and https://ndl.au/api/books.
Everything else is static. Stdlib only, so the GitHub Action needs no install
step. The ndl.au logo is pre-rasterised to ASCII (see LOGO) so this script
never needs an image library at runtime.
"""

import html
import json
import urllib.request

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


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ndl-profile-readme"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def trunc(s, n=46):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def live():
    playing, reading, extra = "—", "—", 0
    try:
        rt = (fetch("https://ndl.au/api/spotify") or {}).get("recent_tracks") or []
        if rt:
            playing = trunc(f"{rt[0]['track_name']} — {rt[0]['artist_name']}")
    except Exception:
        pass
    try:
        cur = [b for b in (fetch("https://ndl.au/api/books") or []) if b.get("status") == "reading"]
        cur.sort(key=lambda b: b.get("date_started") or "", reverse=True)
        if cur:
            book = cur[0].get("book") or {}
            author = book.get("author")
            if isinstance(author, list):
                author = author[0] if author else ""
            surname = (author or "").split()[-1] if author else ""
            title = book.get("title", "")
            reading = trunc(f"{title} — {surname}" if surname else title)
            extra = len(cur) - 1
    except Exception:
        pass
    return playing, reading, extra


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


def build():
    playing, reading, extra = live()
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


if __name__ == "__main__":
    import os

    out = os.path.join(os.path.dirname(__file__), "..", "assets", "terminal.svg")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    print(f"wrote {os.path.normpath(out)}")
