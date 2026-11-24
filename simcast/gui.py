"""SimCast's Windows desktop window: the timing tower, what the booth is saying, the controls and the settings,
plus an optional captions window that floats on top (for a second screen, or to capture in OBS)."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import ttk

from . import hotkeys, providers, ui, update
from .app import APP_DIR, APP_NAME, APP_VERSION, DEFAULTS, STYLES, file_log, style_of
from .link import DUCK_LABELS
from .audio import VOICE_LABEL, VOICES
from .guide import TOPICS as GUIDE
from .ui import P, Btn, Pill, Segmented, Switch, px

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
ICO = os.path.join(os.path.dirname(HERE), "SimCast.ico")
LICENSE_DIR = os.path.join(HERE, "licenses")

WHO_COLOUR = {"lead": P["amber"], "analyst": P["blue"], "pit": P["rose"]}     # each voice's colour
WHO_TINT = {"lead": P["amber_tint"], "analyst": P["blue_tint"], "pit": P["red_tint"]}
CHATTER_LABELS = {"quiet": "Quiet", "normal": "Normal", "chatty": "Chatty"}
FOCUS_LABELS = {"field": "Whole field", "balanced": "Balanced", "me": "Mostly me"}
STYLE_LABELS = {"tv": "TV broadcast", "late": "Late-night stream", "pub": "Mates down the pub", "custom": "Custom"}
STYLE_NOTES = {
    "tv": "A proper television booth: warm, clean, the whole race with your story in it.",
    "late": "Looser and chattier: more banter and big reactions, the odd swear word, side conversations whenever "
            "there's time.",
    "pub": "Completely unhinged and all about you: rants, feuds, conspiracy theories and plenty of swearing, all "
           "set off by the racing.",
    "custom": "Your own mix: set it under Fine-tune.",
}
TONE_LABELS = {"broadcast": "Professional", "loose": "Loose", "unhinged": "Unhinged"}
BANTER_LABELS = {"rare": "Rarely", "normal": "Sometimes", "lots": "Whenever there's time"}
DRIVE_LABELS = {"stream": "Stream", "driver": "Driving"}
CONVO_LABELS = {"auto": "Automatic (a second provider with a key)", "same": "The same as above",
                "openai": "OpenAI", "gemini": "Google Gemini", "deepseek": "DeepSeek"}
NASCAR_LABELS = {"auto": "Automatic", "on": "On", "off": "Off"}
PRONOUN_LABELS = {"auto": "From my name", "he": "He", "she": "She", "they": "They"}
PROVIDERS = {"openai": "OpenAI", "gemini": "Google Gemini", "deepseek": "DeepSeek"}
ASSIST_LABELS = {"auto": "Automatic (DeepSeek, then OpenAI)", "off": "Off", "deepseek": "DeepSeek",
                 "openai": "OpenAI", "gemini": "Google Gemini"}


def _dpi_aware():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SimCast.App")
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def dark_titlebar(win, caption=None):
    """Windows 10/11: a dark title bar, and on Windows 11 a caption and border in SimCast's colours."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                break

        def rgb(h):
            return ctypes.c_int(int(h[5:7], 16) << 16 | int(h[3:5], 16) << 8 | int(h[1:3], 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(rgb(caption or P["bg"])), 4)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(rgb(P["line"])), 4)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(rgb(P["fg"])), 4)
        corner = ctypes.c_int(2)                                  # Windows 11: rounded window corners
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
    except Exception:
        pass


def _gap_text(s, i, session):
    if i == 0:
        return "Leader" if session == "race" else (_lap(s["best"]) if s["best"] else "")
    if s["status"] in ("dnf", "dsq"):
        return "Out"
    if session != "race":
        return f"+{s['gap']:.3f}" if s["gap"] is not None else ("" if s["best"] else "No time")
    if s["interval"] is not None:
        return f"+{s['interval']:.1f}"
    if s["laps_down"]:
        return f"+{s['laps_down']} L"
    return ""


def _lap(t):
    m, sec = divmod(t, 60)
    return f"{int(m)}:{sec:06.3f}" if m else f"{sec:.3f}"


def section_title(parent, text, sub=None):
    """A card's heading: the title, and a quieter note on the right."""
    bg = ui.bg_of(parent)
    row = tk.Frame(parent, bg=bg)
    tk.Label(row, text=text, font=ui.F["h3"], fg=P["fg"], bg=bg).pack(side="left")
    lab = tk.Label(row, text=sub or "", font=ui.F["small"], fg=P["mut"], bg=bg)
    lab.pack(side="right")
    return row, lab


