#!/usr/bin/env python3
"""DejaView M6.1 - generate 20 fully-synthetic test screenshots.

Outputs (into the directory containing this script):
  - code_01.png .. code_05.png      (code editor, dark VS Code theme)
  - terminal_01.png .. terminal_05.png (terminals with fictional errors)
  - webpage_01.png .. webpage_05.png   (browser pages, mixed CJK/English)
  - chat_01.png .. chat_05.png         (chat app, fictional users)
  - one <name>.json ground-truth file per PNG

Hard discipline (handbook section 0): EVERY project name, error code, URL,
domain, username, file path, phone, email and IP in these images is FICTIONAL.
No real personal information of any kind is rendered.

Deterministic: no randomness -> output is reproducible.
"""

import json
import os

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
W, H = 1920, 1080

# System fonts. PingFang is not installed on this host, so STHeiti Medium
# (ships with macOS) is used for CJK + mixed CJK/Latin runs. It renders
# ASCII acceptably, so it is also used for pure-CJK lines.
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"
FONT_SANS = "/System/Library/Fonts/Helvetica.ttc"
FONT_CJK = "/System/Library/Fonts/STHeiti Medium.ttc"

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------- #
# Font + text helpers
# --------------------------------------------------------------------------- #
_FONT_CACHE = {}


