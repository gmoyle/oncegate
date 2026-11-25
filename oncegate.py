#!/usr/bin/env python3
"""OnceGate: single-use invite links in front of any web app.

Give someone a link. The first time they open it (and tap "Open"), the link is
used up and their browser gets a session for a few hours. Anyone who tries the
link afterwards gets "already used". Everyone else gets nothing at all.

    public port  -> invite pages + reverse proxy to UPSTREAM for valid sessions
    admin port   -> create / revoke invites and sessions (LAN only, password)
    command line -> python oncegate.py invite --label Lily --hours 4

Standard library only. Configuration is by environment variable; see README.md.
"""

import argparse
import base64
import hashlib
import hmac
import html
import http.client
import ipaddress
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.1.0"

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------


def _env(name, default=None):
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _env_int(name, default):
    try:
        return int(_env(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    def __init__(self):
        self.upstream = _env("ONCEGATE_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
        self.public_url = _env("ONCEGATE_PUBLIC_URL", "").rstrip("/")
        self.listen = _env("ONCEGATE_LISTEN", "0.0.0.0")
        self.port = _env_int("ONCEGATE_PORT", 9099)
        self.admin_listen = _env("ONCEGATE_ADMIN_LISTEN", "0.0.0.0")
        self.admin_port = _env_int("ONCEGATE_ADMIN_PORT", 9098)
        self.data_dir = _env("ONCEGATE_DATA_DIR", "/data")
        self.admin_password_file = _env("ONCEGATE_ADMIN_PASSWORD_FILE", "/secrets/admin-password")
        self.admin_allow = [ipaddress.ip_network(n.strip(), strict=False) for n in _env(
            "ONCEGATE_ADMIN_ALLOW",
            "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,fd7a:115c:a1e0::/48",
        ).split(",") if n.strip()]
        self.session_hours = float(_env("ONCEGATE_SESSION_HOURS", "4"))
        self.max_session_hours = float(_env("ONCEGATE_MAX_SESSION_HOURS", "168"))
        self.invite_days = float(_env("ONCEGATE_INVITE_DAYS", "0"))   # 0 = unused invites never expire
        self.guest_methods = {m.strip().upper() for m in _env("ONCEGATE_GUEST_METHODS", "GET,HEAD").split(",") if m.strip()}
        self.trust_proxy = _env("ONCEGATE_TRUST_PROXY", "1") not in ("0", "false", "no")
        self.title = _env("ONCEGATE_TITLE", "Private site")
        self.cookie_name = _env("ONCEGATE_COOKIE", "oncegate")
        self.max_body = _env_int("ONCEGATE_MAX_BODY", 10 * 1024 * 1024)
        self.rate_per_min = _env_int("ONCEGATE_RATE_PER_MIN", 30)

    @property
    def secure_cookie(self):
        return self.public_url.startswith("https://")


CFG = Config()

# ----------------------------------------------------------------------------
# Storage: one JSON file, written atomically. Only hashes of codes are kept.
# ----------------------------------------------------------------------------

_lock = threading.RLock()


def _now():
    return int(time.time())


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token():
    return secrets.token_urlsafe(32)


def _new_id():
    return secrets.token_hex(6)


class Store:
    def __init__(self, data_dir):
        self.dir = data_dir
        self.path = os.path.join(data_dir, "oncegate.json")
        self.audit_path = os.path.join(data_dir, "audit.log")
        os.makedirs(data_dir, exist_ok=True)
        self.data = {"invites": {}, "sessions": {}, "secret": None}
        self._mtime = None
        self._load()
        if not self.data.get("secret"):
            self.data["secret"] = secrets.token_hex(32)
            self._save()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update(d)
            self._mtime = os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            pass

    def refresh(self):
        """Pick up changes another process (the command line) made to the file."""
        with _lock:
            try:
                m = os.stat(self.path).st_mtime_ns
            except FileNotFoundError:
                return
            if m != self._mtime:
                self._load()

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self._mtime = os.stat(self.path).st_mtime_ns
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def audit(self, event, **kw):
        line = json.dumps(dict(t=_now(), event=event, **kw), sort_keys=True)
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    @property
    def secret(self):
        return self.data["secret"]

    # ---- invites ----
    def create_invite(self, label, session_hours=None, note="", invite_days=None):
        label = (label or "Guest").strip()[:60] or "Guest"
        note = (note or "").strip()[:200]
        hours = CFG.session_hours if session_hours in (None, "") else float(session_hours)
        hours = max(0.05, min(hours, CFG.max_session_hours))
        days = CFG.invite_days if invite_days in (None, "") else float(invite_days)
        code = _new_token()
        iid = _new_id()
        self.refresh()
        with _lock:
            self.data["invites"][iid] = {
                "hash": _hash(code), "label": label, "note": note, "sessionHours": hours,
                "created": _now(), "expires": (_now() + int(days * 86400)) if days > 0 else None,
                "usedAt": None, "usedIp": None, "revoked": False,
            }
            self._save()
        self.audit("invite_created", id=iid, label=label, hours=hours)
        return iid, code

    def find_invite(self, code):
        h = _hash(code)
        self.refresh()
        with _lock:
            for iid, inv in self.data["invites"].items():
                if hmac.compare_digest(inv["hash"], h):
                    return iid, inv
        return None, None

    def invite_state(self, inv):
        if inv is None:
            return "unknown"
        if inv.get("revoked"):
            return "revoked"
        if inv.get("usedAt"):
            return "used"
        if inv.get("expires") and _now() > inv["expires"]:
            return "expired"
        return "ready"

    def redeem(self, code, ip, ua):
        """Atomically use up an invite and start a session. Returns (session_token, invite) or (None, state)."""
        with _lock:
            iid, inv = self.find_invite(code)
            state = self.invite_state(inv)
            if state != "ready":
                return None, state
            inv["usedAt"] = _now()
            inv["usedIp"] = ip
            token = _new_token()
            sid = _new_id()
            self.data["sessions"][sid] = {
                "hash": _hash(token), "label": inv["label"], "invite": iid, "created": _now(),
                "expires": _now() + int(inv["sessionHours"] * 3600), "ip": ip, "ua": (ua or "")[:160],
                "revoked": False,
            }
            self._save()
        self.audit("invite_redeemed", id=iid, session=sid, label=inv["label"], ip=ip)
        return token, inv

    # ---- sessions ----
    def session(self, token):
        if not token:
            return None, None
        h = _hash(token)
        self.refresh()
        with _lock:
            for sid, s in self.data["sessions"].items():
                if hmac.compare_digest(s["hash"], h):
                    if s.get("revoked") or _now() > s["expires"]:
                        return None, None
                    return sid, s
        return None, None

    def revoke(self, kind, rid):
        self.refresh()
        with _lock:
            item = self.data[kind].get(rid)
            if not item:
                return False
            item["revoked"] = True
            self._save()
        self.audit(kind[:-1] + "_revoked", id=rid)
        return True

    def end_session(self, token):
        sid, s = self.session(token)
        if sid:
            self.revoke("sessions", sid)

    def purge(self):
        cutoff = _now() - 30 * 86400
        self.refresh()
        with _lock:
            before = (len(self.data["invites"]), len(self.data["sessions"]))
            self.data["sessions"] = {k: v for k, v in self.data["sessions"].items() if v["expires"] > cutoff}
            self.data["invites"] = {k: v for k, v in self.data["invites"].items()
                                    if self.invite_state(v) == "ready" or (v.get("usedAt") or v["created"]) > cutoff}
            if before != (len(self.data["invites"]), len(self.data["sessions"])):
                self._save()


STORE = None  # created in main()

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "proxy-connection"}

_rate = {}
_rate_lock = threading.Lock()


def rate_ok(ip, limit):
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate.get(ip, []) if now - t < 60]
        hits.append(now)
        _rate[ip] = hits
        if len(_rate) > 5000:
            _rate.clear()
        return len(hits) <= limit


def esc(s):
    return html.escape(str(s), quote=True)


def fmt_time(t):
    if not t:
        return "—"
    lt = time.localtime(t)
    return time.strftime("%Y-%m-%d %H:%M ", lt) + (time.strftime("%Z", lt) or "UTC")


def fmt_duration(hours):
    mins = round(hours * 60)
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    if mins % 60 == 0:
        h = mins // 60
        return f"{h} hour{'s' if h != 1 else ''}"
    return f"{hours:g} hours"


def fmt_left(t):
    s = t - _now()
    if s <= 0:
        return "ended"
    h, m = divmod(s // 60, 60)
    return f"{h}h {m}m left" if h else f"{m}m left"


def page(title, body, status_word=""):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{esc(title)}</title>
<style>
:root{{--bg:#0f1a2c;--card:#17243a;--ink:#eef3fa;--muted:#9fb0cb;--accent:#f3b552;--ok:#6fd49c;--bad:#ff8a80;--line:#2a3d5c;color-scheme:dark}}
@media (prefers-color-scheme: light){{:root{{--bg:#eef1f6;--card:#fff;--ink:#162032;--muted:#5a6578;--accent:#b86e00;--ok:#1d7a4a;--bad:#c23b3b;--line:#dbe1ea;color-scheme:light}}}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px max(16px,env(safe-area-inset-left))}}
.card{{width:min(440px,100%);background:var(--card);border:1px solid var(--line);border-radius:18px;padding:28px;display:grid;gap:14px}}
h1{{margin:0;font-size:1.6rem;line-height:1.2}}p{{margin:0;color:var(--muted)}}
button,.btn{{font:600 1rem system-ui,sans-serif;border:0;border-radius:12px;padding:13px 18px;background:var(--accent);color:#1b1407;cursor:pointer;width:100%}}
.tag{{display:inline-block;font-size:.75rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}}
.bad{{color:var(--bad)}}small{{color:var(--muted)}}
</style></head><body><main class="card">{body}</main></body></html>"""


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}

# ----------------------------------------------------------------------------
# Public side: invite pages + proxy
# ----------------------------------------------------------------------------


class PublicHandler(BaseHTTPRequestHandler):
    server_version = "OnceGate/" + __version__
    protocol_version = "HTTP/1.0"   # one request per connection keeps proxying simple

    def log_message(self, fmt, *args):
        pass  # the audit log records what matters; access logs would capture invite codes

    # ---- utils ----
    def client_ip(self):
        ip = self.client_address[0]
        if CFG.trust_proxy:
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                ip = xff.split(",")[0].strip()
        return ip

    def cookie(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == self.cookie_full_name():
                return v
        return None

    @staticmethod
    def cookie_full_name():
        return ("__Host-" if CFG.secure_cookie else "") + CFG.cookie_name

    def send_page(self, status, title, body, extra_headers=None):
        data = page(title, body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def no_access(self):
        self.send_page(401, CFG.title,
                       f'<span class="tag">{esc(CFG.title)}</span><h1>You need an invite link</h1>'
                       '<p>This site is private. Ask the person who runs it to send you a link.</p>')

    # ---- routing ----
    def route(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/_once/"):
            return self.gate(path[len("/_once/"):])
        sid, sess = STORE.session(self.cookie())
        if not sid:
            return self.no_access()
        if self.command not in CFG.guest_methods:
            body = json.dumps({"error": "Guests can only view this site"}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-OnceGate", "guest")
            self.end_headers()
            self.wfile.write(body)
            return
        return self.proxy(sess)

    def do_GET(self):
        self.route()

    def do_HEAD(self):
        self.route()

    def do_POST(self):
        self.route()

    def do_PUT(self):
        self.route()

    def do_PATCH(self):
        self.route()

    def do_DELETE(self):
        self.route()

    def do_OPTIONS(self):
        self.route()

    # ---- invite pages ----
    def gate(self, rest):
        ip = self.client_ip()
        if rest == "auth":                      # forward-auth endpoint for Caddy / Traefik / nginx
            sid, sess = STORE.session(self.cookie())
            # Proxies pass the original method in X-Forwarded-Method; keep guests view-only here too.
            method = (self.headers.get("X-Forwarded-Method") or "GET").upper()
            status = 401 if not sid else (200 if method in CFG.guest_methods else 403)
            self.send_response(status)
            if sid:
                self.send_header("X-OnceGate-User", sess["label"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if rest == "logout":
            STORE.end_session(self.cookie())
            return self.send_page(200, "Signed out", "<h1>You're signed out</h1><p>You can close this page.</p>",
                                  {"Set-Cookie": self.clear_cookie()})
        if not rate_ok(ip, CFG.rate_per_min):
            return self.send_page(429, "Slow down", "<h1>Too many tries</h1><p>Wait a minute and try again.</p>")
        code = rest.strip("/")
        if not code or len(code) > 100:
            return self.no_access()
        if self.command == "GET" or self.command == "HEAD":
            # Showing the page does NOT use up the link. Message apps and mail scanners
            # open links to make previews; only the button press counts.
            _, inv = STORE.find_invite(code)
            state = STORE.invite_state(inv)
            if state != "ready":
                return self.dead_link(state)
            dur = fmt_duration(inv["sessionHours"])
            return self.send_page(200, CFG.title, f"""
<span class="tag">{esc(CFG.title)}</span>
<h1>Hi {esc(inv['label'])}</h1>
<p>This link works once. Tap the button to open the site on this device for {esc(dur)}.</p>
<form method="post"><button type="submit">Open the site</button></form>
<small>Don't forward this link; once it's used it stops working.</small>""")
        if self.command == "POST":
            token, inv = STORE.redeem(code, ip, self.headers.get("User-Agent"))
            if not token:
                return self.dead_link(inv)
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", self.make_cookie(token, int(inv["sessionHours"] * 3600)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self.no_access()

    def dead_link(self, state):
        msg = {
            "used": ("This link has already been used", "Each link works once. Ask for a new one if you need to get back in."),
            "expired": ("This link has expired", "Ask for a new one."),
            "revoked": ("This link was cancelled", "Ask for a new one."),
        }.get(state, ("This link isn't valid", "Check you copied the whole link, or ask for a new one."))
        self.send_page(410 if state in ("used", "expired", "revoked") else 404, msg[0],
                       f'<span class="tag">{esc(CFG.title)}</span><h1 class="bad">{esc(msg[0])}</h1><p>{esc(msg[1])}</p>')

    def make_cookie(self, token, max_age):
        parts = [f"{self.cookie_full_name()}={token}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax"]
        if CFG.secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    def clear_cookie(self):
        parts = [f"{self.cookie_full_name()}=", "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax"]
        if CFG.secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    # ---- proxy ----
    def proxy(self, sess):
        up = urllib.parse.urlsplit(CFG.upstream)
        conn_cls = http.client.HTTPSConnection if up.scheme == "https" else http.client.HTTPConnection
        length = int(self.headers.get("Content-Length") or 0)
        if length > CFG.max_body:
            return self.send_page(413, "Too large", "<h1>Request too large</h1>")
        body = self.rfile.read(length) if length else None
        headers = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in HOP_BY_HOP or lk in ("host", "content-length", "x-onceGate-user".lower()):
                continue
            if lk == "cookie":
                kept = [c for c in v.split(";") if c.strip().partition("=")[0] != self.cookie_full_name()]
                if not kept:
                    continue
                v = ";".join(kept).strip()
            headers[k] = v
        headers["Host"] = up.netloc
        headers["X-Forwarded-For"] = self.client_ip()
        headers["X-Forwarded-Proto"] = "https" if CFG.secure_cookie else "http"
        headers["X-OnceGate-User"] = sess["label"]
        if body is not None:
            headers["Content-Length"] = str(len(body))
        target = (up.path.rstrip("/") + self.path) if up.path not in ("", "/") else self.path
        try:
            conn = conn_cls(up.hostname, up.port, timeout=30)
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError:
            return self.send_page(502, "Unavailable", "<h1>The site isn't answering</h1><p>Try again in a minute.</p>")
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() in HOP_BY_HOP or k.lower() == "content-length":
                continue
            self.send_header(k, v)
        cl = resp.getheader("Content-Length")
        if cl:
            self.send_header("Content-Length", cl)
        self.send_header("X-OnceGate", "guest")
        self.end_headers()
        if self.command != "HEAD":
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except OSError:
                    break
        conn.close()


# ----------------------------------------------------------------------------
# Admin side
# ----------------------------------------------------------------------------


def admin_password():
    try:
        with open(CFG.admin_password_file, "r", encoding="utf-8-sig") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        return lines[0] if len(lines) == 1 and len(lines[0]) >= 8 else None
    except OSError:
        return None


def invite_url(code):
    base = CFG.public_url or ""
    return f"{base}/_once/{code}"


ADMIN_CSS = """
:root{--bg:#eef1f6;--card:#fff;--ink:#162032;--muted:#5a6578;--line:#dbe1ea;--accent:#2b5fb0;--ok:#1d7a4a;--warn:#a15c00;--bad:#c23b3b;--soft:#f5f7fb}
@media (prefers-color-scheme: dark){:root{--bg:#0d141f;--card:#151f2e;--ink:#e6ecf5;--muted:#9aa7bb;--line:#26344a;--accent:#7fa9f2;--ok:#6fd49c;--warn:#f0b860;--bad:#ff8a80;--soft:#1a2637;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:24px max(16px,env(safe-area-inset-left))}
h1{margin:0 0 4px;font-size:1.6rem}h2{font-size:1.1rem;margin:0 0 10px}p.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px;overflow-x:auto}
form.new{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));align-items:end}
label{display:grid;gap:4px;font-size:.8rem;color:var(--muted);font-weight:600}
input{font:inherit;color:var(--ink);background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:0}
button{font:600 .9rem system-ui,sans-serif;border:0;border-radius:9px;padding:9px 14px;background:var(--accent);color:#fff;cursor:pointer}
button.x{background:transparent;color:var(--bad);border:1px solid var(--line);padding:4px 10px;font-size:.8rem}
table{border-collapse:collapse;width:100%;font-size:.88rem}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.pill{font-size:.72rem;font-weight:700;padding:2px 8px;border-radius:99px;background:var(--soft)}
.ready{color:var(--ok)}.used,.expired,.revoked{color:var(--muted)}.live{color:var(--ok)}
.new-link{background:var(--soft);border:2px dashed var(--accent);border-radius:12px;padding:14px;display:grid;gap:8px}
.new-link code{font-size:.95rem;word-break:break-all;user-select:all}
.empty{color:var(--muted);font-style:italic}
"""


_admin_sessions = {}          # sha256(token) -> expiry; in memory, so a restart signs the admin out
_admin_lock = threading.Lock()
ADMIN_COOKIE = "oncegate_admin"
ADMIN_SESSION_SECONDS = 12 * 3600


class AdminHandler(BaseHTTPRequestHandler):
    server_version = "OnceGate-Admin/" + __version__

    def log_message(self, fmt, *args):
        pass

    def allowed(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return any(ip in n for n in CFG.admin_allow)

    def admin_cookie(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == ADMIN_COOKIE:
                return v
        return None

    def authed(self):
        pw = admin_password()
        if not pw:
            return None
        tok = self.admin_cookie()
        if tok:
            h = _hash(tok)
            with _admin_lock:
                exp = _admin_sessions.get(h)
                if exp and exp > _now():
                    return True
        hdr = self.headers.get("Authorization") or ""       # Basic auth still works for scripts
        if not hdr.startswith("Basic "):
            return False
        try:
            user, _, given = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(given.encode(), pw.encode())

    def csrf_token(self):
        return hmac.new(STORE.secret.encode(), b"admin-csrf", hashlib.sha256).hexdigest()[:32]

    def send_html(self, status, body):
        data = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OnceGate</title><style>{ADMIN_CSS}</style></head><body><div class="wrap">{body}</div></body></html>""".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in SECURITY_HEADERS.items():
            # "no-referrer" makes browsers send Origin: null on form posts, which the
            # cross-site check would reject; same-origin keeps the admin's own origin visible.
            self.send_header(k, "same-origin" if k == "Referrer-Policy" else v)
        self.end_headers()
        self.wfile.write(data)

    def gatekeep(self):
        if not self.allowed():
            self.send_html(403, "<h1>Not from here</h1><p class='sub'>The admin page only answers on your home network.</p>")
            return False
        ok = self.authed()
        if ok is None:
            self.send_html(503, f"<h1>Set an admin password first</h1><p class='sub'>Put one line, at least 8 characters, in "
                                f"<code>{esc(CFG.admin_password_file)}</code>, then reload this page.</p>")
            return False
        if not ok:
            self.login_page()
            return False
        return True

    def login_page(self, error=""):
        err = f"<p style='color:var(--bad)'>{esc(error)}</p>" if error else ""
        self.send_html(401, f"""<div class="card" style="max-width:380px;margin:40px auto"><h1>OnceGate</h1>
<p class="sub">Sign in to make single-use links.</p>{err}
<form method="post" action="/login" style="display:grid;gap:10px">
<label>Admin password<input type="password" name="password" autocomplete="current-password" autofocus required></label>
<button type="submit">Sign in</button></form></div>""")

    def handle_login(self):
        if not rate_ok("admin:" + self.client_address[0], 10):
            return self.login_page("Too many tries. Wait a minute.")
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(min(length, 4096)).decode("utf-8", "replace"))
        given = (form.get("password") or [""])[0]
        pw = admin_password()
        if not pw or not hmac.compare_digest(given.encode(), pw.encode()):
            STORE.audit("admin_login_failed", ip=self.client_address[0])
            return self.login_page("That password didn't match.")
        tok = _new_token()
        with _admin_lock:
            now = _now()
            for k in [k for k, v in _admin_sessions.items() if v <= now]:
                del _admin_sessions[k]
            _admin_sessions[_hash(tok)] = now + ADMIN_SESSION_SECONDS
        STORE.audit("admin_login", ip=self.client_address[0])
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", f"{ADMIN_COOKIE}={tok}; Path=/; Max-Age={ADMIN_SESSION_SECONDS}; HttpOnly; SameSite=Strict")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self.gatekeep():
            return
        if urllib.parse.urlsplit(self.path).path != "/":
            return self.send_html(404, "<h1>Not found</h1>")
        self.render()

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path == "/login" and self.allowed():
            if admin_password() is None:
                return self.gatekeep()
            return self.handle_login()
        if not self.gatekeep():
            return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host") or ""
        if origin and urllib.parse.urlsplit(origin).netloc != host:
            return self.send_html(403, "<h1>Blocked</h1><p class='sub'>That request came from another site.</p>")
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(min(length, 8192)).decode("utf-8", "replace"))
        f = {k: v[0] for k, v in form.items()}
        if not hmac.compare_digest(f.get("csrf", ""), self.csrf_token()):
            return self.send_html(403, "<h1>Blocked</h1><p class='sub'>The form was out of date. Reload and try again.</p>")
        path = urllib.parse.urlsplit(self.path).path
        if path == "/invite":
            try:
                iid, code = STORE.create_invite(f.get("label"), f.get("hours") or None, f.get("note"))
            except ValueError:
                return self.render(error="Session length must be a number of hours.")
            return self.render(new=(f.get("label") or "Guest", invite_url(code)))
        if path == "/revoke":
            STORE.revoke("invites" if f.get("kind") == "invite" else "sessions", f.get("id", ""))
        if path == "/logout":
            tok = self.admin_cookie()
            if tok:
                with _admin_lock:
                    _admin_sessions.pop(_hash(tok), None)
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"{ADMIN_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def render(self, new=None, error=None):
        STORE.purge()
        csrf = self.csrf_token()
        out = [f"<h1>OnceGate</h1><p class='sub'>Single-use links to <code>{esc(CFG.upstream)}</code>"
               f"{' at <code>' + esc(CFG.public_url) + '</code>' if CFG.public_url else ''}.</p>"]
        if not CFG.public_url:
            out.append("<div class='card'><b>Public address not set.</b> Set <code>ONCEGATE_PUBLIC_URL</code> so new links include it.</div>")
        if error:
            out.append(f"<div class='card' style='color:var(--bad)'>{esc(error)}</div>")
        if new:
            out.append(f"<div class='card new-link'><b>Link for {esc(new[0])}, works once:</b><code>{esc(new[1])}</code>"
                       "<small>Copy it now. For safety only a fingerprint of it is stored, so it can't be shown again.</small></div>")
        out.append(f"""<div class="card"><h2>New single-use link</h2>
<form class="new" method="post" action="/invite"><input type="hidden" name="csrf" value="{csrf}">
<label>Who is it for<input name="label" maxlength="60" placeholder="e.g. Lily" required></label>
<label>Session length (hours)<input name="hours" inputmode="decimal" placeholder="{CFG.session_hours:g}"></label>
<label>Note<input name="note" maxlength="200" placeholder="optional"></label>
<button type="submit">Create link</button></form></div>""")
        with _lock:
            invites = sorted(STORE.data["invites"].items(), key=lambda kv: -kv[1]["created"])
            sessions = sorted(STORE.data["sessions"].items(), key=lambda kv: -kv[1]["created"])
        rows = []
        for iid, inv in invites:
            st = STORE.invite_state(inv)
            btn = (f"<form method='post' action='/revoke'><input type='hidden' name='csrf' value='{csrf}'>"
                   f"<input type='hidden' name='kind' value='invite'><input type='hidden' name='id' value='{esc(iid)}'>"
                   "<button class='x'>Cancel</button></form>") if st == "ready" else ""
            rows.append(f"<tr><td><b>{esc(inv['label'])}</b><br><small>{esc(inv.get('note') or '')}</small></td>"
                        f"<td><span class='pill {st}'>{st}</span></td><td>{fmt_time(inv['created'])}</td>"
                        f"<td>{fmt_time(inv.get('usedAt'))}<br><small>{esc(inv.get('usedIp') or '')}</small></td>"
                        f"<td>{inv['sessionHours']:g} h</td><td>{btn}</td></tr>")
        out.append("<div class='card'><h2>Links</h2>" + (
            "<table><tr><th>For</th><th>State</th><th>Created</th><th>Used</th><th>Session</th><th></th></tr>" + "".join(rows) + "</table>"
            if rows else "<p class='empty'>No links yet.</p>") + "</div>")
        srows = []
        for sid, s in sessions:
            live = not s.get("revoked") and s["expires"] > _now()
            btn = (f"<form method='post' action='/revoke'><input type='hidden' name='csrf' value='{csrf}'>"
                   f"<input type='hidden' name='kind' value='session'><input type='hidden' name='id' value='{esc(sid)}'>"
                   "<button class='x'>End now</button></form>") if live else ""
            state = "live" if live else ("ended" if not s.get("revoked") else "revoked")
            srows.append(f"<tr><td><b>{esc(s['label'])}</b><br><small>{esc(s.get('ua') or '')[:80]}</small></td>"
                         f"<td><span class='pill {'live' if live else 'used'}'>{state}</span></td><td>{fmt_time(s['created'])}</td>"
                         f"<td>{fmt_left(s['expires']) if live else fmt_time(s['expires'])}</td><td>{esc(s.get('ip') or '')}</td><td>{btn}</td></tr>")
        out.append("<div class='card'><h2>Sessions</h2>" + (
            "<table><tr><th>Who</th><th>State</th><th>Started</th><th>Ends</th><th>From</th><th></th></tr>" + "".join(srows) + "</table>"
            if srows else "<p class='empty'>Nobody has used a link yet.</p>") + "</div>")
        out.append(f"<form method='post' action='/logout'><input type='hidden' name='csrf' value='{csrf}'>"
                   f"<p class='sub'>OnceGate {__version__} · <button class='x' style='color:var(--muted)'>Sign out</button></p></form>")
        self.send_html(200, "".join(out))


# ----------------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------------


def serve():
    pub = ThreadingHTTPServer((CFG.listen, CFG.port), PublicHandler)
    adm = ThreadingHTTPServer((CFG.admin_listen, CFG.admin_port), AdminHandler)
    pub.daemon_threads = adm.daemon_threads = True
    threading.Thread(target=adm.serve_forever, daemon=True).start()
    print(f"OnceGate {__version__}: public :{CFG.port} -> {CFG.upstream}; admin :{CFG.admin_port}; data {CFG.data_dir}", flush=True)
    if not admin_password():
        print(f"Admin page is locked until {CFG.admin_password_file} holds a password (one line, 8+ characters).", flush=True)
    pub.serve_forever()


def cli(argv):
    ap = argparse.ArgumentParser(prog="oncegate", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the gate (default)")
    inv = sub.add_parser("invite", help="create a single-use link and print it")
    inv.add_argument("--label", default="Guest")
    inv.add_argument("--hours", type=float)
    inv.add_argument("--note", default="")
    sub.add_parser("list", help="list links and sessions")
    rv = sub.add_parser("revoke", help="cancel a link or end a session by id")
    rv.add_argument("id")
    a = ap.parse_args(argv)
    global STORE
    STORE = Store(CFG.data_dir)
    if a.cmd in (None, "serve"):
        return serve()
    if a.cmd == "invite":
        _, code = STORE.create_invite(a.label, a.hours, a.note)
        print(invite_url(code))
        return 0
    if a.cmd == "list":
        for iid, inv in STORE.data["invites"].items():
            print(f"link    {iid}  {STORE.invite_state(inv):8} {inv['label']}")
        for sid, s in STORE.data["sessions"].items():
            live = not s.get("revoked") and s["expires"] > _now()
            print(f"session {sid}  {'live' if live else 'ended':8} {s['label']}  {fmt_left(s['expires']) if live else ''}")
        return 0
    if a.cmd == "revoke":
        ok = STORE.revoke("invites", a.id) or STORE.revoke("sessions", a.id)
        print("revoked" if ok else "no such id")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]) or 0)