class SimCastWindow:
    POLL_MS = 400
    UPDATE_FIRST_MS = 4000                         # first update check, once the window is up
    UPDATE_EVERY_MS = 12 * 3600 * 1000             # then every 12 hours while SimCast is open

    def __init__(self, cast):
        _dpi_aware()
        self.cast = cast
        cast.director.on_air = False               # SimCast always opens off air: the booth talks when you ask
        self.root = r = tk.Tk(className="SimCast")
        r.title(f"{APP_NAME} {APP_VERSION}")
        r.configure(bg=P["bg"])
        self._fonts()
        r.minsize(px(880), px(560))
        geo = cast.cfg.get("window")
        r.geometry(geo if isinstance(geo, str) and "x" in geo else f"{px(1200)}x{px(780)}")
        self._style()
        self._icons()
        self._build()
        dark_titlebar(r)
        self.last_line_t = 0.0
        self.captions = None
        self.settings_win = None
        self.update_info = None                    # the newer release on offer, if any
        self.update_bar = None
        self.update_status = tk.StringVar(value="")       # shown in Settings > About
        self._update_events = deque()              # from the checking / downloading threads to the window
        self._updating = False
        self.stop = threading.Event()
        self.loop = threading.Thread(target=cast.run, args=(self.stop,), daemon=True, name="simcast-loop")
        r.protocol("WM_DELETE_WINDOW", self.close)
        if cast.cfg.get("captions"):
            self.toggle_captions(True)
        if not cast.cfg.get("guide_seen"):                 # first start: open the guide on Getting started
            r.after(600, self.open_guide)
            cast.update_settings({"guide_seen": True})
        r.after(self.UPDATE_FIRST_MS, self._update_tick)

    # ------------------------------------------------------------------ look
    def _fonts(self):
        self.f = ui.setup(self.root)

    def _style(self):
        ui.style_ttk(self.root)

    def _img(self, name):
        try:
            return tk.PhotoImage(file=os.path.join(ASSETS, name))
        except Exception:
            return None

    def _icons(self):
        self.img_logo = self._img("logo-40.png")
        self.img_hero = self._img("hero-car-240.png")
        if self.img_hero is not None and ui.SCALE < 1.5:
            self.img_hero_s = self.img_hero.subsample(2)     # a quieter 120 px mark for the stand-by screen
        else:
            self.img_hero_s = self.img_hero
        self.img_helmet = self._img("helmet-32.png")
        icon = self._img("icon-48.png")
        if icon is not None:
            self.root.iconphoto(True, icon)
            self._icon = icon
        if sys.platform == "win32" and os.path.exists(ICO):
            try:
                self.root.iconbitmap(default=ICO)
            except Exception:
                pass

    # ------------------------------------------------------------------ layout
    def _build(self):
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(2, weight=1)
        G = px(20)                                  # the page's outer margin

        # the app bar: the mark and name, the status, the controls
        self.hdr = hdr = tk.Frame(r, bg=P["bg"], padx=G, pady=px(14))
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(2, weight=1)
        brand = tk.Frame(hdr, bg=P["bg"])
        brand.grid(row=0, column=0, sticky="w")
        if self.img_logo:
            tk.Label(brand, image=self.img_logo, bg=P["bg"]).pack(side="left", padx=(0, px(10)))
        tk.Label(brand, text="SimCast", font=self.f["title"], fg=P["fg"], bg=P["bg"]).pack(side="left")
        self.tally = tk.Canvas(hdr, height=px(24), bg=P["bg"], highlightthickness=0, bd=0)
        self.tally.grid(row=0, column=1, sticky="w", padx=(px(14), 0))
        self.tally_box = self.tally.create_image(0, 0, anchor="nw")
        self.tally_dot = self.tally.create_oval(px(10), px(9), px(16), px(15), outline="")
        self.tally_txt = self.tally.create_text(px(22), px(12), anchor="w", text="STANDBY", font=self.f["micro"],
                                                fill=P["mut"])
        self._tally_state = None
        btns = tk.Frame(hdr, bg=P["bg"])
        btns.grid(row=0, column=3, sticky="e")
        self.nascar_btn = Btn(btns, text="NASCAR off", kind="ghost", command=self.toggle_nascar)
        self.nascar_btn.pack(side="left", padx=px(2))
        Btn(btns, text="Skip line", kind="ghost", command=self.skip).pack(side="left", padx=px(2))
        self.guide_btn = Btn(btns, text="Guide", kind="ghost", command=self.open_guide)
        self.guide_btn.pack(side="left", padx=px(2))
        self.settings_btn = Btn(btns, text="Settings", command=self.open_settings)
        self.settings_btn.pack(side="left", padx=(px(2), px(10)))
        self.air_btn = Btn(btns, text="Go on air", style="OffAir.TButton", command=self.toggle_air, minwidth=px(112))
        self.air_btn.pack(side="left")

        # the session bar: the track, the lap, the flag, what SimCast is connected to
        strip = ui.card(r, r=10)
        strip.grid(row=1, column=0, sticky="ew", padx=G)
        strip.columnconfigure(2, weight=1)
        self.session = tk.Label(strip, text="", font=self.f["track"], fg=P["fg"], bg=P["panel"], anchor="w")
        self.session.grid(row=0, column=0, sticky="w", padx=(px(18), 0), pady=px(12))
        self.lap = tk.Label(strip, text="", font=self.f["ui"], fg=P["mut"], bg=P["panel"], anchor="w")
        self.lap.grid(row=0, column=1, sticky="w", padx=(px(12), 0))
        self.flag = Pill(strip, "")
        self.flag.grid(row=0, column=3, padx=(0, px(12)))
        self.status = tk.Label(strip, text="Starting…", font=self.f["small"], fg=P["mut"], bg=P["panel"])
        self.status.grid(row=0, column=4, sticky="e", padx=(0, px(18)))

        self.body = tk.Frame(r, bg=P["bg"], padx=G, pady=px(14))
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=0, minsize=px(340))
        self.body.columnconfigure(1, weight=1)
        self.body.rowconfigure(0, weight=1)

        # the timing tower
        tw_ = ui.card(self.body)
        tw_.grid(row=0, column=0, sticky="nsew", padx=(0, px(14)))
        tw_.rowconfigure(1, weight=1)
        tw_.columnconfigure(0, weight=1)
        th, self.tower_info = section_title(tw_, "Timing")
        th.grid(row=0, column=0, sticky="ew", padx=px(18), pady=(px(16), px(10)))
        self.tower = tk.Canvas(tw_, bg=P["panel"], highlightthickness=0, bd=0, width=px(320))
        self.tower.grid(row=1, column=0, sticky="nsew", padx=px(10), pady=(0, px(12)))
        self.tower.bind("<Configure>", lambda e: self._draw_tower(force=True))
        self.tower.bind("<MouseWheel>", lambda e: self.tower.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.tower.bind("<Button-4>", lambda e: self.tower.yview_scroll(-1, "units"))
        self.tower.bind("<Button-5>", lambda e: self.tower.yview_scroll(1, "units"))
        self.tower_rows = []                       # what the tower shows: (pos, name, gap, tags) per car
        self._tower_drawn = None
        self._tower_session = None

        # the booth: who said what
        bo = ui.card(self.body)
        bo.grid(row=0, column=1, sticky="nsew")
        bo.rowconfigure(1, weight=1)
        bo.columnconfigure(0, weight=1)
        bh, self.speaking = section_title(bo, "Commentary")
        self.speaking.configure(fg=P["rose"], font=self.f["smallb"])
        bh.grid(row=0, column=0, columnspan=2, sticky="ew", padx=px(18), pady=(px(16), px(10)))
        tk.Frame(bo, bg=P["line"], height=1).grid(row=0, column=0, columnspan=2, sticky="sew")
        tab = px(84)
        self.text = tk.Text(bo, bg=P["panel"], fg=P["fg2"], relief="flat", wrap="word", font=self.f["line"],
                            padx=px(18), pady=px(12), highlightthickness=0, borderwidth=0, cursor="arrow",
                            spacing1=0, spacing2=px(3), spacing3=0, tabs=(tab,), state="disabled")
        self.text.grid(row=1, column=0, sticky="nsew", pady=(0, px(8)))
        sb = ttk.Scrollbar(bo, orient="vertical", command=self.text.yview)
        sb.grid(row=1, column=1, sticky="ns", padx=(0, px(6)), pady=(px(6), px(14)))
        self.text.configure(yscrollcommand=ui.autohide(sb))
        for who, col in WHO_COLOUR.items():
            self.text.tag_configure("who_" + who, foreground=col, font=self.f["who"])
        self.text.tag_configure("e0", font=self.f["line"], foreground=P["mut"])
        self.text.tag_configure("e1", font=self.f["line"], foreground=P["fg2"])
        self.text.tag_configure("e2", font=self.f["line2"], foreground=P["fg"])
        self.text.tag_configure("e3", font=self.f["line3"], foreground=P["white"])
        self.text.tag_configure("para", lmargin1=0, lmargin2=tab)
        self.text.tag_configure("hint", foreground=P["dim"])
        self.text.tag_configure("gap", font=(self.f["ui"][0], 5))
        self._text_insert([("The commentary appears here as it's spoken.", "hint")])

        # the stand-by screen (instead of the tower and booth when no sim is running)
        self.hero = ui.card(r, r=14)
        self.hero.columnconfigure(0, weight=1)
        self.hero.rowconfigure(0, weight=1)
        inner = tk.Frame(self.hero, bg=P["panel"], padx=px(44), pady=px(36))
        inner.grid(row=0, column=0, sticky="nsew")
        inner.columnconfigure(1, weight=1)
        if self.img_hero_s:
            tk.Label(inner, image=self.img_hero_s, bg=P["panel"]).grid(row=0, column=0, rowspan=4,
                                                                       padx=(0, px(32)), sticky="nw")
        Pill(inner, "STANDBY", fg=P["mut"], fill=P["panel2"]).grid(row=0, column=1, sticky="w")
        tk.Label(inner, text="Waiting for a race", font=self.f["h1"], fg=P["fg"], bg=P["panel"]).grid(
            row=1, column=1, sticky="w", pady=(px(10), 0))
        tk.Label(inner, fg=P["mut"], bg=P["panel"], wraplength=px(560), justify="left", font=self.f["ui"],
                 text="Start a race or a rally stage in any supported sim and SimCast picks it up by itself. "
                      "The booth stays off air until you press Go on air."
                 ).grid(row=2, column=1, sticky="w", pady=(px(6), px(18)))
        acts = tk.Frame(inner, bg=P["panel"])
        acts.grid(row=3, column=1, sticky="nw")
        Btn(acts, text="Getting started", style="Accent.TButton", command=self.open_guide).pack(side="left")
        Btn(acts, text="Settings", command=self.open_settings).pack(side="left", padx=(px(8), 0))
        tk.Frame(inner, bg=P["line"], height=1).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(px(32), px(22)))
        sh, _ = section_title(inner, "Sims on this PC", "Ready means nothing to do")
        sh.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, px(10)))
        self.sims_box = tk.Frame(inner, bg=P["panel"])
        self.sims_box.grid(row=6, column=0, columnspan=2, sticky="ew")
        self._sims_shown = None

        foot = tk.Frame(r, bg=P["bg"], padx=G, pady=px(10))
        foot.grid(row=3, column=0, sticky="ew")
        foot.columnconfigure(0, weight=1)
        self.foot = tk.Label(foot, text="", font=self.f["small"], fg=P["dim"], bg=P["bg"], anchor="w")
        self.foot.grid(row=0, column=0, sticky="w")
        self.cap_var = tk.BooleanVar(value=bool(self.cast.cfg.get("captions")))
        Switch(foot, "Captions window", self.cap_var, command=lambda: self.toggle_captions(self.cap_var.get()),
               font_key="small", fg=P["mut"]).grid(row=0, column=1, sticky="e")
        self._idle_shown = None

    def _show_idle(self, idle):
        if idle == self._idle_shown:
            return
        self._idle_shown = idle
        if idle:
            self.body.grid_remove()
            self.hero.grid(row=2, column=0, sticky="nsew", padx=px(20), pady=(px(14), px(4)))
        else:
            self.hero.grid_remove()
            self.body.grid()

    TAGS = {"pit": ("PIT", "amber", "amber_tint"), "stop": ("STOP", "red", "red_tint"), "out": ("OUT", "mut", "panel2"),
            "fin": ("FIN", "fg", "elev"), "fl": ("FL", "purple", "purple_tint")}

    def _draw_tower(self, rows=None, session=None, force=False):
        """The running order: the position, the name, the gap, and tags (PIT, FL, FIN, OUT). Your car is the
        tinted row with a red edge; a car in a battle has an amber dot."""
        if rows is None:
            rows, session = self.tower_rows, self._tower_session
        key = (tuple(rows), session, self.tower.winfo_width())
        if not force and key == self._tower_drawn:
            return
        self._tower_drawn = key
        c = self.tower
        c.delete("all")
        W, h = max(px(260), c.winfo_width()), px(32)
        for i, (pos, name, gap, tags) in enumerate(rows):
            y = i * h
            me, out = "me" in tags, "out" in tags
            row_bg = P["panel"]
            if me:
                row_bg = P["acc_tint"]
                c.create_image(0, y + px(1), anchor="nw", image=ui.rounded(W, h - px(2), px(7), row_bg, P["panel"],
                                                                           P["acc_line"]))
                c.create_rectangle(px(1), y + px(8), px(3), y + h - px(8), fill=P["acc"], outline="")
            elif i:
                c.create_line(px(8), y, W - px(8), y, fill=P["line"])
            mid = y + h / 2
            c.create_text(px(30), mid, anchor="e", text=str(pos), font=self.f["num"],
                          fill=P["fg"] if (me or pos <= 3) else P["mut"])
            if "battle" in tags and not me:
                c.create_oval(px(38), mid - px(3), px(44), mid + px(3), fill=P["amber"], outline="")
            ink = P["dim"] if out else P["fg"]
            c.create_text(px(50), mid, anchor="w", text=name, fill=ink,
                          font=self.f["towerb"] if (pos == 1 or me) else self.f["tower"])
            gx = W - px(12)
            gid = c.create_text(gx, mid, anchor="e", text=gap, fill=P["fg"] if (i == 0 or me) else P["mut"],
                                font=self.f["num"] if i == 0 or me else self.f["tower"])
            bx = c.bbox(gid)
            x = (bx[0] if bx and gap else gx) - px(8)
            for t in ("pit", "stop", "out", "fin", "fl"):
                if t in tags:
                    label, fg, fill = self.TAGS[t]
                    img = ui.pill_image(label, P[fg], P[fill], row_bg, "tag", 6, 2)
                    c.create_image(x, mid, anchor="e", image=img)
                    c.create_text(x - img.width() / 2, mid, text=label, font=self.f["tag"], fill=P[fg])
                    x -= img.width() + px(4)
        c.configure(scrollregion=(0, 0, W, max(len(rows) * h, 1)))

    # ------------------------------------------------------------------ live updates
    def _text_insert(self, parts):
        t = self.text
        at_end = t.yview()[1] > 0.98
        t.configure(state="normal")
        for txt, tag in parts:
            t.insert("end", txt, tag)
        lines = int(t.index("end-1c").split(".")[0])
        if lines > 600:
            t.delete("1.0", f"{lines - 500}.0")
        t.configure(state="disabled")
        if at_end:
            t.see("end")

    def poll(self):
        try:
            self._update_drain()
            self._render(self.cast.state())
        except Exception as e:
            self.foot.configure(text=f"Display error: {e}")
        self.root.after(self.POLL_MS, self.poll)

    COMPACT_BELOW = 1060                           # window width (design px) under which the header goes short

    FLAG_CHIP = {"fcy": ("FULL COURSE YELLOW", "CAUTION"), "sc": ("SAFETY CAR", "CAUTION"), "vsc": ("VIRTUAL SAFETY CAR",
                                                                                               "CAUTION")}

    def _set_tally(self, live, on_air):
        pulse = live and int(time.time() * 2) % 3 != 0
        state = (live, on_air, pulse)
        if state == self._tally_state:
            return
        self._tally_state = state
        c = self.tally
        if live:
            fill, dot, txt, ink = P["red_tint"], (P["acc"] if pulse else P["acc_line"]), "LIVE", P["rose"]
        else:
            fill, dot, txt, ink = P["panel2"], P["dim"], "STANDBY", P["mut"]
        tw = ui.font("micro").measure(txt) + px(34)
        c.configure(width=tw)
        img = ui.rounded(tw, px(24), px(12), fill, P["bg"], P["acc_line"] if live else P["line2"])
        self._tally_img = img
        c.itemconfigure(self.tally_box, image=img)
        c.itemconfigure(self.tally_dot, fill=dot)
        c.itemconfigure(self.tally_txt, text=txt, fill=ink)

    def _render(self, S):
        live = bool(S["source"])
        self._set_tally(live, S["on_air"])
        self.status.configure(text=S["status"] or "")
        self.session.configure(text=S["track"] or ("" if live else "No session"))
        lap = ""
        if S["track"]:
            if S["session"] == "race" and S["lap"]:
                lap = f"Lap {S['lap']}" + (f" of {S['laps']}" if S["laps"] else "")
            elif S["session"]:
                lap = S["session"].capitalize()
        self.lap.configure(text=lap)
        nascar = bool(S.get("nascar"))
        if S["finished"]:
            self.flag.set("CHECKERED FLAG" if nascar else "CHEQUERED FLAG", P["fg"], P["elev"])
        elif S["flag"] in self.FLAG_CHIP:
            self.flag.set(self.FLAG_CHIP[S["flag"]][1 if nascar else 0], P["amber"], P["amber_tint"])
        elif S["started"] and S["session"] == "race":
            self.flag.set("GREEN FLAG", P["green"], P["green_tint"])
        else:
            self.flag.set("")
        compact = self.root.winfo_width() < px(self.COMPACT_BELOW)  # a narrow window: shorter labels, so all fit
        self.nascar_btn.configure(text="NASCAR" if compact else ("NASCAR on" if nascar else "NASCAR off"),
                                  kind="on" if nascar else "ghost")
        self.air_btn.configure(text="●  On air" if S["on_air"] else "Go on air",
                               style="OnAir.TButton" if S["on_air"] else "OffAir.TButton")
        self.guide_btn.configure(text="?" if compact else "Guide")
        self.settings_btn.configure(text="⚙" if compact else "Settings")
        self._show_idle(not live)
        self._render_sims(S.get("sims") or [])

        rows = []
        for i, s in enumerate(S["standings"]):
            tags = (("me",) if s["player"] else ()) + (("battle",) if s["battle"] and not s["player"] else ()) + \
                (("out",) if s["status"] in ("dnf", "dsq") else ()) + (("pit",) if s["in_pit"] else ()) + \
                (("stop",) if s["status"] == "stopped" else ()) + (("fin",) if s["status"] == "finished" else ()) + \
                (("fl",) if s["fastest"] else ())
            rows.append((s["pos"], s["call"], _gap_text(s, i, S["session"]), tags))
        self.tower_rows, self._tower_session = rows, S["session"]
        self._draw_tower(rows, S["session"])
        self.tower_info.configure(text=f"{len(rows)} cars" if rows else "")

        new = [ln for ln in S["lines"] if ln["t"] > self.last_line_t]
        if new:
            if self.last_line_t == 0.0:
                self.text.configure(state="normal")
                self.text.delete("1.0", "end")
                self.text.configure(state="disabled")
            parts = []
            for ln in new:
                parts += [(ln["name"], ("who_" + ln["who"], "para")), ("\t", "para"),
                          (ln["text"] + "\n", ("e%d" % ln["energy"], "para")), ("\n", "gap")]
                self.last_line_t = ln["t"]
            self._text_insert(parts)
            if self.captions is not None:
                self.captions.show(new[-1])
        self.speaking.configure(text="●  Speaking" if S["speaking"] else ("" if S["on_air"] else "Off air"),
                                fg=P["rose"] if S["speaking"] else P["dim"])
        if self.captions is not None and not S["speaking"]:
            self.captions.idle()

        st = S["settings"]
        bits = []
        if S["ai"]:
            bits.append(f"AI booth: {S['ai']}")
        elif st.get("ai_colour"):
            bits.append("AI booth: no key, using the built-in lines")
        if S["voice_note"]:
            bits.append(S["voice_note"])
        if S["errors"]:
            bits.append(S["errors"][-1])
        self.foot.configure(text="   ·   ".join(bits))

    def _render_sims(self, sims):
        key = tuple((s["name"], s["status"], s["ok"]) for s in sims)
        if key == self._sims_shown:
            return
        self._sims_shown = key
        sims_list(self.sims_box, sims, self.f)

    # ------------------------------------------------------------------ actions
    def skip(self):
        self.cast.audio.interrupt(None)

    def toggle_air(self):
        self.cast.toggle_air()

    def toggle_nascar(self):
        self.cast.toggle_nascar()

    def _hotkey(self, action):
        """From the hotkey thread: act on the engine at once (it's thread safe)."""
        try:
            self.cast.hotkey(action)
        except Exception as e:
            self.cast.log(f"Hotkey {action}: {e}")

    def toggle_captions(self, on):
        if on and self.captions is None:
            self.captions = Captions(self)
            if self.cast.lines:
                self.captions.show(self.cast.lines[-1])          # the latest line straight away
        elif not on and self.captions is not None:
            self.captions.close()
            self.captions = None
        self.cap_var.set(bool(on))
        if bool(self.cast.cfg.get("captions")) != bool(on):
            self.cast.update_settings({"captions": bool(on)})

    # ------------------------------------------------------------------ updates (simcast/update.py)
    def _update_tick(self):
        """Check now, then every 12 hours while SimCast is open (Settings > About switches it off)."""
        if self.cast.cfg.get("check_updates", True):
            self.check_for_updates()
        self.root.after(self.UPDATE_EVERY_MS, self._update_tick)

    def check_for_updates(self, manual=False):
        """Asks GitHub on a background thread; the answer comes back to the window through poll()."""
        if manual:
            self.update_status.set("Checking…")

        def work():
            try:
                info = update.check_latest_release(APP_VERSION)
            except Exception as e:
                file_log(f"Update check failed: {type(e).__name__}: {e}")
                if manual:
                    self._update_events.append(("status", "Couldn't reach GitHub to check for updates."))
                return
            if info is None:
                if manual:
                    self._update_events.append(("status", f"You're up to date (SimCast {APP_VERSION})."))
                return
            if not manual and info["tag"] == self.cast.cfg.get("update_skip"):
                return
            self._update_events.append(("found", info))
        threading.Thread(target=work, daemon=True, name="update-check").start()

    def _update_drain(self):
        while self._update_events:
            kind, val = self._update_events.popleft()
            if kind == "status":
                self.update_status.set(val)
            elif kind == "found":
                self.update_status.set(f"SimCast {val['version']} is available.")
                if not (self.update_info and self.update_info["tag"] == val["tag"] and self.update_bar is not None):
                    self._show_update(val)
            elif kind == "progress":
                self._update_progress(val)

    def _show_update(self, info):
        self.update_info = info
        if self.update_bar is not None:
            self.update_bar.destroy()
        bg = P["panel"]
        bar = self.update_bar = tk.Frame(self.hdr, bg=bg, highlightbackground=P["line2"], highlightthickness=1,
                                         padx=px(12), pady=px(8))
        ui.round_corners(bar, 10, bg, P["bg"], P["line2"])
        Pill(bar, f"SimCast {info['version']} is available", fg=P["white"], fill=P["acc"], font_key="smallb",
             padx=10, pady=4).pack(side="left", padx=(0, px(12)))
        self.update_msg = tk.Label(bar, text=f"You have {APP_VERSION}.", bg=bg, fg=P["mut"], font=self.f["small"])
        self.update_msg.pack(side="left")
        Btn(bar, text="Not now", kind="ghost", small=True, command=self._skip_update).pack(side="right",
                                                                                            padx=(px(4), 0))
        Btn(bar, text="What's new", small=True, command=lambda: _open_url(info["url"])).pack(side="right", padx=px(4))
        can = update.can_install(info)
        self.update_btn = Btn(bar, text="Install update" if can else "Download", style="Accent.TButton", small=True,
                              command=self._install_update if can else (lambda: _open_url(info["url"])))
        self.update_btn.pack(side="right", padx=px(4))
        bar.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(px(12), 0))
        self.cast.log(f"Update available: SimCast {info['version']} ({info['url']})")

    def _skip_update(self):
        """Not now: don't offer this release again by itself (Settings > About > Check now still finds it)."""
        if self.update_info:
            self.cast.update_settings({"update_skip": self.update_info["tag"]})
        bar, self.update_bar = self.update_bar, None
        if bar is not None:
            bar.destroy()

    def _install_update(self):
        info = self.update_info
        if not info or self._updating:
            return
        self._updating = True
        self.update_btn.configure(state="disabled")
        safe = "".join(c if c.isalnum() or c in ".-" else "_" for c in info["tag"])
        dest = os.path.join(tempfile.gettempdir(), f"SimCast-Setup-{safe}.exe")

        def work():
            try:
                update.download_installer(info["installer_url"], dest, info["sha256"], current=APP_VERSION,
                                          progress=lambda got, total: self._update_events.append(("progress", (got, total))))
                update.start_installer(dest)
                self._update_events.append(("progress", "run"))
            except Exception as e:
                file_log(f"Update failed: {type(e).__name__}: {e}")
                self._update_events.append(("progress", f"error:{e}"))
            finally:
                self._updating = False
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _update_progress(self, p):
        if self.update_bar is None:
            return
        if p == "run":
            self.update_msg.configure(text="Setup started: it closes SimCast and updates it. Your settings are kept.")
        elif isinstance(p, str):
            self.update_msg.configure(text="The update couldn't be downloaded. Use What's new to get it from GitHub.",
                                      fg=P["rose"])
            self.update_btn.configure(state="normal")
        else:
            got, total = p
            self.update_msg.configure(text=f"Downloading… {got * 100 // total}%" if total else
                                      f"Downloading… {got // 1024} KB")

    def open_guide(self, topic=None):
        g = getattr(self, "guide_win", None)
        if g is not None and g.win.winfo_exists():
            g.win.lift()
            if topic:
                names = [t for t, _ in g.topics]
                g.show(names.index(topic) if topic in names else 0)
            return
        self.guide_win = GuideWindow(self, topic)

    def open_settings(self):
        if self.settings_win is not None and self.settings_win.win.winfo_exists():
            self.settings_win.win.lift()
            return
        self.settings_win = SettingsWindow(self)

    def run(self):
        self.hotkeys = hotkeys.Hotkeys(self.cast.config, self._hotkey, log=self.cast.log)
        self.hotkeys.start()
        try:
            self.cast.sync_stream()                     # OBS captions page and Twitch chat, if switched on
        except Exception as e:
            self.cast.log(f"Streaming: {e}")
        self.loop.start()
        self.root.after(100, self.poll)
        self.root.mainloop()

    def close(self):
        try:
            g = self.root.geometry().split("+")[0]
            if self.root.state() == "normal" and g != self.cast.cfg.get("window"):
                self.cast.update_settings({"window": g})
        except Exception:
            pass
        self.stop.set()
        try:
            self.cast.audio.interrupt(None)
        except Exception:
            pass
        self.root.destroy()



