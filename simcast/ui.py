"""SimCast's widget kit for Tk: the colours and fonts, and the rounded, anti-aliased pieces plain Tk doesn't have
(buttons, switches, segmented controls, pills and cards). Tk can't blend with what's behind a widget, so every
rounded shape is a small image drawn here against the colour it sits on."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# A calm, near-black interface: neutral greys with a hint of blue, one brand red for what's live or yours,
# and colour only where it means something (the voices, the flags, a car in the pits).
P = {
    "bg": "#0b0c0f",        # the window
    "panel": "#131519",     # cards
    "panel2": "#1a1d22",    # fields, rows inside a card
    "elev": "#22262c",      # a selected segment, a raised control
    "hover": "#272b32",
    "line": "#23272d",      # hairlines and card borders
    "line2": "#30353d",     # control borders
    "fg": "#eceef1",
    "fg2": "#c3c8cf",       # long-form text
    "mut": "#8b939e",       # labels and secondary text
    "dim": "#5d6570",       # hints
    "acc": "#e5484d",       # SimCast red: live, on, yours
    "acc_hi": "#f2585d",
    "acc_tint": "#2a1517",  # your car's row
    "acc_line": "#5c2328",
    "ink": "#0b0c0f",       # text on a light fill
    "white": "#ffffff",
    "green": "#3dd68c", "green_tint": "#13261d",
    "amber": "#f5a524", "amber_tint": "#2b2112",
    "red": "#e5484d", "red_dk": "#b8363b", "red_tint": "#2a1517",
    "purple": "#b38cff", "purple_tint": "#211a33",
    "blue": "#6cb6ff", "blue_tint": "#14202e",
    "rose": "#ff8a93",
}

SCALE = 1.0          # screen pixels per design pixel (set from the screen's DPI by setup())
F: dict = {}         # the named fonts (set by setup())


def px(n):
    return max(1, int(round(n * SCALE)))


def setup(root):
    """The fonts and the DPI scale. Segoe UI on Windows (Variable on 11), Inter or the platform's face elsewhere."""
    global SCALE
    try:
        SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:
        SCALE = 1.0
    fams = set(tkfont.families(root))

    def pick(*names, d="TkDefaultFont"):
        return next((f for f in names if f in fams), d)
    ui = pick("Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", "DejaVu Sans")
    semi = pick("Segoe UI Variable Text Semibold", "Segoe UI Semibold", d="")
    disp = pick("Segoe UI Variable Display Semibold", "Segoe UI Semibold", "Inter Display", d=ui)
    mono = pick("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", d="TkFixedFont")

    def sb(size):                       # semibold: a real semibold face where there is one, bold otherwise
        return (semi, size) if semi else (ui, size, "bold")
    disp_w = () if disp != ui and "Semibold" in disp else ("bold",)
    F.update({
        "ui": (ui, 10), "uib": sb(10), "small": (ui, 9), "smallb": sb(9), "micro": sb(8),
        "btn": sb(10), "btn_s": sb(9), "title": (disp, 15) + disp_w, "h1": (disp, 22) + disp_w,
        "h2": (disp, 15) + disp_w, "h3": sb(11), "track": (disp, 14) + disp_w,
        "tower": (ui, 10), "towerb": sb(10), "tag": sb(7), "num": sb(10),
        "line": (ui, 11), "line2": sb(11), "line3": (disp, 14) + disp_w, "who": sb(10),
        "cap": (disp, 22) + disp_w, "capwho": sb(11), "mono": (mono, 9), "nav": (ui, 10), "navb": sb(10),
    })
    return F


def font(name):
    return tkfont.Font(font=F[name])


# ---------------------------------------------------------------------------------------------- colour helpers
def _rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a, b, t):
    """a over b, t of a."""
    (r1, g1, b1), (r2, g2, b2) = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % (round(r1 * t + r2 * (1 - t)), round(g1 * t + g2 * (1 - t)), round(b1 * t + b2 * (1 - t)))