def font(path, size):
    key = (path, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except OSError:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def tw(draw, txt, fnt):
    return draw.textbbox((0, 0), txt, font=fnt)[2]


def th(draw, txt, fnt):
    return draw.textbbox((0, 0), txt, font=fnt)[3]


def has_cjk(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def mono(size):
    return font(FONT_MONO, size)


def sans(size):
    return font(FONT_SANS, size)


def cjk(size):
    return font(FONT_CJK, size)


def body_font(size, txt):
    """Pick a body font that can render the run (CJK font when needed)."""
    return cjk(size) if has_cjk(txt) else sans(size)


def draw_text(draw, xy, txt, fnt, fill):
    draw.text(xy, txt, font=fnt, fill=fill)


def draw_run(draw, x, y, segments, fnt):
    """segments: list of (text, color). Draws left to right, returns new x."""
    for seg_txt, seg_col in segments:
        draw.text((x, y), seg_txt, font=fnt, fill=seg_col)
        x += tw(draw, seg_txt, fnt)
    return x


# --------------------------------------------------------------------------- #
# Syntax highlighting for code lines (visual only)
# --------------------------------------------------------------------------- #
KEYWORDS = {
    "def", "class", "return", "if", "elif", "else", "for", "while", "import",
    "from", "as", "try", "except", "finally", "with", "raise", "in", "not",
    "and", "or", "is", "None", "True", "False", "self", "const", "let", "var",
    "function", "export", "interface", "type", "new", "async", "await", "pub",
    "fn", "struct", "impl", "use", "match", "mut", "go", "package", "func",
    "map", "make", "nil",
}

C_CODE = {  # VS Code Dark+ palette
    "bg": (30, 30, 30),
    "sidebar": (37, 37, 38),
    "activity": (51, 51, 51),
    "titlebar": (50, 50, 50),
    "gutter": (45, 45, 45),
    "lnum": (133, 133, 133),
    "text": (212, 212, 212),
    "comment": (106, 153, 85),
    "string": (206, 145, 120),
    "keyword": (86, 156, 214),
    "number": (181, 206, 168),
    "func": (220, 220, 170),
    "class": (78, 201, 176),
    "tab_active": (37, 37, 38),
    "tab_inactive": (45, 45, 45),
    "border": (60, 60, 60),
    "statusbar": (7, 111, 144),
    "statusbar_text": (255, 255, 255),
}


def code_segments(line):
    """Tokenize a code line into (text, color) segments for coloring.

    Crude but produces believable VS Code-like coloring. Handles inline
    comments, quoted strings, and identifiers.
    """
    segs = []

    # 1) strip off a leading comment (# or //) -> whole tail is a comment
    for marker in ("#", "//"):
        idx = line.find(marker)
        if idx != -1:
            head = line[:idx]
            tail = line[idx:]
            return _tok_head(head) + [(tail, C_CODE["comment"])]
    return _tok_head(line)


def _tok_head(head):
    segs = []
    token = ""
    in_str = False
    str_q = ""

    def flush(tok):
        if not tok:
            return
        if tok in KEYWORDS:
            segs.append((tok, C_CODE["keyword"]))
        elif tok.lstrip("-").isdigit():
            segs.append((tok, C_CODE["number"]))
        else:
            segs.append((tok, C_CODE["text"]))

    i = 0
    while i < len(head):
        ch = head[i]
        if in_str:
            token += ch
            if ch == str_q and not (i > 0 and head[i - 1] == "\\"):
                segs.append((token, C_CODE["string"]))
                token = ""
                in_str = False
            i += 1
            continue
        if ch in ('"', "'"):
            flush(token)
            token = ch
            in_str = True
            str_q = ch
            i += 1
            continue
        if ch.isalnum() or ch == "_":
            token += ch
            i += 1
            continue
        # punctuation
        flush(token)
        token = ""
        # function-name colouring: identifier immediately followed by "("
        if ch == "(" and segs and segs[-1][1] == C_CODE["text"]:
            prev_txt, _ = segs[-1]
            segs[-1] = (prev_txt, C_CODE["func"])
        segs.append((ch, C_CODE["text"]))
        i += 1
    flush(token)
    # class-ish: a token that starts uppercase & is "class-like"
    return segs


# --------------------------------------------------------------------------- #
# Chrome decorators
# --------------------------------------------------------------------------- #
def traffic_lights(draw, x, y):
    for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        draw.ellipse([x + i * 22, y, x + i * 22 + 14, y + 14], fill=col)


def rect(draw, box, color, outline=None):
    draw.rectangle(box, fill=color, outline=outline)


def hline(draw, x1, x2, y, color, width=1):
    draw.line([(x1, y), (x2, y)], fill=color, width=width)


# --------------------------------------------------------------------------- #
# Renderer: code editor (VS Code dark)
# --------------------------------------------------------------------------- #
def render_code(spec):
    img = Image.new("RGB", (W, H), C_CODE["bg"])
    draw = ImageDraw.Draw(img)

    # title bar
    rect(draw, [0, 0, W, 40], C_CODE["titlebar"])
    traffic_lights(draw, 14, 13)
    # tabs
    tab_fnt = sans(13)
    x = 90
    tabs = spec["tabs"]
    for i, t in enumerate(tabs):
        w = tw(draw, t, tab_fnt) + 36
        col = C_CODE["tab_active"] if i == 0 else C_CODE["tab_inactive"]
        rect(draw, [x, 0, x + w, 40], col)
        draw.text((x + 16, 11), t, font=tab_fnt, fill=C_CODE["text"])
        if i == 0:
            hline(draw, x, x + w, 39, C_CODE["statusbar"], 2)
        x += w
    hline(draw, 0, W, 40, C_CODE["border"])

    # activity bar (far left icons column)
    rect(draw, [0, 40, 48, H], C_CODE["activity"])
    icons = [("files", 96, 200), ("search", 96, 260), ("git", 96, 320),
             ("bug", 96, 380), ("ext", 96, 440)]
    icon_fnt = sans(18)
    for _, ix, iy in icons:
        draw.rectangle([14, iy, 34, iy + 20], outline=C_CODE["lnum"])

    # sidebar (file tree)
    sb_w = 240
    rect(draw, [48, 40, 48 + sb_w, H], C_CODE["sidebar"])
    sf = sans(13)
    draw.text((62, 58), spec["project"].upper(), font=sans(11),
              fill=C_CODE["lnum"])
    y = 84
    for item, depth in spec["tree"]:
        prefix = "  " * depth
        marker = "▾ " if depth == 0 else "• "
        draw.text((62, y), marker + prefix + item, font=sf,
                  fill=C_CODE["text"])
        y += 22

    # editor area
    ed_x = 48 + sb_w
    rect(draw, [ed_x, 40, W, H], C_CODE["bg"])
    # gutter
    gutter_w = 60
    rect(draw, [ed_x, 40, ed_x + gutter_w, H], C_CODE["gutter"])
    # path breadcrumb
    draw.text((ed_x + 14, 47), spec["path"], font=sans(12),
              fill=C_CODE["lnum"])
    hline(draw, ed_x, W, 70, C_CODE["border"])

    code_fnt = mono(16)
    line_h = 23
    y = 86
    lines = spec["lines"]
    for i, line in enumerate(lines, start=1):
        # line number
        ln = str(i).rjust(4)
        draw.text((ed_x + 6, y), ln, font=mono(13), fill=C_CODE["lnum"])
        # highlighted code
        segs = code_segments(line)
        draw_run(draw, ed_x + gutter_w + 16, y, segs, code_fnt)
        y += line_h

    # status bar
    rect(draw, [0, H - 26, W, H], C_CODE["statusbar"])
    sb_f = sans(12)
    draw.text((12, H - 22), "  main  ", font=sb_f, fill=C_CODE["statusbar_text"])
    draw.text((90, H - 22), "0 errors  0 warnings", font=sb_f,
              fill=C_CODE["statusbar_text"])
    draw.text((W - 320, H - 22), "UTF-8  LF  Python  Ln 42, Col 18",
              font=sb_f, fill=C_CODE["statusbar_text"])

    return img


# --------------------------------------------------------------------------- #
# Renderer: terminal
# --------------------------------------------------------------------------- #
TERM_DARK = {
    "bg": (22, 22, 24),
    "title": (52, 52, 55),
    "text": (204, 204, 204),
    "prompt": (38, 209, 139),
    "path": (86, 156, 214),
    "dim": (122, 122, 122),
    "err": (244, 135, 113),
    "warn": (220, 175, 90),
    "accent": (120, 200, 255),
}
TERM_LIGHT = {
    "bg": (250, 250, 250),
    "title": (230, 230, 232),
    "text": (40, 40, 42),
    "prompt": (16, 138, 90),
    "path": (28, 110, 178),
    "dim": (140, 140, 140),
    "err": (199, 37, 78),
    "warn": (170, 120, 20),
    "accent": (20, 120, 200),
}


def render_terminal(spec):
    palette = TERM_LIGHT if spec.get("light") else TERM_DARK
    img = Image.new("RGB", (W, H), palette["bg"])
    draw = ImageDraw.Draw(img)

    # title bar
    rect(draw, [0, 0, W, 34], palette["title"])
    traffic_lights(draw, 14, 10)
    draw.text((W // 2 - 80, 9), spec["title"], font=sans(13),
              fill=palette["text"])
    hline(draw, 0, W, 34, palette["dim"])

    fnt = mono(16)
    line_h = 24
    x0, y = 24, 52
    for row in spec["lines"]:
        # each row: list of (text, role)
        cx = x0
        for txt, role in row:
            col = palette["text"]
            if role == "prompt":
                col = palette["prompt"]
            elif role == "path":
                col = palette["path"]
            elif role == "err":
                col = palette["err"]
            elif role == "warn":
                col = palette["warn"]
            elif role == "dim":
                col = palette["dim"]
            elif role == "accent":
                col = palette["accent"]
            draw.text((cx, y), txt, font=fnt, fill=col)
            cx += tw(draw, txt, fnt)
        y += line_h
        if y > H - 24:
            break
    return img


# --------------------------------------------------------------------------- #
# Renderer: webpage (browser chrome)
# --------------------------------------------------------------------------- #
WEB_LIGHT = {
    "bg": (255, 255, 255),
    "chrome": (237, 238, 241),
    "border": (210, 212, 216),
    "text": (26, 26, 27),
    "muted": (120, 124, 130),
    "link": (0, 102, 204),
    "h": (20, 20, 22),
    "chip": (240, 241, 244),
    "code_bg": (245, 246, 248),
}
WEB_DARK = {
    "bg": (24, 24, 27),
    "chrome": (39, 39, 42),
    "border": (63, 63, 70),
    "text": (228, 228, 231),
    "muted": (161, 161, 170),
    "link": (104, 168, 255),
    "h": (244, 244, 245),
    "chip": (39, 39, 42),
    "code_bg": (39, 39, 42),
}


def render_webpage(spec):
    palette = WEB_DARK if spec.get("dark") else WEB_LIGHT
    img = Image.new("RGB", (W, H), palette["bg"])
    draw = ImageDraw.Draw(img)

    # browser chrome
    rect(draw, [0, 0, W, 96], palette["chrome"])
    traffic_lights(draw, 18, 18)
    # tab
    tab_y = 8
    rect(draw, [90, tab_y, 430, 48], palette["bg"])
    draw.text((110, tab_y + 10), spec["tab_title"], font=sans(13),
              fill=palette["text"])
    hline(draw, 0, W, 48, palette["border"])
    # nav buttons
    nav_f = sans(18)
    for i, sym in enumerate(["‹", "›", "⟳"]):
        draw.text((20 + i * 40, 60), sym, font=nav_f, fill=palette["muted"])
    # address bar
    bar_x, bar_y, bar_w, bar_h = 160, 58, 1500, 32
    rect(draw, [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], palette["bg"],
         outline=palette["border"])
    lock_f = sans(12)
    draw.text((bar_x + 12, bar_y + 8), "🔒", font=lock_f, fill=palette["muted"])
    draw.text((bar_x + 38, bar_y + 7), spec["url"], font=sans(14),
              fill=palette["text"])
    hline(draw, 0, W, 96, palette["border"])

    # content
    y = 130
    pad = 120
    for block in spec["blocks"]:
        kind = block[0]
        if kind == "h1":
            y += 16
            draw.text((pad, y), block[1], font=cjk(40), fill=palette["h"])
            y += 56
        elif kind == "h2":
            y += 18
            draw.text((pad, y), block[1], font=cjk(28), fill=palette["h"])
            y += 42
        elif kind == "p":
            txt = block[1]
            fnt = body_font(20, txt)
            y = draw_paragraph(draw, pad, y, txt, fnt, palette["text"],
                               max_w=W - 2 * pad, line_h=30)
            y += 16
        elif kind == "meta":
            fnt = sans(15)
            cx = pad
            for txt, role in block[1]:
                col = palette["muted"] if role == "muted" else palette["link"]
                draw.text((cx, y), txt, font=fnt, fill=col)
                cx += tw(draw, txt, fnt) + 16
            y += 28
        elif kind == "link":
            fnt = body_font(20, block[1])
            draw.text((pad, y), block[1], font=fnt, fill=palette["link"])
            y += 30
        elif kind == "code":
            fnt = mono(17)
            ch = 28
            for cl in block[1]:
                rect(draw, [pad, y, W - pad, y + ch], palette["code_bg"])
                draw.text((pad + 14, y + 4), cl, font=fnt, fill=palette["text"])
                y += ch
            y += 14
        elif kind == "chip":
            fnt = sans(14)
            for label in block[1]:
                ww = tw(draw, label, fnt) + 22
                rect(draw, [pad, y, pad + ww, y + 30], palette["chip"],
                     outline=palette["border"])
                draw.text((pad + 11, y + 6), label, font=fnt,
                          fill=palette["text"])
                pad += ww + 10
            y += 40
            pad = 120
        elif kind == "stat":
            # big number + label rows
            for num, label in block[1]:
                draw.text((pad, y), num, font=sans(34), fill=palette["h"])
                draw.text((pad, y + 44), label, font=sans(15),
                          fill=palette["muted"])
                pad += 320
            y += 76
            pad = 120
        if y > H - 60:
            break
    return img


def draw_paragraph(draw, x, y, txt, fnt, color, max_w, line_h):
    """Naive word-wrap paragraph renderer. Returns final y."""
    words = txt.split()
    if not words:
        return y
    line = words[0]
    for w in words[1:]:
        trial = line + " " + w
        if tw(draw, trial, fnt) > max_w:
            draw.text((x, y), line, font=fnt, fill=color)
            y += line_h
            line = w
        else:
            line = trial
    draw.text((x, y), line, font=fnt, fill=color)
    return y + line_h


# --------------------------------------------------------------------------- #
# Renderer: chat (Slack / Discord style)
# --------------------------------------------------------------------------- #
CHAT_LIGHT = {
    "bg": (255, 255, 255),
    "side": (248, 248, 248),
    "side_channel": (241, 242, 245),
    "header": (255, 255, 255),
    "text": (28, 28, 30),
    "muted": (108, 112, 118),
    "border": (225, 226, 230),
    "input": (250, 250, 251),
    "badge": (42, 122, 208),
}
CHAT_DARK = {
    "bg": (54, 57, 63),
    "side": (47, 49, 54),
    "side_channel": (64, 68, 75),
    "header": (54, 57, 63),
    "text": (220, 221, 225),
    "muted": (142, 146, 153),
    "border": (38, 40, 44),
    "input": (64, 68, 75),
    "badge": (88, 101, 242),
}

USER_COLORS = [
    (210, 95, 95), (95, 175, 210), (175, 145, 95), (135, 195, 120),
    (185, 130, 200), (220, 175, 90), (110, 165, 200), (200, 115, 140),
]


def render_chat(spec):
    palette = CHAT_DARK if spec.get("dark") else CHAT_LIGHT
    img = Image.new("RGB", (W, H), palette["bg"])
    draw = ImageDraw.Draw(img)

    side_w = 280
    # sidebar
    rect(draw, [0, 0, side_w, H], palette["side"])
    draw.text((20, 20), spec["workspace"], font=sans(18), fill=palette["text"])
    draw.text((20, 56), spec["subhead"], font=sans(13), fill=palette["muted"])
    cy = 96
    ch_f = sans(14)
    for group, channels in spec["channels"]:
        draw.text((20, cy), group, font=sans(12), fill=palette["muted"])
        cy += 22
        for name, active, unread in channels:
            col = palette["side_channel"] if active else palette["side"]
            rect(draw, [12, cy, side_w - 12, cy + 26], col)
            prefix = "# " if name != "direct" else ""
            label = ("  " + prefix + name)
            draw.text((20, cy + 5), label, font=ch_f,
                      fill=palette["text"] if active else palette["muted"])
            if unread:
                rect(draw, [side_w - 40, cy + 9, side_w - 28, cy + 19],
                     palette["badge"])
            cy += 28
        cy += 8
    hline(draw, side_w, side_w, 0, palette["border"], side_w)

    # main header
    rect(draw, [side_w, 0, W, 56], palette["header"])
    hline(draw, side_w, W, 56, palette["border"])
    draw.text((side_w + 24, 16), "# " + spec["channel"], font=sans(16),
              fill=palette["text"])
    draw.text((side_w + 24 + tw(draw, "# " + spec["channel"], sans(16)) + 24,
              20), spec["topic"], font=sans(13), fill=palette["muted"])

    # messages
    msg_f = body_font(17, "")
    name_f = sans(16)
    time_f = sans(12)
    y = 84
    x0 = side_w + 24
    for m in spec["messages"]:
        user, color_idx, time, body = m
        # avatar
        av = x0
        rect(draw, [av, y, av + 40, y + 40], USER_COLORS[color_idx % len(USER_COLORS)])
        initials = "".join([p[0].upper() for p in user.replace("_", "-").split("-")[:2]])
        draw.text((av + 11, y + 10), initials, font=sans(16),
                  fill=(255, 255, 255))
        # name + time
        nx = av + 52
        draw.text((nx, y), user, font=name_f,
                  fill=USER_COLORS[color_idx % len(USER_COLORS)])
        draw.text((nx + tw(draw, user, name_f) + 12, y + 3), time,
                  font=time_f, fill=palette["muted"])
        # body
        bx = nx
        y += 24
        for line in body:
            fnt = body_font(17, line)
            y = draw_paragraph(draw, bx, y, line, fnt, palette["text"],
                               max_w=W - bx - 60, line_h=24)
        y += 18
        if y > H - 90:
            break

    # input bar
    rect(draw, [side_w + 12, H - 70, W - 24, H - 20], palette["input"],
         outline=palette["border"])
    draw.text((side_w + 28, H - 58), "Message #" + spec["channel"],
              font=sans(15), fill=palette["muted"])
    return img


# --------------------------------------------------------------------------- #
# Content definitions (20 images). All fictional.
# --------------------------------------------------------------------------- #
def specs():
    return [
        # ----------------------------- CODE ------------------------------ #
        dict(
            kind="code", name="code_01",
            category="code",
            project="dejaview-core",
            tabs=["parse.py", "tokenizer.py", "cursor.py"],
            path="dejaview-core/timeline/parse.py",
            tree=[("dejaview-core", 0), ("timeline", 1), ("parse.py", 2),
                  ("tokenizer.py", 2), ("rpc", 1), ("cursor.py", 2),
                  ("tests", 1), ("test_parse.py", 2)],
            lines=[
                "# dejaview-core / timeline parsing",
                "from acme_parser import Tokenizer, LexerError",
                "from lumen_rpc import RemoteCursor",
                "",
                "class TimelineParser:",
                '    """Turn raw capture events into a flat timeline."""',
                "    def __init__(self, endpoint: str = \"local://ipc\"):",
                "        self.tokenizer = Tokenizer()",
                "        self.cursor = RemoteCursor(endpoint)",
                "",
                "    def parse_timeline(self, events):",
                "        tokens = self.tokenizer.lex(events)",
                "        if not tokens:",
                '            raise LexerError("empty timeline batch")',
                "        return [t.as_event() for t in tokens]",
                "",
                "    def window(self, span_ms: int = 5000):",
                "        ordered = sorted(self.events, key=lambda e: e.ts)",
                "        return ordered[-span_ms:]",
                "",
                "if __name__ == \"__main__\":",
                "    parser = TimelineParser(\"local://ipc\")",
                "    print(parser.parse_timeline([]))",
            ],
            ground_truth=dict(
                category="code",
                text_snippets=[
                    "def parse_timeline(self, events):",
                    "from acme_parser import Tokenizer, LexerError",
                    "class TimelineParser:",
                    "from lumen_rpc import RemoteCursor",
                ],
                identifiers=["parse_timeline", "TimelineParser", "acme_parser",
                             "Tokenizer", "LexerError", "lumen_rpc",
                             "RemoteCursor"],
                urls=[], error_codes=[], numbers=["5000"],
            ),
        ),
        dict(
            kind="code", name="code_02",
            category="code",
            project="acme-parser",
            tabs=["lexer.rs", "ast.rs", "errors.rs"],
            path="acme-parser/src/lexer.rs",
            tree=[("acme-parser", 0), ("src", 1), ("lexer.rs", 2),
                  ("ast.rs", 2), ("errors.rs", 2), ("Cargo.toml", 1)],
            lines=[
                "// acme-parser: hand-written lexer",
                "use crate::ast::{Token, Span};",
                "use crate::errors::{AcmeError, Result};",
                "",
                "pub struct Lexer<'a> {",
                "    src: &'a str,",
                "    pos: usize,",
                "}",
                "",
                "impl<'a> Lexer<'a> {",
                "    pub fn new(src: &'a str) -> Self {",
                "        Lexer { src, pos: 0 }",
                "    }",
                "",
                "    pub fn next_token(&mut self) -> Result<Token> {",
                "        self.skip_whitespace();",
                "        if self.pos >= self.src.len() {",
                "            return Ok(Token::Eof);",
                "        }",
                "        let span = Span::at(self.pos, self.pos + 1);",
                "        Err(AcmeError::UnexpectedChar(span))",
                "    }",
                "}",
            ],
            ground_truth=dict(
                category="code",
                text_snippets=[
                    "pub fn next_token(&mut self) -> Result<Token> {",
                    "use crate::ast::{Token, Span};",
                    "pub struct Lexer<'a> {",
                    "use crate::errors::{AcmeError, Result};",
                ],
                identifiers=["Lexer", "next_token", "Token", "Span", "AcmeError",
                             "UnexpectedChar", "acme_parser", "Lexer"],
                urls=[], error_codes=[], numbers=[],
            ),
        ),
        dict(
            kind="code", name="code_03",
            category="code",
            project="lumen-rpc",
            tabs=["client.ts", "transport.ts", "index.ts"],
            path="lumen-rpc/src/client.ts",
            tree=[("lumen-rpc", 0), ("src", 1), ("client.ts", 2),
                  ("transport.ts", 2), ("index.ts", 2), ("package.json", 1)],
            lines=[
                "// lumen-rpc: typed JSON-RPC client",
                "import { Transport } from \"./transport\";",
                "import type { RpcRequest, RpcResponse } from \"./types\";",
                "",
                "export class LumenClient {",
                "  private nextId = 1;",
                "  constructor(private transport: Transport) {}",
                "",
                "  async call<T>(method: string, params: unknown): Promise<T> {",
                "    const req: RpcRequest = {",
                "      id: this.nextId++,",
                "      method,",
                "      params,",
                "    };",
                "    const res: RpcResponse = await this.transport.send(req);",
                "    if (res.error) {",
                "      throw new Error(`lumen-rpc: ${res.error.message}`);",
                "    }",
                "    return res.result as T;",
                "  }",
                "}",
            ],
            ground_truth=dict(
                category="code",
                text_snippets=[
                    "export class LumenClient {",
                    "async call<T>(method: string, params: unknown): Promise<T> {",
                    "import { Transport } from \"./transport\";",
                    "import type { RpcRequest, RpcResponse } from \"./types\";",
                ],
                identifiers=["LumenClient", "Transport", "RpcRequest", "RpcResponse",
                             "lumen_rpc", "call"],
                urls=[], error_codes=[], numbers=["1"],
            ),
        ),
        dict(
            kind="code", name="code_04",
            category="code",
            project="zephyr-index",
            tabs=["index.py", "store.py", "query.py"],
            path="zephyr-index/src/index.py",
            tree=[("zephyr-index", 0), ("src", 1), ("index.py", 2),
                  ("store.py", 2), ("query.py", 2)],
            lines=[
                "# zephyr-index: HNSW-ish vector index",
                "from dataclasses import dataclass",
                "import numpy as np",
                "",
                "@dataclass",
                "class VectorEntry:",
                "    eid: int",
                "    vec: np.ndarray",
                "",
                "class ZephyrIndex:",
                "    def __init__(self, dim: int = 1024):",
                "        self.dim = dim",
                "        self.entries: list[VectorEntry] = []",
                "",
                "    def add(self, eid: int, vec: np.ndarray):",
                "        assert vec.shape == (self.dim,)",
                "        self.entries.append(VectorEntry(eid, vec))",
                "",
                "    def search(self, query: np.ndarray, k: int = 10):",
                "        scores = [(e.eid, float(np.dot(e.vec, query)))",
                "                  for e in self.entries]",
                "        scores.sort(key=lambda t: t[1], reverse=True)",
                "        return scores[:k]",
            ],
            ground_truth=dict(
                category="code",
                text_snippets=[
                    "class ZephyrIndex:",
                    "def add(self, eid: int, vec: np.ndarray):",
                    "def search(self, query: np.ndarray, k: int = 10):",
                    "class VectorEntry:",
                ],
                identifiers=["ZephyrIndex", "VectorEntry", "zephyr_index", "add",
                             "search"],
                urls=[], error_codes=[], numbers=["1024", "10"],
            ),
        ),
        dict(
            kind="code", name="code_05",
            category="code",
            project="nova-cipher",
            tabs=["signing.go", "keys.go", "go.mod"],
            path="nova-cipher/signing/signing.go",
            tree=[("nova-cipher", 0), ("signing", 1), ("signing.go", 2),
                  ("keys.go", 2), ("go.mod", 1)],
            lines=[
                "// nova-cipher: ed25519 envelope signing",
                "package signing",
                "",
                "import (",
                '    "crypto/ed25519"',
                '    "nova-cipher/keys"',
                ")",
                "",
                "type Signer struct {",
                "    priv ed25519.PrivateKey",
                "}",
                "",
                "func NewSigner(km *keys.KeyManager) (*Signer, error) {",
                "    priv, err := km.Primary()",
                "    if err != nil {",
                "        return nil, err",
                "    }",
                "    return &Signer{priv: priv}, nil",
                "}",
                "",
                "func (s *Signer) Sign(payload []byte) []byte {",
                "    return ed25519.Sign(s.priv, payload)",
                "}",
            ],
            ground_truth=dict(
                category="code",
                text_snippets=[
                    "func NewSigner(km *keys.KeyManager) (*Signer, error) {",
                    "func (s *Signer) Sign(payload []byte) []byte {",
                    "type Signer struct {",
                    "package signing",
                ],
                identifiers=["Signer", "NewSigner", "Sign", "KeyManager",
                             "nova_cipher", "signing"],
                urls=[], error_codes=[], numbers=[],
            ),
        ),

        # --------------------------- TERMINAL ---------------------------- #
        dict(
            kind="terminal", name="terminal_01",
            category="terminal",
            title="dejaview@macbook: cargo",
            light=False,
            lines=[
                [("dejaview@macbook ", "prompt"), ("~/dev/dejaview-core ", "path"),
                 ("cargo run --release --bin dejaview-core", "text")],
                [("   Compiling dejaview-core v0.8.2", "dim")],
                [("   Compiling acme-parser v0.4.1", "dim")],
                [("error: ROCM-4042: HIP buffer alloc failed "
                  "(requested 2048 MiB, available 1024 MiB)", "err")],
                [("  --> src/gpu/hip_alloc.rs:142:17", "dim")],
                [("   |", "dim")],
                [("142 |     let buf = device.alloc(bytes)?;", "text")],
                [("   |                 ^^^^^^^^^^^^^^^^ "
                  "HIP runtime out of memory", "err")],
                [("   |", "dim")],
                [("   = note: see https://docs.demo-acme.io/errors/ROCM-4042",
                  "accent")],
                [("error: could not compile `dejaview-core` due to previous "
                  "error", "err")],
                [("", "text")],
                [("dejaview@macbook ", "prompt"), ("~/dev/dejaview-core ", "path"),
                 ("$", "text")],
            ],
            ground_truth=dict(
                category="terminal",
                text_snippets=[
                    "error: ROCM-4042: HIP buffer alloc failed "
                    "(requested 2048 MiB, available 1024 MiB)",
                    "https://docs.demo-acme.io/errors/ROCM-4042",
                    "let buf = device.alloc(bytes)?;",
                ],
                identifiers=["dejaview-core", "acme-parser", "hip_alloc"],
                urls=["https://docs.demo-acme.io/errors/ROCM-4042"],
                error_codes=["ROCM-4042"],
                numbers=["2048", "1024", "142"],
            ),
        ),
        dict(
            kind="terminal", name="terminal_02",
            category="terminal",
            title="dejaview@macbook: python",
            light=False,
            lines=[
                [("dejaview@macbook ", "prompt"), ("~/dev/acme-parser ", "path"),
                 ("python -m acme_parser.cli ingest ./fixtures", "text")],
                [("Traceback (most recent call last):", "err")],
                [('  File "/Users/dev/acme-parser/src/cli.py", line 58, '
                  'in <module>', "dim")],
                [("    from acme_parser import ingest_pipeline", "text")],
                [('  File "/Users/dev/acme-parser/src/acme_parser/__init__.py", '
                  'line 12, in <module>', "dim")],
                [("    from ._native import NativeLex", "text")],
                [("ImportError: libacme.so.3: cannot open shared object file: "
                  "No such file or directory", "err")],
                [("", "text")],
                [("During handling of the above exception, another exception "
                  "occurred:", "warn")],
                [("ImportError: ACME-7781: native backend missing "
                  "(expected libacme.so.3)", "err")],
                [("hint: run `scripts/bootstrap.sh` or see "
                  "https://docs.demo-acme.io/errors/ACME-7781", "accent")],
                [("dejaview@macbook ", "prompt"), ("~/dev/acme-parser ", "path"),
                 ("$", "text")],
            ],
            ground_truth=dict(
                category="terminal",
                text_snippets=[
                    "ImportError: libacme.so.3: cannot open shared object file: "
                    "No such file or directory",
                    "ImportError: ACME-7781: native backend missing "
                    "(expected libacme.so.3)",
                    "https://docs.demo-acme.io/errors/ACME-7781",
                ],
                identifiers=["acme_parser", "ingest_pipeline", "NativeLex",
                             "libacme"],
                urls=["https://docs.demo-acme.io/errors/ACME-7781"],
                error_codes=["ACME-7781"],
                numbers=["58", "12"],
            ),
        ),
        dict(
            kind="terminal", name="terminal_03",
            category="terminal",
            title="dejaview@macbook: go",
            light=False,
            lines=[
                [("dejaview@macbook ", "prompt"), ("~/dev/nova-cipher ", "path"),
                 ("go test ./signing/... -run TestVerify", "text")],
                [("=== RUN   TestVerifyEnvelope", "dim")],
                [("    signing_test.go:91: verifying payload #7", "dim")],
                [("panic: runtime error: index out of range [5] with "
                  "length 3", "err")],
                [("", "text")],
                [("goroutine 1 [running]:", "err")],
                [("nova-cipher/signing.verifyBatch({0xc000200000?, 0x3, 0x3})",
                  "dim")],
                [("    /Users/dev/nova-cipher/signing/verify.go:44 +0x1a2",
                  "dim")],
                [("nova-cipher/signing.TestVerifyEnvelope(0xc00010e680)",
                  "dim")],
                [("    /Users/dev/nova-cipher/signing/signing_test.go:91 "
                  "+0x2f1", "dim")],
                [("exit status 2", "err")],
                [("FAIL    nova-cipher/signing  0.412s  "
                  "(NOVA-9012: panicking test)", "err")],
                [("see https://docs.demo-acme.io/errors/NOVA-9012", "accent")],
            ],
            ground_truth=dict(
                category="terminal",
                text_snippets=[
                    "panic: runtime error: index out of range [5] with "
                    "length 3",
                    "NOVA-9012: panicking test",
                    "https://docs.demo-acme.io/errors/NOVA-9012",
                ],
                identifiers=["nova-cipher", "verifyBatch", "TestVerifyEnvelope",
                             "verify"],
                urls=["https://docs.demo-acme.io/errors/NOVA-9012"],
                error_codes=["NOVA-9012"],
                numbers=["44", "91", "0.412"],
            ),
        ),
        dict(
            kind="terminal", name="terminal_04",
            category="terminal",
            title="dejaview@macbook: grpcurl",
            light=True,
            lines=[
                [("dejaview@macbook ", "prompt"), ("~/dev/lumen-rpc ", "path"),
                 ("grpcurl -plaintext -d '{\"q\":\"hi\"}' 127.0.0.1:8004 "
                  "lumen.Query/Search", "text")],
                [("Error invoking RPC: LUMEN-5563: connection refused "
                  "(target 127.0.0.1:8004)", "err")],
                [("debug: dial tcp 127.0.0.1:8004: connect: connection refused",
                  "dim")],
                [("context: deadline exceeded after 3.000s", "warn")],
                [("", "text")],
                [("LUMEN-5563: no lumen-rpc gateway listening on 8004", "err")],
                [("remediation:", "dim")],
                [("  1) start the gateway:  compose up gateway", "accent")],
                [("  2) check the firewall rules for 8004", "accent")],
                [("docs: https://docs.demo-acme.io/errors/LUMEN-5563",
                  "accent")],
                [("dejaview@macbook ", "prompt"), ("~/dev/lumen-rpc ", "path"),
                 ("$", "text")],
            ],
            ground_truth=dict(
                category="terminal",
                text_snippets=[
                    "LUMEN-5563: connection refused (target 127.0.0.1:8004)",
                    "LUMEN-5563: no lumen-rpc gateway listening on 8004",
                    "https://docs.demo-acme.io/errors/LUMEN-5563",
                ],
                identifiers=["lumen-rpc", "lumen", "gateway", "grpcurl"],
                urls=["https://docs.demo-acme.io/errors/LUMEN-5563"],
                error_codes=["LUMEN-5563"],
                numbers=["8004", "3.000"],
            ),
        ),
        dict(
            kind="terminal", name="terminal_05",
            category="terminal",
            title="dejaview@macbook: openssl",
            light=True,
            lines=[
                [("dejaview@macbook ", "prompt"), ("~/dev/zephyr-index ", "path"),
                 ("zephyr verify --key release.pub bundle.zf", "text")],
                [("loading bundle.zf ... 412 chunks", "dim")],
                [("verifying signature ...", "dim")],
                [("error: ZEPHYR-3300: signature verification failed", "err")],
                [("  expected issuer: zephyr-index/release", "dim")],
                [("  found issuer:    unknown/3f2a1c0b", "dim")],
                [("  pubkey alg:      ed25519", "dim")],
                [("  sig bytes:       9c 4f 22 10 7a 88 ... 0e", "dim")],
                [("", "text")],
                [("ZEPHYR-3300: bundle rejected (signature mismatch)", "err")],
                [("reference: https://docs.demo-acme.io/errors/ZEPHYR-3300",
                  "accent")],
                [("hint: rotate keys via `zephyr keys rotate` then re-sign "
                  "the bundle", "accent")],
                [("exit code 1", "err")],
            ],
            ground_truth=dict(
                category="terminal",
                text_snippets=[
                    "error: ZEPHYR-3300: signature verification failed",
                    "ZEPHYR-3300: bundle rejected (signature mismatch)",
                    "https://docs.demo-acme.io/errors/ZEPHYR-3300",
                ],
                identifiers=["zephyr", "zephyr-index", "verify"],
                urls=["https://docs.demo-acme.io/errors/ZEPHYR-3300"],
                error_codes=["ZEPHYR-3300"],
                numbers=["412", "3300"],
            ),
        ),

        # ---------------------------- WEBPAGE ---------------------------- #
        dict(
            kind="webpage", name="webpage_01",
            category="webpage",
            dark=False,
            tab_title="DejaView 设计文档 · docs.demo-acme.io",
            url="https://docs.demo-acme.io/zh/design/timeline.html",
            blocks=[
                ("meta", [("Updated 2026-07-15", "muted"),
                          ("#142 replies", "link"),
                          ("v0.8.2", "muted")]),
                ("h1", "DejaView 时间线设计 / Timeline Design Notes"),
                ("p", "DejaView 把屏幕活动建模为一条单调递增的事件流 "
                      "(timeline)。每张截图经过 OCR 与语义理解后产出一条原子事件,"
                      "事件按时间戳排序写入 Postgres。"),
                ("p", "Timeline is modeled as a strictly monotonic event "
                      "stream. Each screenshot becomes one atomic event after "
                      "OCR + perception, persisted to Postgres with an HNSW "
                      "embedding index."),
                ("h2", "关键指标 / Key Metrics"),
                ("stat", [("412", "events captured today"),
                          ("98.2%", "OCR block confidence (avg)"),
                          ("8 ms", "embedding latency p50")]),
                ("h2", "相关链接 / See Also"),
                ("link", "https://docs.demo-acme.io/zh/design/sentinel.html"),
                ("link", "https://docs.demo-acme.io/en/api/memoryd.html"),
                ("chip", ["Postgres", "HNSW", "PaddleOCR", "Qwen3-Embedding"]),
            ],
            ground_truth=dict(
                category="webpage",
                text_snippets=[
                    "DejaView 时间线设计 / Timeline Design Notes",
                    "DejaView 把屏幕活动建模为一条单调递增的事件流 (timeline)。",
                ],
                identifiers=[],
                urls=["https://docs.demo-acme.io/zh/design/timeline.html",
                      "https://docs.demo-acme.io/zh/design/sentinel.html",
                      "https://docs.demo-acme.io/en/api/memoryd.html"],
                error_codes=[],
                numbers=["2026-07-15", "142", "v0.8.2", "412", "98.2", "8"],
            ),
        ),
        dict(
            kind="webpage", name="webpage_02",
            category="webpage",
            dark=False,
            tab_title="acme-parser CHANGELOG · 0.4.1",
            url="https://docs.demo-acme.io/en/acme-parser/changelog.html",
            blocks=[
                ("meta", [("Updated 2026-07-09", "muted"),
                          ("#37 issues", "link"),
                          ("MIT licensed", "muted")]),
                ("h1", "acme-parser CHANGELOG"),
                ("h2", "v0.4.1 - 2026-07-09"),
                ("p", "Fixed a tokenizer crash on empty input and added "
                      "streaming lex mode. Memory footprint reduced by "
                      "roughly 18% on the dejaview-core capture workload."),
                ("code", ["- Lexer::next_token() now returns Eof instead of "
                          "panicking on empty input",
                          "+ Lexer::with_streaming() for chunked input",
                          "* token table size: 64KB -> 52KB"]),
                ("h2", "下载 / Downloads"),
                ("link", "https://docs.demo-acme.io/dl/acme-parser-0.4.1.tar.gz"),
                ("link", "https://docs.demo-acme.io/dl/acme-parser-0.4.1.sig"),
                ("stat", [("37", "open issues"),
                          ("412", "stars"),
                          ("18%", "memory reduction")]),
            ],
            ground_truth=dict(
                category="webpage",
                text_snippets=[
                    "acme-parser CHANGELOG",
                    "Lexer::next_token() now returns Eof instead of panicking "
                    "on empty input",
                ],
                identifiers=["acme-parser", "Lexer"],
                urls=["https://docs.demo-acme.io/en/acme-parser/changelog.html",
                      "https://docs.demo-acme.io/dl/acme-parser-0.4.1.tar.gz",
                      "https://docs.demo-acme.io/dl/acme-parser-0.4.1.sig"],
                error_codes=[],
                numbers=["2026-07-09", "37", "0.4.1", "412", "18", "64", "52"],
            ),
        ),
        dict(
            kind="webpage", name="webpage_03",
            category="webpage",
            dark=True,
            tab_title="lumen-rpc · 基准测试 Benchmarks",
            url="https://bench.demo-acme.io/lumen-rpc/2026-07-15.html",
            blocks=[
                ("meta", [("Generated 2026-07-15", "muted"),
                          ("#9 benchmarks", "link"),
                          ("macOS arm64", "muted")]),
                ("h1", "lumen-rpc 基准测试 / Benchmarks"),
                ("p", "All numbers are synthetic, collected on the "
                      "reference arm64 host with lumen-rpc v1.2.0 talking to "
                      "the local gateway over a unix domain socket."),
                ("h2", "吞吐 / Throughput"),
                ("stat", [("42,910", "req/s (call, p50)"),
                          ("31,204", "req/s (call, p99)"),
                          ("8 ms", "p50 latency")]),
                ("h2", "结果表 / Result Tables"),
                ("code", ["call            p50=8.00ms   p99=21.40ms   "
                          "42910 req/s",
                          "stream           p50=2.10ms   p99=6.80ms    "
                          "88210 msg/s",
                          "batch(n=64)      p50=12.4ms   p99=33.1ms    "
                          "5170 batch/s"]),
                ("link", "https://bench.demo-acme.io/lumen-rpc/raw.csv"),
                ("link", "https://docs.demo-acme.io/en/lumen-rpc/method.html"),
            ],
            ground_truth=dict(
                category="webpage",
                text_snippets=[
                    "lumen-rpc 基准测试 / Benchmarks",
                    "call            p50=8.00ms   p99=21.40ms   42910 req/s",
                ],
                identifiers=["lumen-rpc"],
                urls=["https://bench.demo-acme.io/lumen-rpc/2026-07-15.html",
                      "https://bench.demo-acme.io/lumen-rpc/raw.csv",
                      "https://docs.demo-acme.io/en/lumen-rpc/method.html"],
                error_codes=[],
                numbers=["2026-07-15", "1.2.0", "42,910", "31,204", "8",
                         "88,210", "5,170", "64"],
            ),
        ),
        dict(
            kind="webpage", name="webpage_04",
            category="webpage",
            dark=False,
            tab_title="zephyr-index FAQ · 常见问题",
            url="https://docs.demo-acme.io/zh/zephyr-index/faq.html",
            blocks=[
                ("meta", [("Updated 2026-06-30", "muted"),
                          ("#58 replies", "link")]),
                ("h1", "zephyr-index 常见问题 / FAQ"),
                ("h2", "Q1: 向量维度可以改吗?"),
                ("p", "默认 1024 维 (Qwen3-Embedding-0.6B)。升级到 4B 模型时"
                      "用 MRL 截断回 1024 维,schema 不变,但需要全量重嵌。"),
                ("p", "Default is 1024 dims. Upgrading to the 4B model uses MRL "
                      "truncation back to 1024 so the schema stays the same, "
                      "but a full re-embed is required."),
                ("h2", "Q2: 支持多大数据量?"),
                ("p", "单机 HNSW 实测 412 万向量,召回率 98.2%,p50 查询 "
                      "12 ms。更大规模建议分片。"),
                ("h2", "参考 / References"),
                ("link", "https://docs.demo-acme.io/zh/zephyr-index/tuning.html"),
                ("link", "https://docs.demo-acme.io/zh/zephyr-index/sharding.html"),
                ("chip", ["HNSW", "Qwen3-Embedding", "MRL", "1024-dim"]),
            ],
            ground_truth=dict(
                category="webpage",
                text_snippets=[
                    "zephyr-index 常见问题 / FAQ",
                    "默认 1024 维 (Qwen3-Embedding-0.6B)。",
                ],
                identifiers=["zephyr-index", "Qwen3-Embedding", "HNSW"],
                urls=["https://docs.demo-acme.io/zh/zephyr-index/faq.html",
                      "https://docs.demo-acme.io/zh/zephyr-index/tuning.html",
                      "https://docs.demo-acme.io/zh/zephyr-index/sharding.html"],
                error_codes=[],
                numbers=["2026-06-30", "58", "1024", "4", "412", "98.2", "12"],
            ),
        ),
        dict(
            kind="webpage", name="webpage_05",
            category="webpage",
            dark=True,
            tab_title="nova-cipher 安全公告 · SEC-2026-0142",
            url="https://security.demo-acme.io/advisories/SEC-2026-0142.html",
            blocks=[
                ("meta", [("Published 2026-07-11", "muted"),
                          ("severity: high", "muted"),
                          ("CVE-N/A (synthetic)", "muted")]),
                ("h1", "nova-cipher 安全公告 / Advisory SEC-2026-0142"),
                ("p", "A synthetic advisory for the nova-cipher signing "
                      "path. Affected versions: nova-cipher >= 0.3.0 and "
                      "< 0.3.4. The Verify path did not check the issuer "
                      "field on ed25519 envelopes."),
                ("h2", "受影响版本 / Affected"),
                ("code", ["nova-cipher >= 0.3.0, < 0.3.4",
                          "fix: 0.3.4 (released 2026-07-11)",
                          "credits: sam_qa (synthetic reporter)"]),
                ("h2", "升级 / Upgrade"),
                ("p", "升级到 0.3.4 或以上,然后运行 "
                      "`zephyr keys rotate` 轮换密钥。"),
                ("link", "https://docs.demo-acme.io/en/nova-cipher/upgrade.html"),
                ("link", "https://security.demo-acme.io/diffs/nova-0.3.4.patch"),
                ("stat", [("0.3.4", "fixed version"),
                          ("3", "affected releases"),
                          ("142", "advisory id")]),
            ],
            ground_truth=dict(
                category="webpage",
                text_snippets=[
                    "nova-cipher 安全公告 / Advisory SEC-2026-0142",
                    "nova-cipher >= 0.3.0, < 0.3.4",
                ],
                identifiers=["nova-cipher", "zephyr"],
                urls=["https://security.demo-acme.io/advisories/SEC-2026-0142.html",
                      "https://docs.demo-acme.io/en/nova-cipher/upgrade.html",
                      "https://security.demo-acme.io/diffs/nova-0.3.4.patch"],
                error_codes=["SEC-2026-0142"],
                numbers=["2026-07-11", "0.3.0", "0.3.4", "3", "142"],
            ),
        ),

        # ----------------------------- CHAT ------------------------------ #
        dict(
            kind="chat", name="chat_01",
            category="chat",
            dark=False,
            workspace="dejaview-core",
            subhead="alex_w  ·  4 members",
            channels=[
                ("Channels", [
                    ("general", False, False),
                    ("dejaview-core", True, False),
                    ("sentinel", False, True),
                    ("errors", False, False),
                ]),
                ("Direct", [("direct", False, False)]),
            ],
            channel="dejaview-core",
            topic="timeline parsing + ROCm build",
            messages=[
                ("alex_w", 1, "09:14",
                 ["hey, the release build just blew up on the HIP buffer"]),
                ("alex_w", 1, "09:14",
                 ["error: ROCM-4042: HIP buffer alloc failed, asked for "
                  "2048 MiB but only 1024 free"]),
                ("morgan_dev", 2, "09:16",
                 ["seen that before, the sentinel is holding the other "
                  "half of VRAM"]),
                ("morgan_dev", 2, "09:16",
                 ["gate the sentinel to 512 MiB and rebuild, then it fits"]),
                ("sam_qa", 3, "09:21",
                 ["logged it as NOVA-9012 in the synthetic tracker, linking "
                  "https://docs.demo-acme.io/errors/ROCM-4042"]),
                ("alex_w", 1, "09:23",
                 ["thanks, rebuilding now with the cap"]),
            ],
            ground_truth=dict(
                category="chat",
                text_snippets=[
                    "error: ROCM-4042: HIP buffer alloc failed, asked for "
                    "2048 MiB but only 1024 free",
                    "https://docs.demo-acme.io/errors/ROCM-4042",
                    "logged it as NOVA-9012 in the synthetic tracker",
                ],
                identifiers=["alex_w", "morgan_dev", "sam_qa", "dejaview-core",
                             "sentinel"],
                urls=["https://docs.demo-acme.io/errors/ROCM-4042"],
                error_codes=["ROCM-4042", "NOVA-9012"],
                numbers=["2048", "1024", "512"],
            ),
        ),
        dict(
            kind="chat", name="chat_02",
            category="chat",
            dark=True,
            workspace="acme-parser",
            subhead="jordan_ops  ·  6 members",
            channels=[
                ("Channels", [
                    ("general", False, False),
                    ("builds", True, True),
                    ("ingest", False, False),
                    ("native", False, False),
                ]),
                ("Direct", [("direct", False, False)]),
            ],
            channel="builds",
            topic="CI is red on main",
            messages=[
                ("jordan_ops", 4, "11:02",
                 ["CI red on main again, libacme.so.3 missing in the "
                  "container image"]),
                ("jordan_ops", 4, "11:02",
                 ["ImportError: libacme.so.3: cannot open shared object file"]),
                ("casey_eng", 5, "11:05",
                 ["yep, the bootstrap script did not pin the .so version"]),
                ("casey_eng", 5, "11:06",
                 ["opened ACME-7781, fix is in PR 142"]),
                ("riley_ml", 6, "11:09",
                 ["also got LUMEN-5563 once the ingest worker started, "
                  "gateway was down on 8004"]),
                ("jordan_ops", 4, "11:11",
                 ["merging the pin, rebuilding image now"]),
            ],
            ground_truth=dict(
                category="chat",
                text_snippets=[
                    "ImportError: libacme.so.3: cannot open shared object file",
                    "opened ACME-7781, fix is in PR 142",
                    "also got LUMEN-5563 once the ingest worker started",
                ],
                identifiers=["jordan_ops", "casey_eng", "riley_ml",
                             "acme-parser", "libacme"],
                urls=[],
                error_codes=["ACME-7781", "LUMEN-5563"],
                numbers=["3", "142", "8004"],
            ),
        ),
        dict(
            kind="chat", name="chat_03",
            category="chat",
            dark=False,
            workspace="nova-cipher",
            subhead="taylor_build  ·  3 members",
            channels=[
                ("Channels", [
                    ("general", False, False),
                    ("signing", True, False),
                    ("security", False, True),
                    ("releases", False, False),
                ]),
                ("Direct", [("direct", False, False)]),
            ],
            channel="signing",
            topic="verify panic on main",
            messages=[
                ("taylor_build", 0, "14:40",
                 ["TestVerifyEnvelope panics on main, looks like an index bug"]),
                ("taylor_build", 0, "14:40",
                 ["panic: runtime error: index out of range [5] with "
                  "length 3"]),
                ("casey_eng", 5, "14:43",
                 ["verifyBatch is iterating past the end of the 3-element "
                  "slice, off by a couple"]),
                ("casey_eng", 5, "14:44",
                 ["fix in PR 91, also filed NOVA-9012"]),
                ("alex_w", 1, "14:50",
                 ["lgtm, ship 0.3.4 after it merges"]),
            ],
            ground_truth=dict(
                category="chat",
                text_snippets=[
                    "panic: runtime error: index out of range [5] with "
                    "length 3",
                    "verifyBatch is iterating past the end of the 3-element "
                    "slice",
                    "fix in PR 91, also filed NOVA-9012",
                ],
                identifiers=["taylor_build", "casey_eng", "alex_w",
                             "nova-cipher", "verifyBatch", "TestVerifyEnvelope"],
                urls=[],
                error_codes=["NOVA-9012"],
                numbers=["5", "3", "91", "0.3.4"],
            ),
        ),
        dict(
            kind="chat", name="chat_04",
            category="chat",
            dark=False,
            workspace="zephyr-index",
            subhead="riley_ml  ·  5 members",
            channels=[
                ("Channels", [
                    ("general", False, False),
                    ("indexing", True, False),
                    ("security", False, True),
                    ("bench", False, False),
                ]),
                ("Direct", [("direct", False, False)]),
            ],
            channel="indexing",
            topic="bundle signature mismatch",
            messages=[
                ("riley_ml", 6, "10:05",
                 ["zephyr verify is rejecting the release bundle"]),
                ("riley_ml", 6, "10:05",
                 ["ZEPHYR-3300: signature verification failed, issuer "
                  "mismatch"]),
                ("morgan_dev", 2, "10:08",
                 ["the bundle was signed with the old key, we rotated on "
                  "2026-06-30"]),
                ("morgan_dev", 2, "10:09",
                 ["re-sign with release.pub and it should verify"]),
                ("sam_qa", 3, "10:12",
                 ["adding a rotation note to "
                  "https://docs.demo-acme.io/zh/zephyr-index/faq.html"]),
            ],
            ground_truth=dict(
                category="chat",
                text_snippets=[
                    "ZEPHYR-3300: signature verification failed, issuer "
                    "mismatch",
                    "the bundle was signed with the old key",
                    "https://docs.demo-acme.io/zh/zephyr-index/faq.html",
                ],
                identifiers=["riley_ml", "morgan_dev", "sam_qa",
                             "zephyr-index", "zephyr"],
                urls=["https://docs.demo-acme.io/zh/zephyr-index/faq.html"],
                error_codes=["ZEPHYR-3300"],
                numbers=["2026-06-30", "3300"],
            ),
        ),
        dict(
            kind="chat", name="chat_05",
            category="chat",
            dark=True,
            workspace="lumen-rpc",
            subhead="jordan_ops  ·  4 members",
            channels=[
                ("Channels", [
                    ("general", False, False),
                    ("gateway", True, True),
                    ("bench", False, False),
                    ("incidents", False, False),
                ]),
                ("Direct", [("direct", False, False)]),
            ],
            channel="gateway",
            topic="gateway down on 8004",
            messages=[
                ("jordan_ops", 4, "16:20",
                 ["queries are timing out, gateway not listening on 8004"]),
                ("jordan_ops", 4, "16:20",
                 ["LUMEN-5563: connection refused (target 127.0.0.1:8004)"]),
                ("taylor_build", 0, "16:23",
                 ["compose up gateway was skipped in the deploy, restarting "
                  "now"]),
                ("taylor_build", 0, "16:25",
                 ["back up, p50 latency back to 8 ms"]),
                ("casey_eng", 5, "16:30",
                 ["postmortem going in "
                  "https://docs.demo-acme.io/en/lumen-rpc/method.html"]),
            ],
            ground_truth=dict(
                category="chat",
                text_snippets=[
                    "LUMEN-5563: connection refused (target 127.0.0.1:8004)",
                    "compose up gateway was skipped in the deploy",
                    "https://docs.demo-acme.io/en/lumen-rpc/method.html",
                ],
                identifiers=["jordan_ops", "taylor_build", "casey_eng",
                             "lumen-rpc", "gateway"],
                urls=["https://docs.demo-acme.io/en/lumen-rpc/method.html"],
                error_codes=["LUMEN-5563"],
                numbers=["8004", "8"],
            ),
        ),
    ]


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def build_image(spec):
    if spec["kind"] == "code":
        return render_code(spec)
    if spec["kind"] == "terminal":
        return render_terminal(spec)
    if spec["kind"] == "webpage":
        return render_webpage(spec)
    if spec["kind"] == "chat":
        return render_chat(spec)
    raise ValueError("unknown kind: " + spec["kind"])


def main():
    for spec in specs():
        img = build_image(spec)
        png_path = os.path.join(OUT_DIR, spec["name"] + ".png")
        img.save(png_path, "PNG")
        gt = dict(spec["ground_truth"])
        gt["image"] = spec["name"] + ".png"
        gt["category"] = spec["category"]
        gt.setdefault("text_snippets", [])
        gt.setdefault("identifiers", [])
        gt.setdefault("urls", [])
        gt.setdefault("error_codes", [])
        gt.setdefault("numbers", [])
        json_path = os.path.join(OUT_DIR, spec["name"] + ".json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(gt, fh, ensure_ascii=False, indent=2)
        print("wrote", png_path, "+", json_path)
    print("done:", len(specs()), "images")


if __name__ == "__main__":
    main()