class Captions:
    """A small always-on-top window with the line being spoken: for a second screen, or a window capture in OBS."""

    def __init__(self, app):
        self.app = app
        f = app.f
        self.win = w = tk.Toplevel(app.root)
        w.title("SimCast captions")
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try:
            w.attributes("-alpha", 0.96)
        except Exception:
            pass
        w.configure(bg=P["bg"], highlightthickness=1, highlightbackground=P["line2"])
        pos = app.cast.cfg.get("captions_pos")
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        self.width = px(900)
        x, y = (pos if isinstance(pos, list) and len(pos) == 2 else (sw // 2 - self.width // 2, sh - px(220)))
        self.home_y = int(y)                       # where the user put it; long lines may lift it
        w.geometry(f"{self.width}x{px(110)}+{int(x)}+{int(y)}")
        self.bar = tk.Frame(w, bg=P["amber"], width=px(4))            # the speaker's colour down the left edge
        self.bar.pack(side="left", fill="y")
        inner = tk.Frame(w, bg=P["bg"], padx=px(18), pady=px(12))
        inner.pack(side="left", fill="both", expand=True)
        top = tk.Frame(inner, bg=P["bg"])
        top.pack(fill="x")
        self.who = tk.Label(top, text="SimCast", font=f["capwho"], fg=P["mut"], bg=P["bg"])
        self.who.pack(side="left")
        tk.Label(top, text="Drag to move  ·  right-click to close", font=f["small"], fg=P["dim"],
                 bg=P["bg"]).pack(side="right")
        self.text = tk.Label(inner, text="", font=f["cap"], fg=P["fg"], bg=P["bg"], wraplength=self.width - px(60),
                             justify="left", anchor="w")
        self.text.pack(fill="both", expand=True, pady=(px(4), 0))
        self.hide_at = 0.0
        for wd in (w, inner, top, self.text, self.who):
            wd.bind("<ButtonPress-1>", self._grab)
            wd.bind("<B1-Motion>", self._drag)
            wd.bind("<ButtonRelease-1>", self._drop)
            wd.bind("<Button-3>", lambda e: app.toggle_captions(False))

    def show(self, ln):
        col = WHO_COLOUR.get(ln["who"], P["amber"])
        self.bar.configure(bg=col)
        self.who.configure(text=ln["name"], fg=col)
        self.text.configure(text=ln["text"], fg=P["fg"])
        self._fit()
        self.hide_at = time.monotonic() + 2.0 + len(ln["text"].split()) * 0.38

    def _fit(self):
        """Grow or shrink the window's height so the whole line fits, keeping it on screen."""
        w = self.win
        try:
            w.update_idletasks()
            h = max(px(110), w.winfo_reqheight())
            x, sh = w.winfo_x(), w.winfo_screenheight()
            y = min(self.home_y, max(0, sh - h))  # grow upwards rather than run off the bottom of the screen
            w.geometry(f"{self.width}x{h}+{x}+{y}")
        except Exception:
            pass

    def idle(self):
        if self.hide_at and time.monotonic() > self.hide_at:
            self.text.configure(fg=P["dim"])
            self.hide_at = 0.0

    def _grab(self, e):
        self._off = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag(self, e):
        self.win.geometry(f"+{e.x_root - self._off[0]}+{e.y_root - self._off[1]}")

    def _drop(self, e):
        self.home_y = self.win.winfo_y()
        self.app.cast.update_settings({"captions_pos": [self.win.winfo_x(), self.win.winfo_y()]})

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


class SettingsWindow:
    """Settings, in seven tabs: Commentary, Booth, Voices, AI, Sims, Stream, About."""

    def __init__(self, app):
        self.app = app
        cast = app.cast
        cfg = cast.config()
        self.win = w = tk.Toplevel(app.root)
        w.title("SimCast settings")
        w.configure(bg=P["panel"])
        w.transient(app.root)
        w.resizable(False, False)
        dark_titlebar(w, P["bg"])
        w.columnconfigure(1, weight=1)
        w.rowconfigure(0, weight=1)
        side = tk.Frame(w, bg=P["bg"], padx=px(10), pady=px(18))
        side.grid(row=0, column=0, rowspan=2, sticky="ns")
        tk.Label(side, text="Settings", font=app.f["h2"], fg=P["fg"], bg=P["bg"], anchor="w").pack(
            fill="x", padx=px(12), pady=(0, px(14)))
        self.titles = []
        main = tk.Frame(w, bg=P["panel"])
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self.page_title = tk.Label(main, text="", font=app.f["h2"], fg=P["fg"], bg=P["panel"], anchor="w")
        self.page_title.grid(row=0, column=0, sticky="ew", padx=px(28), pady=(px(22), 0))
        self.nb = nb = ttk.Notebook(main, style="Side.TNotebook")
        nb.grid(row=1, column=0, sticky="nsew")
        self.v = {}
        self.f = app.f

        self.scrolls = []

        def page(title):
            sc = ui.Scrolled(nb, P["panel"], lambda c: ttk.Frame(c, style="Card.TFrame",
                                                                 padding=(px(28), px(14), px(28), px(20))))
            f = sc.inner
            f.columnconfigure(1, weight=1)
            nb.add(sc, text=title)
            self.titles.append(title)
            self.scrolls.append(sc)
            return f

        # ---------------------------------------------------------------- Commentary
        f = page("Commentary")
        self._style_block(f, cfg)
        self.row(f, 6, "Your name", self.entry(f, "driver_name", cfg))
        self.note(f, 7, "As the booth should say it. Empty: the name your sim uses.")
        self.row(f, 8, "Call me", self.radios(f, "driver_pronouns", PRONOUN_LABELS, cfg), sticky="w")
        self.note(f, 9, "Other drivers are 'he' or 'she' when their first name makes it clear, 'they' when it "
                        "doesn't.")
        self.row(f, 10, "Pit lane", self.check(f, "pit_reporter", "A pit-lane reporter covers the stops", cfg))
        self.row(f, 12, "Mode", self.radios(f, "drive_mode", DRIVE_LABELS, cfg), sticky="w")
        self.note(f, 13, "Driving: the booth holds back while you brake into a corner, race a car within a second, "
                        "or go off, and stops a side conversation when someone gets alongside. What happened is "
                        "called on the next straight. Stream: the full show for viewers.")

        # ---------------------------------------------------------------- Booth
        f = page("Booth")
        self.row(f, 3, "Race name", self.entry(f, "race_name", cfg, width=44))
        self.note(f, 4, "What the booth calls this race, e.g. 2026 Las Vegas Grand Prix. Empty: just the track.")
        self.row(f, 5, "Race sponsor", self.entry(f, "race_sponsor", cfg, width=44))
        self.row(f, 6, "Your sponsors", self.entry(f, "my_sponsors", cfg, width=44))
        self.note(f, 7, "Comma-separated, e.g. Acme Oil, Bolt Energy.")
        self.row(f, 8, "Other drivers", self.entry(f, "driver_sponsors", cfg, width=44))
        self.note(f, 9, "Name: sponsors, separated by semicolons, e.g. Okafor: Acme Oil, Bolt; Lewis Hamilton: Zed "
                        "Watches.")
        self.row(f, 10, "", self.check(f, "invent_sponsors", "Make up sponsors for anyone without any", cfg))
        self.row(f, 11, "Memory", self.check(f, "memory", "Remember races: results, rivalries and who called what",
                                             cfg),
                 Btn(f, text="Forget", style="Small.TButton", command=self._forget))
        self.note(f, 12, "So the booth can say 'these two made contact at Spa last time out'. Demo races aren't "
                         "remembered. Drivers are matched by name. With an AI key and 'Season storylines' on, the AI "
                         "reads the results after each race and writes the running stories (a streak, a rivalry, a "
                         "slump) for the booth to bring up next time.")
        rf = ttk.Frame(f, style="Card.TFrame")
        self.entry(rf, "roster_file", cfg, width=34).pack(side="left", fill="x", expand=True)
        Btn(rf, text="Browse", style="Small.TButton", command=self._browse_roster).pack(side="left", padx=(6, 0))
        Btn(rf, text="Make one", style="Small.TButton", command=self._make_roster).pack(side="left", padx=(6, 0))
        self.row(f, 13, "League roster", rf)
        self.note(f, 14, "A spreadsheet (CSV) with Driver, Number, Team, Pronouns, Sponsors and Say as. It wins over "
                         "the guesses above and gives the booth 'the number 12 Porsche'. Make one starts it with the "
                         "drivers on track now.")
        self.row(f, 15, "NASCAR mode", self.radios(f, "nascar", NASCAR_LABELS, cfg), sticky="w")
        self.row(f, 16, "   stages end on lap", self.entry(f, "nascar_stages", cfg, width=20), sticky="w")
        self.note(f, 17, "The NASCAR button in the main window switches it on or off for one race (until the track "
                         "changes), whatever this says. Stock cars and ovals get a NASCAR broadcast: green flag, cautions and restarts, stages and "
                         "stage points, laps led, the free pass, pit road, the checkered flag. Stages: e.g. 60, 185. "
                         "Empty: about 30% and 60% of a race of 20 laps or more. off: no stages.")
        self.row(f, 18, "", self.check(f, "nascar_voices", "American voices for NASCAR races (unless you've picked "
                                                           "your own)", cfg))
        self.row(f, 19, "Season storylines", self.check(f, "season_stories", "The AI writes the season's running "
                                                                             "stories into the memory after each race",
                                                        cfg))

        # ---------------------------------------------------------------- Voices
        f = page("Voices")
        self.row(f, 0, "Speech", self.check(f, "tts", "The booth speaks (off: text only)", cfg, on="on", off="off"))
        self.voice_boxes = {}
        from . import eleven
        self.all_voices = VOICES + eleven.voices(cfg)
        labels_of = {v: n for n, v in self.all_voices}
        r = 1
        for who, label in (("lead", "Lead commentator"), ("analyst", "Analyst"), ("pit", "Pit-lane reporter")):
            self.row(f, r, label, self.entry(f, f"{who}_name", cfg),
                     Btn(f, text="Test", style="Small.TButton", command=lambda w=who: self.test_voice(w)))
            cb = ttk.Combobox(f, state="readonly", width=44, values=[n for n, _ in self.all_voices], height=18)
            cur = cfg.get(f"{who}_voice")
            cb.set(labels_of.get(cur) or VOICE_LABEL.get(cur) or
                   ("ElevenLabs voice (add your key to list it)" if str(cur).startswith("eleven:") else VOICES[0][0]))
            self.voice_boxes[who] = cb
            self.row(f, r + 1, "   voice", cb)
            r += 2
        outs = []
        try:
            outs = cast.audio.list_outputs() if hasattr(cast.audio, "list_outputs") else []
        except Exception:
            outs = []
        cur_out = cfg.get("audio_out") or ""
        if cur_out and cur_out not in outs:
            outs.append(cur_out)
        self.row(f, r, "Play on", self.combo(f, "audio_out", [""] + outs, {"": "Windows default", **{o: o for o in outs}}, cfg),
                 Btn(f, text="Test", style="Small.TButton", command=lambda: self.test_voice("lead")))
        self.v["volume"] = tk.DoubleVar(value=float(cfg.get("volume", 0.9)))
        self.row(f, r + 1, "Volume", ttk.Scale(f, from_=0, to=1, variable=self.v["volume"]))
        self.v["speech_rate"] = tk.DoubleVar(value=float(cfg.get("speech_rate", 0)))
        self.row(f, r + 2, "Speed", ttk.Scale(f, from_=-20, to=20, variable=self.v["speech_rate"]))
        self.row(f, r + 3, "When SimCoach talks", self.combo(f, "simcoach_duck", list(DUCK_LABELS), DUCK_LABELS, cfg))
        self.note(f, r + 4, "With SimCoach running too, both keep talking: the booth drops under the coach and crew "
                            "while they speak, then comes back up.")
        self.note(f, r + 5, "Microsoft and Google voices need the internet. Kokoro voices run on this PC (a one-time "
                            "download of about 115 MB). OpenAI voices use your OpenAI key and are the most lifelike. "
                            "If a voice can't be reached, the line is spoken by Kokoro, Google or Windows instead.")
        self.row(f, r + 6, "ElevenLabs key", self.entry(f, "elevenlabs_api_key", cfg, show="•"),
                 ttk.Label(f, text="✓ set" if cast.state()["settings"]["keys"].get("elevenlabs_api_key") else "",
                           style="Card.TLabel", foreground=P["green"]))
        self.note(f, r + 7, "Emotion: the free voices (Microsoft, Google, Kokoro) can only change pace, pitch and "
                            "volume, so 'angry' mostly sounds faster and louder. OpenAI voices are told how to say "
                            "each line and carry real feeling. ElevenLabs voices carry the most: each line's mood "
                            "goes in as a cue ([excited], [sighs], [laughs]...). With a key, the voices on your "
                            "ElevenLabs account appear in the lists above (reopen Settings after adding it).")

        # ---------------------------------------------------------------- AI
        f = page("AI")
        self.note(f, 0, "Without AI the booth calls every moment from its own lines. With a key, the two commentators "
                        "talk to each other: the analyst answers the calls, quiet spells become real conversations "
                        "that run on for a few exchanges, every line gets its emotion, and there's a sign-off at the end. Keys stay on this PC.", span=3)
        self.row(f, 1, "AI commentary", self.check(f, "ai_colour", "On", cfg))
        self.row(f, 2, "Use first", self.combo(f, "ai_provider", list(PROVIDERS), PROVIDERS, cfg))
        self.row(f, 3, "", self.check(f, "ai_fallback", "If it's down or out of credit, use another provider with a key", cfg))
        self.row(f, 4, "Conversations", self.combo(f, "ai_convo", list(CONVO_LABELS), CONVO_LABELS, cfg))
        self.note(f, 5, "A second AI that writes the booth's conversations alongside the first, so the chat keeps "
                        "flowing while the big moments are answered. Automatic uses DeepSeek (or another provider) "
                        "when it has a key.")
        has = cast.state()["settings"]["keys"]
        self.model_boxes = {}
        r = 6
        for p, label in PROVIDERS.items():
            self.row(f, r, f"{label} key", self.entry(f, f"{p}_api_key", cfg, show="•"),
                     ttk.Label(f, text="✓ set" if has.get(f"{p}_api_key") else "", style="Card.TLabel",
                               foreground=P["green"]))
            cb = ttk.Combobox(f, state="readonly", width=60)
            self._fill_models(cb, p, cfg)
            self.model_boxes[p] = cb
            self.row(f, r + 1, "   model", cb)
            r += 2
        tts = ttk.Combobox(f, state="readonly", width=44)
        self._fill_tts(tts, cfg)
        self.tts_box = tts
        self.row(f, r, "OpenAI speech", tts)
        imp = providers.import_simcoach_keys()
        if imp:
            Btn(f, text="Copy keys from SimCoach", style="Small.TButton",
                       command=lambda: self._import(imp)).grid(row=r + 1, column=1, sticky="w", pady=(6, 0))
        st = cast.state()
        ttk.Label(f, style="Card.TLabel", foreground=P["green"] if st["ai"] else P["mut"], wraplength=px(520),
                  text=(f"Now using: {st['ai']} for reactions" if st["ai"] else "No AI key yet: the booth uses its own lines.") +
                       (f"\nConversations: {st['ai_convo']}" if st.get("ai_convo") else "") +
                       (f"\nLast problem: {st['ai_error']}" if st.get("ai_error") else "")
                  ).grid(row=r + 2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        from .usage import DEFAULT_RATES, METER
        self.row(f, r + 3, "Cost rates", self.entry(f, "ai_rates", cfg, width=60))
        self.note(f, r + 4, f"{METER.summary(cfg.get('ai_rates') or '')} Rough estimate: dollars per million tokens "
                            f"(in/out) and per million characters for voices. Empty uses these defaults, which may be "
                            f"out of date: {DEFAULT_RATES}.", span=3)
        self.row(f, r + 5, "Write-ups", self.combo(f, "ai_assist", list(ASSIST_LABELS), ASSIST_LABELS, cfg))
        self.note(f, r + 6, "Jobs off the air, with time to do them well: a race report, YouTube titles, a "
                            "description and tags in each recap, and the season's storylines for the booth's memory. "
                            "Automatic uses DeepSeek (the cheapest good writer) when it has a key, then OpenAI. "
                            "Turn each one on or off under Stream and Booth." +
                            (f" Now: {st['ai_assist']}." if st.get("ai_assist") else ""))
        keys_fr = ttk.Frame(f, style="Card.TFrame")
        keys_msg = ttk.Label(f, text="", style="Card.TLabel", foreground=P["mut"], wraplength=px(520), justify="left")
        Btn(keys_fr, text="Test keys", style="Small.TButton",
                   command=lambda: self._test_keys(keys_msg)).pack(side="left")
        self.row(f, r + 7, "Check", keys_fr, sticky="w")
        keys_msg.grid(row=r + 8, column=0, columnspan=3, sticky="w")

        # ---------------------------------------------------------------- Sims
        f = page("Sims")
        self.row(f, 0, "Game setup", self.check(f, "game_setup", "Switch on what installed sims need, automatically", cfg))
        self.note(f, 1, "EA WRC, DiRT Rally 2.0 and F1 25 telemetry, ACC's broadcasting port and Assetto Corsa's "
                        "CrewChiefEx app (backing up what it changes). Restart a game once after it's been set up.")
        self.row(f, 2, "F1 25 port", self.entry(f, "f1_port", cfg, width=8), sticky="w")
        self.row(f, 3, "SimHub", self.check(f, "udp_share_simhub", "Share UDP port 20777 with SimHub", cfg))
        self.row(f, 4, "Forza port", self.entry(f, "forza_port", cfg, width=8), sticky="w")
        box = ttk.Frame(f, style="Card.TFrame")
        box.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 4))
        sims_list(box, cast.state().get("sims") or [], self.f, wrap=300)

        def rescan():
            cast.scan_sims(setup=False)
            sims_list(box, cast.state().get("sims") or [], self.f, wrap=300)
        Btn(f, text="Scan again", style="Small.TButton", command=rescan).grid(row=6, column=0, sticky="w",
                                                                                   pady=(4, 8))
        self.note(f, 7, "Whole field: Le Mans Ultimate, rFactor 2, iRacing, Automobilista 2, Project CARS 2 and 3, "
                        "RaceRoom, F1 25, ACC, Assetto Corsa (with the CrewChiefEx app). Your car only: AC EVO, "
                        "Forza Motorsport, Forza Horizon 5 and 4, Assetto Corsa without CrewChiefEx. Rally stages: EA SPORTS WRC, DiRT Rally 2.0, AC Rally. The Guide has the "
                        "one-time game settings.", span=3)
        r = 8
        self.hotkey_vars = {}
        for act in hotkeys.ACTIONS:
            self.row(f, r, hotkeys.ACTION_LABEL[act], self._hotkey_row(f, act, cfg))
            r += 1
        self.note(f, r, "Click Set, then press a key or a button on your wheel or button box. They work while the sim "
                        "has the focus.")

        # ---------------------------------------------------------------- Stream
        f = page("Stream")
        self.row(f, 0, "OBS captions", self.check(f, "obs_captions", "Show the captions on a page for OBS", cfg))
        port = ttk.Frame(f, style="Card.TFrame")
        self.entry(port, "obs_port", cfg, width=8).pack(side="left")
        url = f"http://127.0.0.1:{cfg.get('obs_port') or 8686}/"
        ttk.Label(port, text="   " + url, style="Card.TLabel").pack(side="left")
        Btn(port, text="Copy", style="Small.TButton",
                   command=lambda: (self.win.clipboard_clear(), self.win.clipboard_append(url))).pack(side="left", padx=8)
        self.row(f, 1, "Port", port, sticky="w")
        self.note(f, 2, "In OBS: Sources > + > Browser, paste the address, width 1600, height 300. The background is "
                        "transparent; each line shows with the speaker's name in their colour, then fades. Add "
                        "?lines=1 to the address to show one line at a time.")
        self.row(f, 3, "Twitch channel", self.entry(f, "twitch_channel", cfg))
        self.row(f, 4, "", self.check(f, "twitch_chat", "The booth answers the odd message in a quiet spell", cfg))
        self.row(f, 5, "", self.check(f, "twitch_only_booth", "Only messages that start with !booth", cfg))
        self.row(f, 6, "", self.check(f, "twitch_moderate", "Screen chat with OpenAI's moderation check first "
                                                            "(free with an OpenAI key)", cfg))
        self.note(f, 7, "Chat is read without logging in, and nothing is ever posted. It needs an AI key: the AI picks "
                        "a message, puts it in its own words and answers it ('Kate, someone in chat says you're "
                        "wrong'). Abuse, spam, links and attempts to make the booth say something are ignored, and "
                        "chat is never read out word for word.")
        self.row(f, 8, "Recap", self.check(f, "recap", "Write a recap after each race", cfg),
                 Btn(f, text="Open folder", style="Small.TButton",
                            command=lambda: (os.makedirs(os.path.join(APP_DIR, "Recaps"), exist_ok=True),
                                             _open_path(os.path.join(APP_DIR, "Recaps")))))
        off = ttk.Frame(f, style="Card.TFrame")
        self.entry(off, "recap_offset", cfg, width=8).pack(side="left")
        ttk.Label(off, text="  seconds from the start of your stream to lights out", style="Card.TLabel",
                  foreground=P["mut"]).pack(side="left")
        self.row(f, 9, "", self.check(f, "recap_ai", "Add an AI race report, YouTube titles, a description "
                                                     "and tags", cfg))
        self.row(f, 10, "Offset", off, sticky="w")
        self.note(f, 11, "The result, YouTube chapters to paste into the description, the highlights and the whole "
                         "transcript, with timestamps from lights out (plus the offset). In Recaps next to the "
                         "settings. With an AI key, a written race report and what you need to post the video are "
                         "added a minute later (not for demo races).")

        # ---------------------------------------------------------------- About
        f = page("About")
        top = ttk.Frame(f, style="Card.TFrame")
        top.grid(row=0, column=0, columnspan=3, sticky="w")
        if app.img_logo:
            ttk.Label(top, image=app.img_logo, style="Card.TLabel").pack(side="left", padx=(0, 10))
        ttk.Label(top, text=f"SimCast {APP_VERSION}", style="Card.TLabel", font=self.f["h1"]).pack(side="left")
        self.note(f, 1, "Live TV-style commentary for sim racing. PolyForm Noncommercial License 1.0.0, Copyright (c) 2026 Jcedwar61. "
                        "SimCast works on its own: SimCoach doesn't need to be installed.", span=3)
        b = ttk.Frame(f, style="Card.TFrame")
        b.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        Btn(b, text="Guide", style="Small.TButton", command=app.open_guide).pack(side="left", padx=(0, 6))
        Btn(b, text="Licences", style="Small.TButton",
                   command=lambda: app.open_guide("About and licences")).pack(side="left", padx=6)
        Btn(b, text="Open log folder", style="Small.TButton",
                   command=lambda: _open_path(APP_DIR)).pack(side="left", padx=6)
        self.row(f, 3, "Updates", self.check(f, "check_updates", "Check for a new version when SimCast starts", cfg),
                 Btn(f, text="Check now", style="Small.TButton",
                            command=lambda: app.check_for_updates(manual=True)))
        ttk.Label(f, textvariable=app.update_status, style="Card.TLabel", foreground=P["mut"],
                  wraplength=px(500)).grid(row=4, column=1, columnspan=2, sticky="w")
        self.note(f, 5, "A new version shows a bar at the top of the window. Install update downloads its setup "
                        "wizard from GitHub, checks it against the published checksum and runs it; nothing is "
                        "installed until you click.", span=3)

        tk.Frame(main, bg=P["line"], height=1).grid(row=2, column=0, sticky="ew")
        bb = tk.Frame(main, bg=P["panel"], padx=px(28), pady=px(14))
        bb.grid(row=3, column=0, sticky="ew")
        Btn(bb, text="Save changes", style="Accent.TButton", command=self.save).pack(side="right")
        Btn(bb, text="Cancel", command=w.destroy).pack(side="right", padx=px(8))
        self.nav = ui.NavList(side, self.titles, nb.select, bg=P["bg"], width=0)
        self.nav.pack(fill="x")
        nb.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        self._tab_changed()
        w.update_idletasks()
        inner_w = max(sc.inner.winfo_reqwidth() for sc in self.scrolls) + px(14)
        inner_h = max(sc.inner.winfo_reqheight() for sc in self.scrolls)
        for sc in self.scrolls:
            sc.canvas.configure(width=inner_w, height=px(200))
        w.update_idletasks()
        chrome = w.winfo_reqheight() - px(200)
        W = side.winfo_reqwidth() + inner_w + px(20)
        H = min(chrome + inner_h, w.winfo_screenheight() - px(120), chrome + px(640))
        x = app.root.winfo_rootx() + (app.root.winfo_width() - W) // 2
        y = max(0, min(app.root.winfo_rooty() + px(40), w.winfo_screenheight() - H - px(60)))
        w.geometry(f"{W}x{H}+{max(0, x)}+{y}")

    def _tab_changed(self):
        try:
            i = self.nb.index(self.nb.select())
        except Exception:
            return
        self.nav.select(i)
        self.page_title.configure(text=self.titles[i])

    # ---------------------------------------------------------------- the style presets
    def _style_block(self, f, cfg):
        """Style: one click for the whole feel of the booth. The settings it sets live under Fine-tune, and
        touching any of them makes the style Custom."""
        box = f
        self._style_parent = f
        cur = style_of(cfg)
        self.style_var = tk.StringVar(value=cur)
        fr = Segmented(box, self.style_var, STYLE_LABELS, command=lambda: self._apply_style())
        self.row(box, 0, "Style", fr, sticky="w")
        self.style_note = ttk.Label(box, text=STYLE_NOTES[cur], style="Card.TLabel", foreground=P["mut"],
                                    font=self.f["small"], wraplength=px(540), justify="left")
        self.style_note.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 6))
        self.fine_btn = Btn(box, text="Fine-tune  ▸", kind="ghost", small=True, command=self._toggle_fine)
        self.fine_btn.grid(row=2, column=1, sticky="w", pady=(0, 6))
        d = self.fine = ttk.Frame(box, style="Card.TFrame")
        d.columnconfigure(1, weight=1)
        self._fine_cols = d
        self.row(d, 0, "How chatty", self.radios(d, "chatter", CHATTER_LABELS, cfg), sticky="w")
        self.row(d, 1, "Follow", self.radios(d, "focus", FOCUS_LABELS, cfg), sticky="w")
        self.row(d, 2, "Personality", self.radios(d, "booth_tone", TONE_LABELS, cfg), sticky="w")
        self.note(d, 3, "Professional: a TV booth. Loose: more banter, ribbing and big reactions. Unhinged: wild "
                        "overreactions, rants, feuds and conspiracy theories, always set off by the race or by what "
                        "was just said.")
        self.row(d, 4, "Language", self.check(d, "swearing", "Let them swear when a moment earns it", cfg))
        self.row(d, 5, "Random chat", self.radios(d, "banter", BANTER_LABELS, cfg), sticky="w")
        self.note(d, 6, "How often the booth wanders into side conversations in the quiet spells. They always start "
                        "from something on track and come back to the race.")
        self._applying = False
        for k in ("chatter", "focus", "booth_tone", "banter"):
            self.v[k].trace_add("write", lambda *a: self._style_follow())
        self.v["swearing"][1].trace_add("write", lambda *a: self._style_follow())
        if cur == "custom":
            self._toggle_fine()

    def _style_values(self):
        out = {k: self.v[k].get() for k in ("chatter", "focus", "booth_tone", "banter")}
        out["swearing"] = bool(self.v["swearing"][1].get())
        return out

    def _apply_style(self):
        s = self.style_var.get()
        self.style_note.configure(text=STYLE_NOTES.get(s, ""))
        if s == "custom":
            if not self.fine.winfo_manager():
                self._toggle_fine()
            return
        self._applying = True
        try:
            for k, val in STYLES[s].items():
                if k == "swearing":
                    self.v[k][1].set(bool(val))
                else:
                    self.v[k].set(val)
        finally:
            self._applying = False

    def _style_follow(self):
        """A setting under Fine-tune was changed by hand: the style is whichever one now matches, or Custom."""
        if self._applying:
            return
        s = style_of(self._style_values())
        self.style_var.set(s)
        self.style_note.configure(text=STYLE_NOTES.get(s, ""))

    def _toggle_fine(self):
        if self.fine.winfo_manager():
            self.fine.grid_remove()
            self.fine_btn.configure(text="Fine-tune  ▸")
        else:
            self.fine.grid(row=3, column=0, columnspan=3, sticky="ew")
            self.fine_btn.configure(text="Fine-tune  ▾")
            try:                                   # line its labels up with the rest of the page
                self.win.update_idletasks()
                w = self._style_parent.grid_bbox(0, 0)[2]
                self.fine.columnconfigure(0, minsize=w)
            except Exception:
                pass

    # ---------------------------------------------------------------- widgets
    def _browse_roster(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=self.win, title="League roster",
                                       filetypes=[("Spreadsheet (CSV)", "*.csv"), ("All files", "*.*")])
        if p:
            self.v["roster_file"].set(p)

    def _make_roster(self):
        from tkinter import filedialog
        from . import roster
        p = filedialog.asksaveasfilename(parent=self.win, title="Save the roster as", defaultextension=".csv",
                                         initialfile="SimCast roster.csv", filetypes=[("Spreadsheet (CSV)", "*.csv")])
        if not p:
            return
        tr = self.app.cast.tracker
        names = [c.name for c in sorted(getattr(tr, "cars", {}).values(), key=lambda c: c.position or 99)]
        roster.write_template(p, names)
        self.v["roster_file"].set(p)
        _open_path(p)

    def _forget(self):
        from tkinter import messagebox
        mem = getattr(self.app.cast, "memory", None)
        if mem is not None and messagebox.askyesno("Forget", "Forget every race the booth remembers?",
                                                   parent=self.win):
            mem.forget()

    def _hotkey_row(self, f, act, cfg):
        fr = ttk.Frame(f, style="Card.TFrame")
        var = tk.StringVar(value=cfg.get(f"hotkey_{act}") or "")
        self.v[f"hotkey_{act}"] = var
        shown = ttk.Label(fr, text=hotkeys.describe(var.get()), style="Card.TLabel", width=34)
        shown.pack(side="left")

        def got(binding):
            def apply():
                if binding:
                    var.set(binding)
                shown.configure(text=hotkeys.describe(var.get()))
                btn.configure(text="Set")
            try:
                self.win.after(0, apply)
            except Exception:
                pass

        def learn():
            hk = getattr(self.app, "hotkeys", None)
            if hk is None or hk._thread is None:
                shown.configure(text="Hotkeys work on Windows only")
                return
            btn.configure(text="Press...")
            shown.configure(text="Press a key or a wheel button")
            hk.learn(got)

        def clear():
            var.set("")
            shown.configure(text=hotkeys.describe(""))
        btn = Btn(fr, text="Set", style="Small.TButton", command=learn)
        btn.pack(side="left", padx=(6, 0))
        Btn(fr, text="Clear", style="Small.TButton", command=clear).pack(side="left", padx=(6, 0))
        return fr

    def row(self, f, r, label, widget, extra=None, sticky="ew"):
        ttk.Label(f, text=label.strip(), style="Card.TLabel", foreground=P["mut"] if not label.startswith(" ")
                  else P["dim"]).grid(row=r, column=0, sticky="w", padx=(px(16) if label.startswith(" ") else 0,
                                                                         px(20)), pady=px(5))
        widget.grid(row=r, column=1, sticky=sticky, pady=px(5))
        if extra is not None:
            extra.grid(row=r, column=2, sticky="w", padx=(px(8), 0), pady=px(5))

    def note(self, f, r, text, span=2):
        ttk.Label(f, text=text, style="Card.TLabel", foreground=P["dim"], font=self.f["small"],
                  wraplength=px(540), justify="left").grid(row=r, column=0 if span == 3 else 1, columnspan=span,
                                                           sticky="w", pady=(0, px(8)))

    def radios(self, f, key, labels, cfg):
        var = self.v[key] = tk.StringVar(value=cfg.get(key))
        return Segmented(f, var, labels)

    def entry(self, f, key, cfg, show=None, width=30):
        var = self.v[key] = tk.StringVar(value="" if show else str(cfg.get(key) or ""))
        return ttk.Entry(f, textvariable=var, width=width, show=show or "")

    def check(self, f, key, text, cfg, on=True, off=False):
        var = tk.BooleanVar(value=cfg.get(key) == on)
        self.v[key] = ("check", var, on, off)
        return Switch(f, text, var)

    def combo(self, f, key, values, labels, cfg):
        var = tk.StringVar(value=labels.get(cfg.get(key), cfg.get(key) or ""))
        self.v[key] = (var, {v: k for k, v in labels.items()})
        return ttk.Combobox(f, textvariable=var, values=[labels[x] for x in values], state="readonly", width=44)

    def _fill_models(self, cb, p, cfg):
        best = providers.best_model(cfg, p, "react")
        talk = providers.best_model(cfg, p, "convo")
        choices = providers.model_choices(cfg, p)
        labels = [f"Automatic (best: {best})" if talk == best else
                  f"Automatic (reactions: {best}, conversations: {talk})"] + choices
        cb.configure(values=labels)
        cur = cfg.get(f"{p}_model") or providers.AUTO
        cb.set(labels[0] if cur == providers.AUTO or cur not in choices else cur)

    def _fill_tts(self, cb, cfg):
        d = providers.detected(cfg, "openai")
        choices = (d["tts"] if d and d["tts"] else [providers.TTS_FALLBACK])[:8]
        labels = [f"Automatic (best: {providers.tts_model(dict(cfg, openai_tts_model=providers.AUTO))})"] + choices
        cb.configure(values=labels)
        cur = cfg.get("openai_tts_model") or providers.AUTO
        cb.set(labels[0] if cur == providers.AUTO or cur not in choices else cur)

    def _import(self, keys):
        self.app.cast.update_settings(keys)
        self.win.destroy()
        self.app.open_settings()
        self.app.settings_win.nb.select(3)

    def _collect(self):
        out = {}
        for k, var in self.v.items():
            if isinstance(var, tuple) and var[0] == "check":
                out[k] = var[2] if var[1].get() else var[3]
            elif isinstance(var, tuple):
                txt, back = var[0].get(), var[1]
                out[k] = back.get(txt, txt)
            else:
                out[k] = var.get()
        by_label = {n: v for n, v in getattr(self, "all_voices", VOICES)}
        for who, cb in self.voice_boxes.items():
            cur = self.app.cast.cfg.get(f"{who}_voice")
            out[f"{who}_voice"] = by_label.get(cb.get(), cur if str(cur).startswith("eleven:") else
                                                DEFAULTS[f"{who}_voice"])
        for p, cb in self.model_boxes.items():
            val = cb.get()
            out[f"{p}_model"] = providers.AUTO if val.startswith("Automatic") else val
        val = self.tts_box.get()
        out["openai_tts_model"] = providers.AUTO if val.startswith("Automatic") else val
        for k in ("demo_laps", "f1_port", "forza_port", "obs_port", "recap_offset"):
            try:
                out[k] = int(out.get(k) or DEFAULTS[k])
            except ValueError:
                out[k] = DEFAULTS[k]
        out["speech_rate"] = int(round(float(out.get("speech_rate", 0))))
        return out

    def _test_keys(self, label):
        """Settings > AI > Test keys: a tiny request to every provider with a key, in the background."""
        from . import assist
        self.app.cast.update_settings(self._collect())
        cfg = self.app.cast.config()
        if not any(providers.ai_key(cfg, p) for p in providers.AI_PROVIDERS):
            label.configure(text="No AI key to test yet.", foreground=P["mut"])
            return
        label.configure(text="Testing...", foreground=P["mut"])

        def work():
            res = assist.test_keys(cfg)
            ok = all(v[0] for v in res.values())
            text = "\n".join(v[1] for v in res.values())

            def show():
                try:
                    label.configure(text=text, foreground=P["green"] if ok else P["red"])
                except Exception:
                    pass
            try:
                self.win.after(0, show)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True, name="key-test").start()

    def test_voice(self, who):
        self.app.cast.update_settings(self._collect())
        cfg = self.app.cast.config()
        text = {"lead": f"Hello, I'm {cfg.get('lead_name')}, and I'll be calling the action today!",
                "analyst": f"And I'm {cfg.get('analyst_name')}. I'll be telling you what it all means.",
                "pit": f"{cfg.get('pit_name')} here in the pit lane, I'll bring you every stop."}[who]
        audio = self.app.cast.audio
        if hasattr(audio, "preview"):
            audio.preview(who, text, 2)

    def save(self):
        self.app.cast.update_settings(self._collect())
        self.win.destroy()