def bg_of(w):
    """The colour a widget paints its background with, plain Tk or ttk."""
    try:
        return w.cget("background")
    except tk.TclError:
        pass
    try:
        st = ttk.Style(w)
        return st.lookup(w.cget("style") or w.winfo_class(), "background") or P["panel"]
    except Exception:
        return P["panel"]


# ---------------------------------------------------------------------------------------------- rounded images
_CACHE: dict = {}
_SS = 4                                   # samples per pixel edge, for the anti-aliasing


def _corner(r, bw):
    """Coverage of the top-left corner's pixels: (outer, inner) per pixel, 0..1. Inner is the fill inside a border."""
    out = []
    n = _SS * _SS
    for y in range(r):
        row = []
        for x in range(r):
            o = i = 0
            for sy in range(_SS):
                for sx in range(_SS):
                    dx = r - (x + (sx + 0.5) / _SS)
                    dy = r - (y + (sy + 0.5) / _SS)
                    d = (dx * dx + dy * dy) ** 0.5
                    if d <= r:
                        o += 1
                        if d <= r - bw:
                            i += 1
            row.append((o / n, i / n))
        out.append(row)
    return out


def rounded(w, h, r, fill, bg, border=None, bw=1):
    """A w x h rounded rectangle image, anti-aliased against bg, with an optional 1px border."""
    w, h = max(2, int(w)), max(2, int(h))
    r = max(0, min(int(r), w // 2, h // 2))
    key = (w, h, r, fill, bg, border, bw)
    img = _CACHE.get(key)
    if img is not None:
        return img
    edge = border or fill
    cov = _corner(r, bw if border else 0) if r else []

    def px_col(o, i):                     # bg where uncovered, the border's ring, the fill inside it
        if o <= 0:
            return bg
        (r1, g1, b1), (r2, g2, b2), (r3, g3, b3) = _rgb(bg), _rgb(edge), _rgb(fill)
        ring = max(0.0, o - i)
        return "#%02x%02x%02x" % tuple(round(x * (1 - o) + y * ring + z * i) for x, y, z in
                                         ((r1, r2, r3), (g1, g2, g3), (b1, b2, b3)))
    corner_cols = [[px_col(o, i) for (o, i) in row] for row in cov]
    rows = []
    mid = [edge] + [fill] * (w - 2) + [edge] if border else [fill] * w
    top = [edge] * w if border else mid
    for y in range(h):
        if y < r or y >= h - r:
            cy = y if y < r else h - 1 - y
            left = corner_cols[cy]
            line = (top if (border and (y == 0 or y == h - 1)) else mid)[:]
            line[:r] = left
            line[w - r:] = left[::-1]
            if border and (y == 0 or y == h - 1):
                line[r:w - r] = [edge] * (w - 2 * r)
        else:
            line = mid
        rows.append("{" + " ".join(line) + "}")
    img = tk.PhotoImage(width=w, height=h)
    img.put(" ".join(rows))
    _CACHE[key] = img
    return img


def round_corners(frame, r=10, fill=None, bg=None, border=None):
    """Rounds a plain Tk frame's corners: four little corner images, drawn against the colour behind the frame.
    With a border, give the frame highlightthickness=1 in the border's colour."""
    fill = fill or frame.cget("background")
    bg = bg or bg_of(frame.master)
    r = px(r)
    img = rounded(2 * r + 2, 2 * r + 2, r, fill, bg, border)
    parts = []
    for (rx, ry, anchor, sx, sy) in ((0, 0, "nw", 0, 0), (1, 0, "ne", r + 2, 0), (0, 1, "sw", 0, r + 2),
                                     (1, 1, "se", r + 2, r + 2)):
        piece = tk.PhotoImage()
        piece.tk.call(piece, "copy", img, "-from", sx, sy, sx + r, sy + r)
        lab = tk.Label(frame, image=piece, bd=0, highlightthickness=0, bg=bg)
        lab.image = piece
        lab.place(relx=rx, rely=ry, anchor=anchor, bordermode="outside")
        parts.append(lab)
    return parts


def card(parent, r=12, fill=None, border=True, **kw):
    """A rounded card: a frame in the panel colour with a hairline border."""
    fill = fill or P["panel"]
    f = tk.Frame(parent, bg=fill, highlightthickness=1 if border else 0, highlightbackground=P["line"],
                 highlightcolor=P["line"], bd=0, **kw)
    f.after_idle(lambda: f.winfo_exists() and round_corners(f, r, fill, bg_of(parent), P["line"] if border else None))
    return f


# ---------------------------------------------------------------------------------------------- buttons
# kind: (fill, hover fill, text, border)
KINDS = {
    "primary": ("fg", "white", "ink", None),
    "secondary": ("panel2", "hover", "fg", "line2"),
    "ghost": (None, "panel2", "mut", None),
    "live": ("acc", "acc_hi", "white", None),
    "muted": ("panel2", "hover", "mut", "line2"),
    "on": ("acc_tint", "red_tint", "rose", "acc_line"),
}
STYLE_KIND = {"Accent.TButton": "primary", "TButton": "secondary", "OnAir.TButton": "live",
              "OffAir.TButton": "secondary", "Nascar.TButton": "on", "Small.TButton": "secondary",
              "Ghost.TButton": "ghost"}


class Btn(tk.Canvas):
    """A rounded button with a hover state. Speaks enough of ttk.Button's language (text, style, state, command,
    invoke) to stand in for one."""

    def __init__(self, parent, text="", command=None, style="TButton", kind=None, small=None, state="normal",
                 minwidth=0):
        self._bg = bg_of(parent)
        super().__init__(parent, bg=self._bg, highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self._text, self._cmd, self._state = text, command, state
        self._style = style
        self._kind = kind or STYLE_KIND.get(style, "secondary")
        self._small = (style == "Small.TButton") if small is None else small
        self._minw = minwidth
        self._hover = False
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
        self.bind("<FocusIn>", lambda e: self._draw())
        self.bind("<FocusOut>", lambda e: self._draw())
        self._draw()

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _click(self, e):
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.invoke()

    def invoke(self):
        if self._state != "disabled" and self._cmd is not None:
            return self._cmd()

    def _draw(self):
        f = F["btn_s" if self._small else "btn"]
        fo = tkfont.Font(font=f)
        padx, pady = (px(10), px(4)) if self._small else (px(14), px(7))
        w = max(self._minw, fo.measure(self._text) + 2 * padx)
        h = fo.metrics("linespace") + 2 * pady
        fill, hov, ink, border = KINDS[self._kind]
        fill = P[fill] if fill else self._bg
        if self._hover and self._state != "disabled":
            fill = P[hov]
        ink = P[ink]
        if self._kind == "ghost" and self._hover:
            ink = P["fg"]
        border = P[border] if border else None
        if self._state == "disabled":
            ink = mix(ink, fill, 0.45)
        try:
            if self.focus_get() is self and self._kind != "primary":
                border = P["mut"]
        except Exception:
            pass
        self.delete("all")
        super().configure(width=w, height=h, bg=self._bg, cursor="arrow" if self._state == "disabled" else "hand2")
        self.create_image(0, 0, anchor="nw", image=rounded(w, h, px(7), fill, self._bg, border))
        self.create_text(w / 2, h / 2, text=self._text, font=f, fill=ink)

    def configure(self, cnf=None, **kw):
        if cnf is None and not kw:
            return super().configure()
        if isinstance(cnf, str):
            return self.cget(cnf)
        kw = dict(cnf or {}, **kw)
        if "text" in kw:
            self._text = kw.pop("text")
        if "style" in kw:
            self._style = kw.pop("style")
            self._kind = STYLE_KIND.get(self._style, "secondary")
        if "kind" in kw:
            self._kind = kw.pop("kind")
        if "state" in kw:
            self._state = str(kw.pop("state"))
        if "command" in kw:
            self._cmd = kw.pop("command")
        if kw:
            super().configure(**kw)
        self._draw()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        if key == "style":
            return self._style
        if key == "command":
            return self._cmd
        return super().cget(key)

    __getitem__ = cget


class Switch(tk.Frame):
    """An on/off switch with its label, bound to a BooleanVar."""

    def __init__(self, parent, text="", variable=None, command=None, font_key="ui", fg=None):
        bg = bg_of(parent)
        super().__init__(parent, bg=bg, cursor="hand2")
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self._cmd, self._bg = command, bg
        self.w, self.h = px(32), px(18)
        self.c = tk.Canvas(self, width=self.w, height=self.h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.c.pack(side="left")
        self.label = None
        if text:
            self.label = tk.Label(self, text=text, bg=bg, fg=fg or P["fg"], font=F[font_key], cursor="hand2",
                                  justify="left", anchor="w")
            self.label.pack(side="left", padx=(px(9), 0))
        for wd in (self, self.c) + ((self.label,) if self.label else ()):
            wd.bind("<Button-1>", self._toggle)
        self._trace = self.var.trace_add("write", lambda *a: self._draw())
        self.bind("<Destroy>", self._gone, add="+")
        self._draw()

    def _gone(self, e):
        if e.widget is self:
            try:
                self.var.trace_remove("write", self._trace)
            except Exception:
                pass

    def _toggle(self, e=None):
        self.var.set(not bool(self.var.get()))
        if self._cmd:
            self._cmd()

    def _draw(self):
        try:
            on = bool(self.var.get())
        except Exception:
            on = False
        c, w, h = self.c, self.w, self.h
        c.delete("all")
        track = P["acc"] if on else P["line2"]
        c.create_image(0, 0, anchor="nw", image=rounded(w, h, h // 2, track, self._bg))
        k = h - px(4)
        x = w - px(2) - k if on else px(2)
        c.create_image(x, px(2), anchor="nw", image=rounded(k, k, k // 2, P["white"], track))


class Segmented(tk.Canvas):
    """One choice out of a few, as a segmented control, bound to a StringVar."""

    def __init__(self, parent, variable, options, command=None):
        self._bg = bg_of(parent)
        super().__init__(parent, bg=self._bg, highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.options, self._cmd = variable, list(options.items()), command
        self._hover = None
        self._spans = []
        self._trace = variable.trace_add("write", lambda *a: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<Destroy>", self._gone, add="+")
        self._draw()

    def _gone(self, e):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass

    def _at(self, x):
        return next((k for k, (a, b) in zip((k for k, _ in self.options), self._spans) if a <= x < b), None)

    def _motion(self, e):
        self._set_hover(self._at(e.x))

    def _set_hover(self, k):
        if k != self._hover:
            self._hover = k
            self._draw()

    def _click(self, e):
        k = self._at(e.x)
        if k is not None:
            self.var.set(k)
            if self._cmd:
                self._cmd()

    def _draw(self):
        fo_n, fo_s = tkfont.Font(font=F["ui"]), tkfont.Font(font=F["uib"])
        pad, padx, inner = px(3), px(12), px(3)
        h = fo_s.metrics("linespace") + px(12)
        widths = [max(fo_n.measure(lab), fo_s.measure(lab)) + 2 * padx for _, lab in self.options]
        W = sum(widths) + 2 * pad
        self.delete("all")
        self.configure(width=W, height=h)
        self.create_image(0, 0, anchor="nw", image=rounded(W, h, px(8), P["panel2"], self._bg, P["line2"]))
        try:
            cur = self.var.get()
        except Exception:
            cur = None
        x = pad
        self._spans = []
        for (k, lab), w in zip(self.options, widths):
            sel = k == cur
            if sel:
                self.create_image(x, inner, anchor="nw",
                                  image=rounded(w, h - 2 * inner, px(6), P["elev"], P["panel2"], P["line2"]))
            elif k == self._hover:
                self.create_image(x, inner, anchor="nw",
                                  image=rounded(w, h - 2 * inner, px(6), P["hover"], P["panel2"]))
            self.create_text(x + w / 2, h / 2, text=lab, font=F["uib" if sel else "ui"],
                             fill=P["fg"] if sel else P["mut"])
            self._spans.append((x, x + w))
            x += w


def pill_image(text, fg, fill, bg, font_key="micro", padx=8, pady=3):
    fo = tkfont.Font(font=F[font_key])
    w, h = fo.measure(text) + 2 * px(padx), fo.metrics("linespace") + 2 * px(pady)
    return rounded(w, h, h // 2, fill, bg)


class Pill(tk.Label):
    """A small rounded tag: READY, GREEN FLAG... cget('text') is its text."""

    def __init__(self, parent, text="", fg=None, fill=None, font_key="micro", padx=8, pady=3):
        self._bg = bg_of(parent)
        self._fk, self._padx, self._pady = font_key, padx, pady
        super().__init__(parent, bg=self._bg, bd=0, highlightthickness=0, compound="center", font=F[font_key])
        self.set(text, fg or P["fg"], fill or P["panel2"])

    def set(self, text, fg=None, fill=None):
        fg, fill = fg or P["fg"], fill or P["panel2"]
        if not text:
            self.configure(text="", image="", width=0, height=0)
            return
        img = pill_image(text, fg, fill, self._bg, self._fk, self._padx, self._pady)
        self._img = img
        self.configure(text=text, image=img, fg=fg)


def style_ttk(root):
    """ttk's clam theme in SimCast's colours: entries, combo boxes, sliders, scroll bars."""
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=P["panel"], foreground=P["fg"], fieldbackground=P["panel2"], font=F["ui"],
                 bordercolor=P["line2"], lightcolor=P["panel2"], darkcolor=P["panel2"], troughcolor=P["panel2"],
                 focuscolor=P["panel"], selectbackground=P["acc"], selectforeground=P["white"],
                 insertcolor=P["fg"])
    st.configure("TFrame", background=P["bg"])
    st.configure("Card.TFrame", background=P["panel"])
    st.configure("TLabel", background=P["bg"], foreground=P["fg"])
    st.configure("Card.TLabel", background=P["panel"], foreground=P["fg"])
    st.configure("Mut.TLabel", background=P["bg"], foreground=P["mut"])
    st.configure("CardMut.TLabel", background=P["panel"], foreground=P["mut"], font=F["small"])
    st.configure("TEntry", fieldbackground=P["panel2"], foreground=P["fg"], padding=(px(8), px(6)),
                 bordercolor=P["line2"], lightcolor=P["panel2"], darkcolor=P["panel2"])
    st.map("TEntry", bordercolor=[("focus", P["mut"])], lightcolor=[("focus", P["panel2"])])
    st.configure("TCombobox", fieldbackground=P["panel2"], background=P["panel2"], foreground=P["fg"],
                 arrowcolor=P["mut"], padding=(px(8), px(5)), bordercolor=P["line2"], lightcolor=P["panel2"],
                 darkcolor=P["panel2"], arrowsize=px(15))
    st.map("TCombobox", fieldbackground=[("readonly", P["panel2"])], foreground=[("readonly", P["fg"])],
           selectbackground=[("readonly", P["panel2"])], selectforeground=[("readonly", P["fg"])],
           background=[("active", P["hover"]), ("readonly", P["panel2"])], bordercolor=[("focus", P["mut"])],
           arrowcolor=[("active", P["fg"])])
    root.option_add("*TCombobox*Listbox.background", P["panel2"])
    root.option_add("*TCombobox*Listbox.foreground", P["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", P["elev"])
    root.option_add("*TCombobox*Listbox.selectForeground", P["fg"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.font", F["ui"])
    for w in ("TRadiobutton", "TCheckbutton"):
        st.configure(w, background=P["panel"], foreground=P["fg"], indicatorbackground=P["panel2"],
                     indicatorforeground=P["white"], upperbordercolor=P["line2"], lowerbordercolor=P["line2"],
                     indicatormargin=(0, 0, px(8), 0))
        st.map(w, indicatorbackground=[("selected", P["acc"]), ("active", P["hover"])],
               background=[("active", P["panel"])])
    st.configure("Horizontal.TScale", background=P["fg"], troughcolor=P["line2"], bordercolor=P["panel"],
                 lightcolor=P["fg"], darkcolor=P["fg"], sliderlength=px(14), sliderthickness=px(14),
                 troughrelief="flat", gripcount=0)
    st.map("Horizontal.TScale", background=[("active", P["white"])])
    for o in ("Vertical", "Horizontal"):
        st.layout(f"{o}.TScrollbar", [(f"{o}.Scrollbar.trough", {"sticky": "nswe", "children": [
            (f"{o}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.configure(f"{o}.TScrollbar", background=P["line2"], troughcolor=P["panel"], bordercolor=P["panel"],
                     lightcolor=P["line2"], darkcolor=P["line2"], gripcount=0, arrowsize=px(8), width=px(8))
        st.map(f"{o}.TScrollbar", background=[("active", P["dim"])], lightcolor=[("active", P["dim"])],
               darkcolor=[("active", P["dim"])])
    st.layout("Side.TNotebook.Tab", [])
    st.layout("Side.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    st.configure("Side.TNotebook", background=P["panel"], borderwidth=0, bordercolor=P["panel"],
                 lightcolor=P["panel"], darkcolor=P["panel"], tabmargins=0)
    return st


class NavList(tk.Frame):
    """A sidebar of sections: the selected one is raised, with a red marker."""

    def __init__(self, parent, items, on_select, bg=None, width=200):
        bg = bg or bg_of(parent)
        super().__init__(parent, bg=bg)
        self._bg, self.on_select, self.rows, self.cur = bg, on_select, [], None
        for i, text in enumerate(items):
            row = tk.Frame(self, bg=bg, cursor="hand2")
            row.pack(fill="x", pady=px(1))
            bar = tk.Frame(row, bg=bg, width=px(3))
            bar.pack(side="left", fill="y", pady=px(6))
            lab = tk.Label(row, text=text, anchor="w", bg=bg, fg=P["mut"], font=F["nav"], padx=px(12), pady=px(7),
                           cursor="hand2")
            lab.pack(side="left", fill="x", expand=True)
            for wd in (row, bar, lab):
                wd.bind("<Button-1>", lambda e, i=i: self.on_select(i))
                wd.bind("<Enter>", lambda e, i=i: self._hover(i, True))
                wd.bind("<Leave>", lambda e, i=i: self._hover(i, False))
            self.rows.append((row, bar, lab))
        if width:
            self.configure(width=px(width))

    def _hover(self, i, on):
        if i == self.cur:
            return
        row, bar, lab = self.rows[i]
        c = P["panel2"] if on else self._bg
        for wd in (row, lab):
            wd.configure(bg=c)
        bar.configure(bg=c)
        lab.configure(fg=P["fg"] if on else P["mut"])

    def select(self, i):
        self.cur = i
        for k, (row, bar, lab) in enumerate(self.rows):
            on = k == i
            c = P["elev"] if on else self._bg
            row.configure(bg=c)
            lab.configure(bg=c, fg=P["fg"] if on else P["mut"], font=F["navb" if on else "nav"])
            bar.configure(bg=P["acc"] if on else c)


def autohide(sb):
    """A scroll bar's set command that hides the bar while everything fits."""
    def set_(first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            if sb.winfo_manager():
                sb.grid_remove()
        elif not sb.winfo_manager():
            sb.grid()
        sb.set(first, last)
    return set_


class Scrolled(tk.Frame):
    """A frame whose content (.inner) scrolls vertically with the mouse wheel, its scroll bar shown only when
    needed."""

    def __init__(self, parent, bg, inner_factory):
        super().__init__(parent, bg=bg)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = c = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        c.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self, orient="vertical", command=c.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, px(4)), pady=px(6))
        c.configure(yscrollcommand=autohide(sb))
        self.inner = inner_factory(c)
        self._win = c.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", lambda e: c.configure(scrollregion=c.bbox("all")))
        c.bind("<Configure>", lambda e: c.itemconfigure(self._win, width=e.width))
        top = self.winfo_toplevel()                  # every widget in the window passes its wheel events up here
        top.bind("<MouseWheel>", lambda e: self._scroll(-1 if e.delta > 0 else 1), add="+")
        top.bind("<Button-4>", lambda e: self._scroll(-1), add="+")
        top.bind("<Button-5>", lambda e: self._scroll(1), add="+")

    def _scroll(self, d):
        c = self.canvas
        if c.winfo_exists() and c.winfo_ismapped() and c.yview() != (0.0, 1.0):
            c.yview_scroll(d * 3, "units")
