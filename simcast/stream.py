"""For streamers: captions as an OBS browser source, and the booth reading Twitch chat.

Captions: a small web server on this PC (127.0.0.1 only) serves a page with a transparent background that shows
each line as it's said, with the speaker's name in their colour, and fades it out. In OBS: Sources > + > Browser,
URL http://127.0.0.1:<port>/ (Settings > Stream shows it), width 1600, height 300.

Twitch chat: read-only and anonymous (no login, nothing is ever sent to chat). Messages are handed to the AI
booth, which answers the odd one in a quiet spell ("Kate, someone in chat says you're wrong"). Chat is never read
out word for word: the AI paraphrases, ignores anything abusive or spammy, and never takes instructions from it.
"""
from __future__ import annotations

import json
import random
import re
import socket
import ssl
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COLOURS = {"lead": "#4fc3f7", "analyst": "#ffb74d", "pit": "#81c784"}

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>SimCast captions</title>
<style>
  html, body { margin: 0; background: transparent; overflow: hidden; font-family: "Segoe UI", system-ui, sans-serif; }
  #box { position: absolute; left: 24px; right: 24px; bottom: 24px; display: flex; flex-direction: column; gap: 10px; }
  .ln { display: flex; background: rgba(10, 14, 22, 0.82); border-radius: 10px; overflow: hidden;
        transition: opacity 0.8s ease, transform 0.4s ease; transform: translateY(8px); opacity: 0; }
  .ln.show { opacity: 1; transform: none; }
  .ln.old { opacity: 0; }
  .bar { width: 8px; flex: none; }
  .body { padding: 10px 18px 12px; }
  .who { font-size: 18px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
  .txt { font-size: 32px; line-height: 1.25; color: #f4f6fa; text-shadow: 0 2px 4px rgba(0,0,0,0.6); }
</style></head>
<body><div id="box"></div>
<script>
const box = document.getElementById("box");
const keep = parseInt(new URLSearchParams(location.search).get("lines") || "2", 10);
let seq = -1;
async function poll() {
  try {
    const r = await fetch("/lines?since=" + seq, {cache: "no-store"});
    const d = await r.json();
    for (const ln of d.lines) {
      seq = Math.max(seq, ln.seq);
      const el = document.createElement("div");
      el.className = "ln";
      el.innerHTML = '<div class="bar"></div><div class="body"><div class="who"></div><div class="txt"></div></div>';
      el.querySelector(".bar").style.background = ln.colour;
      el.querySelector(".who").style.color = ln.colour;
      el.querySelector(".who").textContent = ln.name;
      el.querySelector(".txt").textContent = ln.text;
      box.appendChild(el);
      requestAnimationFrame(() => el.classList.add("show"));
      setTimeout(() => el.classList.add("old"), Math.max(4000, ln.text.split(" ").length * 420 + 2500));
      setTimeout(() => el.remove(), Math.max(5000, ln.text.split(" ").length * 420 + 3500));
      while (box.children.length > keep) box.firstChild.remove();
    }
  } catch (e) {}
  setTimeout(poll, 250);
}
poll();
</script></body></html>
"""


class CaptionServer:
    """The OBS browser source. lines_fn() -> the recent lines (dicts with seq, who, name, text)."""

    def __init__(self, lines_fn, port=8686, log=print):
        self.lines_fn, self.port, self.log = lines_fn, int(port), log
        self.httpd = None

    def start(self):
        if self.httpd is not None:
            return True
        lines_fn = self.lines_fn

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body, ctype):
                b = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path.startswith("/lines"):
                    m = re.search(r"since=(-?\d+)", self.path)
                    since = int(m.group(1)) if m else -1
                    out = []
                    for ln in list(lines_fn())[-12:]:
                        if ln.get("seq", -1) > since:
                            out.append({"seq": ln["seq"], "name": ln.get("name", ""), "text": ln.get("text", ""),
                                        "colour": COLOURS.get(ln.get("who"), "#4fc3f7")})
                    if since < 0:
                        out = out[-1:]                     # a fresh page starts with the latest line only
                    self._send(json.dumps({"lines": out}), "application/json")
                elif self.path in ("/", "/index.html") or self.path.startswith("/?"):
                    self._send(PAGE, "text/html; charset=utf-8")
                else:
                    self.send_error(404)
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        except OSError as e:
            self.log(f"Captions for OBS: port {self.port} is in use ({e}). Pick another in Settings > Stream.")
            self.httpd = None
            return False
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="obs-captions").start()
        self.log(f"Captions for OBS: http://127.0.0.1:{self.port}/")
        return True

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None


# ----------------------------------------------------------------------------------------------- Twitch chat
_PRIVMSG = re.compile(r"^(?:@(?P<tags>\S+) )?:(?P<user>[^!\s]+)![^ ]+ PRIVMSG #(?P<chan>\S+) :(?P<text>.*)$")
_LINK = re.compile(r"(https?://|www\.|\.\w{2,4}/)", re.I)


def clean_channel(name):
    """'https://twitch.tv/SomeOne' or '#someone' -> 'someone'."""
    n = str(name or "").strip().rstrip("/").split("/")[-1].lstrip("#").lower()
    return n if re.fullmatch(r"[a-z0-9_]{3,25}", n) else ""


def usable(text, only_prefix=""):
    """A chat message worth handing to the booth: some words, no links, not a command, not shouting spam."""
    t = " ".join(str(text or "").split())
    if only_prefix:
        if not t.lower().startswith(only_prefix.lower()):
            return ""
        t = t[len(only_prefix):].strip()
    if not 3 <= len(t) <= 200 or _LINK.search(t) or (t.startswith("!") and not only_prefix):
        return ""
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 3 or (len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > 0.8):
        return ""
    return t


class TwitchChat:
    """Anonymous, read-only Twitch IRC. messages: the recent usable ones as (user, text, time)."""

    HOST, PORT = "irc.chat.twitch.tv", 6697

    def __init__(self, channel_fn, prefix_fn=lambda: "", log=print, allow_fn=None):
        self.channel_fn, self.prefix_fn, self.log = channel_fn, prefix_fn, log
        self.allow_fn = allow_fn                 # assist.Moderator.allow: an AI safety check before the booth sees it
        self.messages: deque = deque(maxlen=30)
        self.used: set = set()
        self._stop = threading.Event()
        self._thread = None
        self.connected_to = ""

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="twitch-chat")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def feed(self, raw_line, now=None):
        """One IRC line in (also used by the tests). Returns the message kept, or None."""
        m = _PRIVMSG.match(raw_line.rstrip("\r\n"))
        if not m:
            return None
        text = usable(m.group("text"), self.prefix_fn() or "")
        if not text:
            return None
        if self.allow_fn is not None and not self.allow_fn(text):
            return None
        msg = (m.group("user"), text, time.monotonic() if now is None else now)
        self.messages.append(msg)
        return msg

    def fresh(self, now=None, max_age=120.0):
        """Unanswered messages from the last couple of minutes, newest first."""
        now = time.monotonic() if now is None else now
        return [m for m in reversed(self.messages) if m not in self.used and now - m[2] <= max_age]

    def take(self, n=3, now=None):
        """A few messages for the booth to pick from: questions and ones that name the booth first."""
        msgs = self.fresh(now)
        msgs.sort(key=lambda m: (0 if "?" in m[1] else 1, -m[2]))
        out = msgs[:n]
        self.used.update(out)
        return out

    def _run(self):
        delay = 2.0
        while not self._stop.is_set():
            chan = clean_channel(self.channel_fn())
            if not chan:
                self.connected_to = ""
                time.sleep(2.0)
                continue
            try:
                self._session(chan)
                delay = 2.0
            except Exception as e:
                self.connected_to = ""
                self.log(f"Twitch chat: {type(e).__name__}: {e}; trying again in {int(delay)} s")
                time.sleep(delay)
                delay = min(60.0, delay * 2)

    def _session(self, chan):
        raw = socket.create_connection((self.HOST, self.PORT), timeout=15)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.HOST)
        sock.settimeout(1.0)
        nick = f"justinfan{random.randint(10000, 99999)}"       # Twitch's anonymous read-only login
        sock.sendall(f"NICK {nick}\r\nJOIN #{chan}\r\n".encode())
        self.connected_to = chan
        self.log(f"Twitch chat: reading #{chan}")
        buf = b""
        last = time.monotonic()
        try:
            while not self._stop.is_set():
                if clean_channel(self.channel_fn()) != chan:
                    return                                       # the channel was changed in Settings
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    if time.monotonic() - last > 360:
                        raise ConnectionError("no word from Twitch for six minutes")
                    continue
                if not data:
                    raise ConnectionError("Twitch closed the connection")
                last = time.monotonic()
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    s = line.decode("utf-8", "replace")
                    if s.startswith("PING"):
                        sock.sendall(("PONG" + s[4:] + "\r\n").encode())
                    else:
                        self.feed(s)
        finally:
            self.connected_to = ""
            try:
                sock.close()
            except Exception:
                pass
