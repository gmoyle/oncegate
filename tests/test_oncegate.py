"""End-to-end tests: a fake upstream app, OnceGate in front of it, real HTTP."""

import base64
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self):
        body = json.dumps({"method": self.command, "path": self.path,
                           "user": self.headers.get("X-OnceGate-User"),
                           "cookie": self.headers.get("Cookie")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_DELETE = _reply


def start(server_cls, handler):
    srv = server_cls(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class OnceGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.up = start(ThreadingHTTPServer, Upstream)
        pw = os.path.join(cls.tmp.name, "admin-password")
        with open(pw, "w") as f:
            f.write("correct horse\n")
        os.environ.update({
            "ONCEGATE_UPSTREAM": f"http://127.0.0.1:{cls.up.server_port}",
            "ONCEGATE_DATA_DIR": os.path.join(cls.tmp.name, "data"),
            "ONCEGATE_ADMIN_PASSWORD_FILE": pw,
            "ONCEGATE_PUBLIC_URL": "",
            "ONCEGATE_RATE_PER_MIN": "1000",
        })
        import oncegate
        cls.og = oncegate
        oncegate.CFG = oncegate.Config()
        oncegate.STORE = oncegate.Store(oncegate.CFG.data_dir)
        cls.pub = start(ThreadingHTTPServer, oncegate.PublicHandler)
        cls.adm = start(ThreadingHTTPServer, oncegate.AdminHandler)

    @classmethod
    def tearDownClass(cls):
        for s in (cls.up, cls.pub, cls.adm):
            s.shutdown()
        cls.tmp.cleanup()

    # ---- helpers ----
    def req(self, method, path, port=None, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port or self.pub.server_port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def new_code(self, **kw):
        _, code = self.og.STORE.create_invite(kw.get("label", "Lily"), kw.get("hours", 2), "")
        return code

    def redeem(self, code):
        r, _ = self.req("POST", "/_once/" + code)
        self.assertEqual(r.status, 303)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        return cookie.split(";")[0]

    # ---- tests ----
    def test_no_cookie_no_access(self):
        r, body = self.req("GET", "/")
        self.assertEqual(r.status, 401)
        self.assertIn(b"invite link", body)

    def test_preview_does_not_use_link(self):
        code = self.new_code()
        for _ in range(3):   # link previews, mail scanners
            r, body = self.req("GET", "/_once/" + code)
            self.assertEqual(r.status, 200)
            self.assertIn(b"Open the site", body)
            self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        self.redeem(code)

    def test_single_use(self):
        code = self.new_code()
        self.redeem(code)
        r, body = self.req("POST", "/_once/" + code)
        self.assertEqual(r.status, 410)
        self.assertIn(b"already been used", body)
        r, _ = self.req("GET", "/_once/" + code)
        self.assertEqual(r.status, 410)

    def test_concurrent_redeem_only_one_wins(self):
        code = self.new_code()
        results = []

        def go():
            r, _ = self.req("POST", "/_once/" + code)
            results.append(r.status)

        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results.count(303), 1)

    def test_proxy_view_only_and_cookie_stripped(self):
        cookie = self.redeem(self.new_code(label="Juniper"))
        r, body = self.req("GET", "/api/x?y=1", headers={"Cookie": cookie + "; other=1",
                                                         "X-OnceGate-User": "spoofed"})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("X-OnceGate"), "guest")
        d = json.loads(body)
        self.assertEqual(d["path"], "/api/x?y=1")
        self.assertEqual(d["user"], "Juniper")
        self.assertEqual(d["cookie"], "other=1")          # the gate's own cookie never reaches the app
        r, _ = self.req("POST", "/api/x", headers={"Cookie": cookie, "Content-Length": "2"}, body=b"{}")
        self.assertEqual(r.status, 403)
        r, _ = self.req("DELETE", "/api/x", headers={"Cookie": cookie})
        self.assertEqual(r.status, 403)

    def test_bad_and_unknown_codes(self):
        for path in ("/_once/", "/_once/nope", "/_once/" + "x" * 200):
            r, _ = self.req("GET", path)
            self.assertIn(r.status, (401, 404))
        r, _ = self.req("GET", "/", headers={"Cookie": "oncegate=forged"})
        self.assertEqual(r.status, 401)

    def test_revoke_invite_and_session(self):
        code = self.new_code()
        iid, _ = self.og.STORE.find_invite(code)
        self.og.STORE.revoke("invites", iid)
        r, body = self.req("POST", "/_once/" + code)
        self.assertEqual(r.status, 410)
        self.assertIn(b"cancelled", body)
        cookie = self.redeem(self.new_code())
        sid, _ = self.og.STORE.session(cookie.split("=", 1)[1])
        self.og.STORE.revoke("sessions", sid)
        r, _ = self.req("GET", "/", headers={"Cookie": cookie})
        self.assertEqual(r.status, 401)

    def test_session_expires(self):
        cookie = self.redeem(self.new_code())
        sid, s = self.og.STORE.session(cookie.split("=", 1)[1])
        s["expires"] = int(time.time()) - 1
        r, _ = self.req("GET", "/", headers={"Cookie": cookie})
        self.assertEqual(r.status, 401)

    def test_forward_auth_and_logout(self):
        cookie = self.redeem(self.new_code(label="Susanne"))
        r, _ = self.req("GET", "/_once/auth", headers={"Cookie": cookie})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("X-OnceGate-User"), "Susanne")
        r, _ = self.req("GET", "/_once/auth", headers={"Cookie": cookie, "X-Forwarded-Method": "DELETE"})
        self.assertEqual(r.status, 403)                       # view-only in forward-auth mode too
        self.req("GET", "/_once/logout", headers={"Cookie": cookie})
        r, _ = self.req("GET", "/_once/auth", headers={"Cookie": cookie})
        self.assertEqual(r.status, 401)

    def test_link_made_by_another_process_is_seen(self):
        other = self.og.Store(self.og.CFG.data_dir)      # like `oncegate invite` run from the command line
        time.sleep(0.01)
        _, code = other.create_invite("CLI", 1, "")
        self.redeem(code)

    def test_note_shown_to_guest_only_when_chosen(self):
        _, private = self.og.STORE.create_invite("Lily", 2, "for my eyes only")
        r, body = self.req("GET", "/_once/" + private)
        self.assertEqual(r.status, 200)
        self.assertNotIn(b"for my eyes only", body)            # notes are private by default
        _, shared = self.og.STORE.create_invite("Lily", 2, "Photos from <b>the weekend</b>", show_note=True)
        r, body = self.req("GET", "/_once/" + shared)
        self.assertIn(b'class="note"', body)
        self.assertIn(b"Photos from &lt;b&gt;the weekend&lt;/b&gt;", body)   # plain text, never markup
        _, empty = self.og.STORE.create_invite("Lily", 2, "  ", show_note=True)
        r, body = self.req("GET", "/_once/" + empty)
        self.assertNotIn(b'class="note"', body)                 # nothing to show, no empty box

    def test_codes_not_stored_in_clear(self):
        code = self.new_code()
        with open(self.og.STORE.path) as f:
            self.assertNotIn(code, f.read())

    def test_admin_requires_password_and_csrf(self):
        port = self.adm.server_port
        r, body = self.req("GET", "/", port=port)
        self.assertEqual(r.status, 401)
        self.assertIn(b'name="password"', body)                 # a real sign-in form, not a browser prompt
        f = "password=wrong"
        r, body = self.req("POST", "/login", port=port, body=f, headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(f))})
        self.assertIn(b"password didn", body)
        f = "password=correct+horse"
        r, _ = self.req("POST", "/login", port=port, body=f, headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(f))})
        self.assertEqual(r.status, 303)
        admin_cookie = r.getheader("Set-Cookie").split(";")[0]
        self.assertIn("SameSite=Strict", r.getheader("Set-Cookie"))
        r, body = self.req("GET", "/", port=port, headers={"Cookie": admin_cookie})
        self.assertEqual(r.status, 200)
        self.assertIn(b"New single-use link", body)
        bad = "Basic " + base64.b64encode(b"x:wrong").decode()
        r, _ = self.req("GET", "/", port=port, headers={"Authorization": bad})
        self.assertEqual(r.status, 401)
        good = "Basic " + base64.b64encode(b"admin:correct horse").decode()
        r, body = self.req("GET", "/", port=port, headers={"Authorization": good})
        self.assertEqual(r.status, 200)
        csrf = re.search(rb'name="csrf" value="([0-9a-f]+)"', body).group(1).decode()
        form = "label=Lily&hours=3&note=hotel"
        r, _ = self.req("POST", "/invite", port=port, body=form,
                        headers={"Authorization": good, "Content-Type": "application/x-www-form-urlencoded",
                                 "Content-Length": str(len(form))})
        self.assertEqual(r.status, 403)                         # missing csrf
        form += "&csrf=" + csrf
        r, body = self.req("POST", "/invite", port=port, body=form,
                           headers={"Authorization": good, "Content-Type": "application/x-www-form-urlencoded",
                                    "Content-Length": str(len(form)), "Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)                         # cross-site
        r, body = self.req("POST", "/invite", port=port, body=form,
                           headers={"Authorization": good, "Content-Type": "application/x-www-form-urlencoded",
                                    "Content-Length": str(len(form))})
        self.assertEqual(r.status, 200)
        link = re.search(rb"<code id='newlink'>(/_once/[A-Za-z0-9_-]+)</code>", body).group(1).decode()
        self.assertIn(b"id='copylink'", body)                  # a Copy link button next to the new link
        nonce = re.search(r"script-src 'nonce-([^']+)'", r.getheader("Content-Security-Policy")).group(1)
        self.assertIn(('<script nonce="%s">' % nonce).encode(), body)   # only our own script may run
        r, body = self.req("GET", link)
        self.assertEqual(r.status, 200)
        self.assertNotIn(b"hotel", body)                        # note stays private without the tick box
        form = "label=Sam&note=Welcome+home&show_note=1&csrf=" + csrf
        r, body = self.req("POST", "/invite", port=port, body=form,
                           headers={"Authorization": good, "Content-Type": "application/x-www-form-urlencoded",
                                    "Content-Length": str(len(form))})
        link = re.search(rb"<code id='newlink'>(/_once/[A-Za-z0-9_-]+)</code>", body).group(1).decode()
        r, body = self.req("GET", link)
        self.assertIn(b"Welcome home", body)
        r, body = self.req("GET", "/", port=port, headers={"Authorization": good})
        self.assertIn(b"shown to guest", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