class GuideWindow:
    """The built-in guide: topics on the left, the page on the right."""

    def __init__(self, app, topic=None):
        self.app = app
        self.win = w = tk.Toplevel(app.root)
        w.title("SimCast guide")
        w.configure(bg=P["bg"])
        w.geometry(f"{px(1040)}x{px(680)}")
        w.minsize(px(760), px(480))
        dark_titlebar(w)
        w.columnconfigure(1, weight=1)
        w.rowconfigure(0, weight=1)
        self.topics = list(GUIDE) + [("About and licences", None)]
        side = tk.Frame(w, bg=P["bg"], padx=px(10), pady=px(18))
        side.grid(row=0, column=0, sticky="ns")
        tk.Label(side, text="Guide", font=app.f["h2"], fg=P["fg"], bg=P["bg"], anchor="w").pack(
            fill="x", padx=px(12), pady=(0, px(14)))
        self.nav = ui.NavList(side, [t for t, _ in self.topics], self.show, bg=P["bg"], width=0)
        self.nav.pack(fill="x")
        self.buttons = [lab for _, _, lab in self.nav.rows]
        body = ui.card(w, r=12)
        body.grid(row=0, column=1, sticky="nsew", padx=(px(6), px(18)), pady=px(18))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.text = tk.Text(body, bg=P["panel"], fg=P["fg2"], relief="flat", wrap="word", font=app.f["line"],
                            padx=px(32), pady=px(24), highlightthickness=0, borderwidth=0, spacing1=px(2),
                            spacing2=px(3), spacing3=px(6), cursor="arrow")
        self.text.grid(row=0, column=0, sticky="nsew", pady=px(2))
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, px(6)), pady=px(12))
        self.text.configure(yscrollcommand=ui.autohide(sb))
        self.text.tag_configure("h", font=app.f["h2"], foreground=P["fg"], spacing1=px(18), spacing3=px(8))
        self.text.tag_configure("b", lmargin1=px(6), lmargin2=px(24))
        self.text.tag_configure("mono", font=app.f["mono"], foreground=P["mut"])
        names = [t for t, _ in self.topics]
        self.show(names.index(topic) if topic in names else 0)

    def show(self, i):
        self.nav.select(i)
        title, body = self.topics[i]
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if body is None:
            self._licences()
        else:
            for line in body.strip().splitlines():
                if line.startswith("## "):
                    t.insert("end", line[3:] + "\n", "h")
                elif line.startswith("- "):
                    t.insert("end", "•   " + line[2:] + "\n", "b")
                elif line.strip():
                    t.insert("end", line + "\n")
        t.configure(state="disabled")
        t.yview_moveto(0)
        self.current = title

    def _licences(self):
        t = self.text
        t.insert("end", f"SimCast {APP_VERSION}\n", "h")
        t.insert("end", "SimCast is free for noncommercial use under the PolyForm Noncommercial License 1.0.0. It works on its own; SimCoach isn't needed.\n")
        for name, title in (("LICENSE.txt", "SimCast licence (PolyForm Noncommercial)"), ("THIRD_PARTY_NOTICES.txt", "Third-party notices")):
            t.insert("end", title + "\n", "h")
            try:
                with open(os.path.join(LICENSE_DIR, name), encoding="utf-8") as fh:
                    t.insert("end", fh.read() + "\n", "mono")
            except Exception:
                t.insert("end", f"({name} is missing from this installation)\n", "mono")
        t.insert("end", "Full licence texts referred to above (LGPL-3.0, GPL-3.0) are in the licenses folder of the "
                        "SimCast installation.\n")


