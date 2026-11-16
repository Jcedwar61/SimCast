"""Updates: SimCast checks GitHub for a newer release and can install it for you.

The window asks check_latest_release() when it starts and every 12 hours (Settings > About switches that off). A
newer release shows a bar with Install update: the release's SimCast-Setup.exe is downloaded, checked against the
SHA-256 GitHub publishes for it, and started; the setup wizard closes SimCast and updates it in place. Settings are
kept. Nothing is downloaded or installed until Install update is clicked.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

UPDATE_REPO = "Jcedwar61/SimCast"
SETUP_NAME = "SimCast-Setup.exe"
MIN_SIZE = 500_000                               # a real installer is a few MB; an error page is not
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))    # the folder sim_cast.py is in


def ver_tuple(v):
    """'v1.3.1' -> (1, 3, 1, 0); anything without numbers -> (0, 0, 0, 0)."""
    nums = [int(x) for x in re.findall(r"\d+", str(v or ""))[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


def pick_release(d, current):
    """From GitHub's 'latest release' reply: {"tag", "version", "url", "notes", "installer_url", "sha256"} when
    it's newer than `current` and has a setup wizard to install from, else None."""
    tag = str(d.get("tag_name") or "")
    if not tag or d.get("draft") or d.get("prerelease") or ver_tuple(tag) <= ver_tuple(current):
        return None
    asset = next((a for a in d.get("assets") or [] if str(a.get("name", "")).lower() == SETUP_NAME.lower()), None)
    digest = str((asset or {}).get("digest") or "")
    sha = digest[7:].lower() if digest.lower().startswith("sha256:") else ""
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        sha = ""
    return {"tag": tag, "version": tag.lstrip("vV"),
            "url": d.get("html_url") or f"https://github.com/{UPDATE_REPO}/releases/latest",
            "notes": str(d.get("body") or "")[:2000],
            "installer_url": (asset or {}).get("browser_download_url") or "", "sha256": sha}


def check_latest_release(current, timeout=8.0):
    """The newest published release when it's newer than this copy, else None. Raises on network errors."""
    import urllib.request
    req = urllib.request.Request(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": f"SimCast/{current}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return pick_release(json.loads(r.read().decode("utf-8")), current)


def installed_copy(here=None):
    """True when this is the copy SimCast-Setup.exe installed (so the wizard can update it in place): the folder
    the wizard recorded in Settings > Apps, or the default one. A copy run from a download or a git checkout
    gets a link to the release page instead."""
    here = os.path.normcase(os.path.abspath(here or HERE))
    local = os.environ.get("LOCALAPPDATA") or ""
    if local and here == os.path.normcase(os.path.abspath(os.path.join(local, "Programs", "SimCast"))):
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SimCast") as k:
            loc = winreg.QueryValueEx(k, "InstallLocation")[0]
        return bool(loc) and here == os.path.normcase(os.path.abspath(loc))
    except Exception:
        return False


def can_install(info, here=None):
    """Install in place only on Windows, for an installed copy, when the release has a wizard with a checksum."""
    import sys
    return bool(sys.platform == "win32" and info and info.get("installer_url") and info.get("sha256")
                and installed_copy(here))


def download_installer(url, dest, sha256, progress=None, timeout=30.0, current=""):
    """Download the setup wizard to `dest`. It's kept only if it's a complete Windows program whose SHA-256 is the
    one GitHub published for the release; anything else is deleted and ValueError raised."""
    import urllib.request
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(sha256 or "")):
        raise ValueError("the release has no checksum to check the download against")
    req = urllib.request.Request(url, headers={"User-Agent": f"SimCast/{current}"})
    tmp = dest + ".part"
    got, h = 0, hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as fh:
            total = int(r.headers.get("Content-Length") or 0)
            head = b""
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                if len(head) < 2:
                    head = (head + chunk)[:2]
                fh.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
        if head != b"MZ" or got < MIN_SIZE or (total and got != total):
            raise ValueError("the download wasn't a complete SimCast installer")
        if h.hexdigest() != sha256.lower():
            raise ValueError("the download didn't match the checksum published for the release")
        os.replace(tmp, dest)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return dest


def start_installer(path):
    """Start the setup wizard on its own; it closes SimCast before replacing its files."""
    import subprocess
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([path], close_fds=True, creationflags=flags)
