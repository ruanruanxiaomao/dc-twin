# -*- coding: utf-8 -*-
"""A compact Markdown renderer for Tk Text (headings, lists, tables, code)."""

from __future__ import annotations

import re
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import ttk

from . import theme as T

INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*]+\*)")


class MarkdownView(tk.Frame):
    def __init__(self, parent, bg: str = T.PANEL):
        super().__init__(parent, bg=bg)
        self.text = tk.Text(self, bg=bg, fg=T.TEXT, insertbackground=T.TEXT,
                            selectbackground=T.PANEL_HI, selectforeground=T.TEXT,
                            relief="flat", bd=0, wrap="word", padx=26, pady=18,
                            spacing1=1, spacing3=3, cursor="arrow",
                            font=T.font(10))
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.vsb.set)
        self.text.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.text.configure(state="disabled")
        self.scale = 1.0
        self.headings: list[tuple[int, int, str]] = []
        self._configure_tags()
        self.text.bind("<Key>", self._readonly_key)
        self._hit_indexes: list[str] = []
        self._hit_pos = 0

    # ------------------------------------------------------------ tags
    def _fs(self, size: int) -> int:
        return max(7, int(round(size * self.scale)))

    def _configure_tags(self) -> None:
        t = self.text
        t.tag_configure("h1", font=(T.FONT_FAMILY, self._fs(19), "bold"),
                        foreground=T.TEXT, spacing1=self._fs(16), spacing3=self._fs(6))
        t.tag_configure("h2", font=(T.FONT_FAMILY, self._fs(14), "bold"),
                        foreground=T.ACCENT, spacing1=self._fs(14), spacing3=self._fs(4))
        t.tag_configure("h3", font=(T.FONT_FAMILY, self._fs(11), "bold"),
                        foreground=T.TEXT, spacing1=self._fs(10), spacing3=self._fs(3))
        t.tag_configure("h4", font=(T.FONT_FAMILY, self._fs(10), "bold"),
                        foreground=T.PURPLE, spacing1=self._fs(8))
        t.tag_configure("body", font=T.font(self._fs(10)), foreground=T.TEXT,
                        lmargin1=2, lmargin2=2, spacing3=self._fs(3))
        t.tag_configure("bullet", font=T.font(self._fs(10)), foreground=T.TEXT,
                        lmargin1=self._fs(18), lmargin2=self._fs(32), spacing3=1)
        t.tag_configure("num", font=T.font(self._fs(10)), foreground=T.TEXT,
                        lmargin1=self._fs(18), lmargin2=self._fs(32), spacing3=1)
        t.tag_configure("quote", font=T.font(self._fs(10)), foreground=T.MUTED,
                        lmargin1=self._fs(18), lmargin2=self._fs(18), spacing3=2)
        t.tag_configure("code", font=T.mono(self._fs(9)), foreground=T.GREEN,
                        background=T.PANEL_ALT)
        t.tag_configure("pre", font=T.mono(self._fs(9)), foreground="#b9d7ff",
                        background="#0a1322", lmargin1=self._fs(10), lmargin2=self._fs(10),
                        spacing1=2, spacing3=2)
        t.tag_configure("table", font=T.mono(self._fs(9)), foreground=T.MUTED,
                        lmargin1=self._fs(6), spacing3=0)
        t.tag_configure("table_head", font=T.mono(self._fs(9), "bold"), foreground=T.TEXT,
                        lmargin1=self._fs(6), spacing3=0)
        t.tag_configure("link", foreground=T.ACCENT, underline=True)
        t.tag_configure("bold", font=(T.FONT_FAMILY, self._fs(10), "bold"))
        t.tag_configure("italic", font=(T.FONT_FAMILY, self._fs(10), "italic"))
        t.tag_configure("hr", foreground=T.BORDER)
        t.tag_configure("hit", background="#4a3a12", foreground=T.AMBER)

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.headings = []
        self._hit_indexes = []

    # ------------------------------------------------------------ render
    def render(self, text: str) -> None:
        self.clear()
        lines = text.replace("\r\n", "\n").split("\n")
        i = 0
        in_code = False
        code_buf: list[str] = []
        table_buf: list[str] = []

        def flush_table():
            nonlocal table_buf
            if table_buf:
                self._render_table(table_buf)
                table_buf = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("```"):
                if in_code:
                    self._insert("\n".join(code_buf) + "\n", "pre")
                    code_buf = []
                    in_code = False
                else:
                    flush_table()
                    in_code = True
                i += 1
                continue
            if in_code:
                code_buf.append(line)
                i += 1
                continue

            if stripped.startswith("|") and stripped.count("|") >= 2:
                table_buf.append(stripped)
                i += 1
                continue
            flush_table()

            if not stripped:
                self._insert("\n", "body")
                i += 1
                continue

            m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if m:
                level = min(len(m.group(1)), 4)
                title = re.sub(r"[*`]", "", m.group(2)).strip()
                idx = int(self.text.index("end-1c").split(".")[0])
                self.headings.append((idx, level, title))
                self._insert(title + "\n", f"h{level}")
                i += 1
                continue

            if re.match(r"^([-*_])\1{2,}$", stripped):
                self._insert("─" * 64 + "\n", "hr")
                i += 1
                continue

            if stripped.startswith(">"):
                body = stripped.lstrip("> ").strip()
                self._insert_inline("│ " + body + "\n", base="quote")
                i += 1
                continue

            bm = re.match(r"^[-*+]\s+(.*)$", stripped)
            if bm:
                self._insert_inline("• " + bm.group(1) + "\n", base="bullet")
                i += 1
                continue

            nm = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
            if nm:
                self._insert_inline(f"{nm.group(1)}. {nm.group(2)}\n", base="num")
                i += 1
                continue

            self._insert_inline(stripped + "\n", base="body")
            i += 1

        if in_code and code_buf:
            self._insert("\n".join(code_buf) + "\n", "pre")
        flush_table()
        self.text.configure(state="disabled")

    # ------------------------------------------------------------ inline
    def _insert(self, s: str, tag: str = "body") -> None:
        self.text.insert("end", s, tag)

    def _insert_inline(self, s: str, base: str = "body") -> None:
        pos = 0
        for m in INLINE_RE.finditer(s):
            if m.start() > pos:
                self.text.insert("end", s[pos:m.start()], base)
            token = m.group(0)
            if token.startswith("**"):
                self.text.insert("end", token[2:-2], ("bold",))
            elif token.startswith("`"):
                self.text.insert("end", token[1:-1], ("code",))
            elif token.startswith("["):
                lm = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token)
                if lm:
                    label, url = lm.group(1), lm.group(2)
                    tag = f"link_{len(self.text.tag_names())}"
                    self.text.insert("end", label, (tag, "link"))
                    self.text.tag_bind(tag, "<Button-1>", lambda _e, u=url: webbrowser.open(u))
                    self.text.tag_bind(tag, "<Enter>", lambda _e: self.text.configure(cursor="hand2"))
                    self.text.tag_bind(tag, "<Leave>", lambda _e: self.text.configure(cursor="arrow"))
                else:
                    self.text.insert("end", token, base)
            elif token.startswith("*") and token.endswith("*") and len(token) > 2:
                self.text.insert("end", token[1:-1], ("italic",))
            else:
                self.text.insert("end", token, base)
            pos = m.end()
        if pos < len(s):
            self.text.insert("end", s[pos:], base)

    # ------------------------------------------------------------ tables
    def _table_font(self) -> tkfont.Font:
        size = self._fs(9)
        font = getattr(self, "_font_cache", None)
        if font is None:
            self._font_cache = {}
            font = self._font_cache
        key = ("mono", size)
        if key not in font:
            font[key] = tkfont.Font(font=T.mono(size))
        return font[key]

    def _render_table(self, rows: list[str]) -> None:
        parsed: list[list[str]] = []
        for row in rows:
            if re.match(r"^\|[\s:|-]+\|$", row):
                continue
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            parsed.append(cells)
        if not parsed:
            return
        ncol = max(len(r) for r in parsed)
        for r in parsed:
            while len(r) < ncol:
                r.append("")

        measure = self._table_font()
        # A monospace face makes every glyph the same width; CJK falls back to a
        # second face with its own (uniform) width. Measuring two reference
        # glyphs is enough to lay out any cell, and avoids one Tk round-trip per
        # string (the single biggest cost when rendering a long document).
        ascii_w = measure.measure("M") or 7
        wide_w = measure.measure("中") or ascii_w * 2
        char_cache: dict[str, int] = {}

        def glyph_w(ch: str) -> int:
            val = char_cache.get(ch)
            if val is None:
                val = wide_w if ord(ch) > 0x1100 else ascii_w
                char_cache[ch] = val
            return val

        def m(text: str) -> int:
            return sum(glyph_w(ch) for ch in text)

        sep = "   "
        sep_w = m(sep)
        avail = self.text.winfo_width() - 96
        if avail < 320:
            avail = 860

        plain = [[self._plain(c) for c in r] for r in parsed]
        natural = [max(m(r[c]) for r in plain) + 6 for c in range(ncol)]
        # Give every column enough room for a readable header, then hand the
        # remaining width to the long-text columns in proportion to their need.
        min_w = [max(52, min(w, 116)) for w in natural]
        avail_cols = max(avail - sep_w * (ncol - 1), ncol * 44)
        if sum(min_w) >= avail_cols:
            scale = avail_cols / max(sum(min_w), 1)
            widths = [max(44, int(w * scale)) for w in min_w]
        else:
            extra = avail_cols - sum(min_w)
            weights = [max(w - mn, 0) for w, mn in zip(natural, min_w)]
            total_w = sum(weights)
            if total_w <= 0:
                widths = [mn + extra // ncol for mn in min_w]
            else:
                widths = [mn + int(extra * wt / total_w) for mn, wt in zip(min_w, weights)]
            widths[-1] += avail_cols - sum(widths)
        space_w = ascii_w

        def truncate(text: str, width: int) -> str:
            if m(text) <= width - 4:
                return text
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if m(text[:mid] + "…") <= width - 4:
                    lo = mid
                else:
                    hi = mid - 1
            return text[:lo] + "…"

        def pad(text: str, width: int) -> str:
            target = width - 4
            cur = m(text)
            if cur < target:
                text += " " * max(0, int((target - cur) / space_w))
            return text

        for idx, r in enumerate(parsed):
            tag = "table_head" if idx == 0 else "table"
            cells = [pad(truncate(plain[idx][c], widths[c]), widths[c]) for c in range(ncol)]
            self.text.insert("end", sep.join(cells) + "\n", tag)
            if idx == 0:
                dash = "─" * max(3, int(sum(widths) / max(m("─"), 1)))
                self.text.insert("end", dash + "\n", "table")
        self.text.insert("end", "\n", "body")


    @staticmethod
    def _plain(s: str) -> str:
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        s = re.sub(r"`([^`]+)`", r"\1", s)
        s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
        return s

    # ------------------------------------------------------------ helpers
    def _readonly_key(self, event):
        if event.state & 0x4 and event.keysym.lower() in ("c", "a"):
            return None
        if event.keysym in ("Up", "Down", "Prior", "Next", "Home", "End", "Left", "Right"):
            return None
        return "break"

    def scroll_to_line(self, line: int) -> None:
        self.text.see(f"{max(1, line)}.0")
        self.text.tag_remove("focusline", "1.0", "end")

    def search(self, needle: str, forward: bool = True) -> bool:
        self.text.tag_remove("hit", "1.0", "end")
        if not needle:
            self._hit_indexes = []
            return False
        start = "1.0"
        hits = []
        while True:
            pos = self.text.search(needle, start, stopindex="end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(needle)}c"
            self.text.tag_add("hit", pos, end)
            hits.append(pos)
            start = end
        self._hit_indexes = hits
        if not hits:
            return False
        self._hit_pos = 0
        self.text.see(hits[0])
        return True

    def search_next(self) -> None:
        if not self._hit_indexes:
            return
        self._hit_pos = (self._hit_pos + 1) % len(self._hit_indexes)
        self.text.see(self._hit_indexes[self._hit_pos])

    def set_scale(self, scale: float) -> None:
        self.scale = max(0.7, min(1.8, scale))