def sims_list(box, sims, f, wrap=None):
    """The installed sims, one row each: the name, what it shares or still needs, and READY or SET UP."""
    for w in box.winfo_children():
        w.destroy()
    bg = ui.bg_of(box)
    wrap = px(wrap or 520)
    if not sims:
        tk.Label(box, text="None found yet. SimCast still picks up a supported sim as soon as it's running.",
                 bg=bg, fg=P["mut"], font=f["ui"]).grid(row=0, column=0, sticky="w")
        return
    box.columnconfigure(1, weight=1)
    for i, s in enumerate(sims):
        if i:
            tk.Frame(box, bg=P["line"], height=1).grid(row=2 * i - 1, column=0, columnspan=3, sticky="ew")
        tk.Label(box, text=s["name"], font=f["uib"], fg=P["fg"], bg=bg, anchor="w", width=30).grid(
            row=2 * i, column=0, sticky="nw", pady=px(9), padx=(0, px(16)))
        tk.Label(box, text=s["status"], font=f["ui"], fg=P["mut"], bg=bg, wraplength=wrap, justify="left",
                 anchor="w").grid(row=2 * i, column=1, sticky="nw", pady=px(9))
        Pill(box, "READY" if s["ok"] else "SET UP", fg=P["green"] if s["ok"] else P["amber"],
             fill=P["green_tint"] if s["ok"] else P["amber_tint"]).grid(row=2 * i, column=2, sticky="ne",
                                                                        pady=px(9), padx=(px(16), 0))


def _open_url(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _open_path(path):
    try:
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)                    # noqa: S606 (opens the folder in Explorer)
    except Exception:
        pass
