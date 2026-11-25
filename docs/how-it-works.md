# How OnceGate works

## The life of a link

```
You (admin page or CLI)            Guest's phone                     OnceGate                          Your app
─────────────────────              ─────────────                     ────────                          ────────
Create link "Lily, 4 h"  ───────────────────────────────────────▶  store sha256(code), state=ready
copy https://…/_once/CODE
send by text  ──────────▶  Messages builds a preview
                           GET /_once/CODE  ─────────────────────▶  ready → show confirm page
                                                                   (nothing used up)
                           Lily taps the link
                           GET /_once/CODE  ─────────────────────▶  ready → show confirm page
                           taps "Open the site"
                           POST /_once/CODE ─────────────────────▶  lock; still ready?
                                                                     mark used, create session
                           ◀──── 303 to /, Set-Cookie: __Host-oncegate=TOKEN (HttpOnly, Secure, 4 h)
                           GET /  (cookie) ──────────────────────▶  session valid, method allowed
                                                                     strip our cookie, add
                                                                     X-OnceGate-User: Lily  ─────▶  page
                           ◀──────────────────────────── page + X-OnceGate: guest ◀────────────────
Someone else, later        POST /_once/CODE ─────────────────────▶  used → "already been used" (410)
4 hours on                 GET / (cookie) ───────────────────────▶  session expired → "You need an invite link" (401)
```

## Why a confirm button

Chat apps, email security scanners and link previewers fetch every URL they see, often before the
person does. If merely opening the link used it up, the preview bot would get the session and Lily
would get *"already used"*. So `GET /_once/<code>` never changes anything. Only the `POST` from the
button does, and bots don't press buttons.

## What's stored

`data/oncegate.json`, written atomically (a temporary file, then a rename) with mode 600:

```json
{
  "secret": "…random, signs admin form tokens…",
  "invites": {
    "1a2b3c4d5e6f": {
      "hash": "sha256 of the code", "label": "Lily", "note": "hotel wifi",
      "sessionHours": 4, "created": 1795567000, "expires": null,
      "usedAt": 1795567600, "usedIp": "203.0.113.7", "revoked": false
    }
  },
  "sessions": {
    "9f8e7d6c5b4a": {
      "hash": "sha256 of the cookie token", "label": "Lily", "invite": "1a2b3c4d5e6f",
      "created": 1795567600, "expires": 1795582000, "ip": "203.0.113.7",
      "ua": "Mozilla/5.0 (iPhone…", "revoked": false
    }
  }
}
```

The code in the link and the token in the cookie are never written down. Knowing the stored hashes
doesn't let anyone get in, because a hash can't be turned back into the code.

The server keeps the file in memory and reloads it whenever another process (the `invite`, `list`
or `revoke` commands) changes it.

## Two modes

**Proxy mode** (the default): OnceGate sits in front of your app. For each request it checks the
session, enforces view-only, removes its own cookie and any spoofed `X-OnceGate-User`, adds
`X-OnceGate-User` and `X-Forwarded-For` / `-Proto`, passes the request on, and streams the reply
back with `X-OnceGate: guest` added.

**Forward-auth mode:** your reverse proxy keeps serving the app and asks OnceGate
`GET /_once/auth` for each request:

| Situation | Reply |
|---|---|
| No valid session | `401` |
| Valid session, method allowed (from `X-Forwarded-Method`, default `GET`) | `200` + `X-OnceGate-User: <label>` |
| Valid session, method not allowed | `403` |

Route `/_once/*` itself to OnceGate so the link, confirm and logout pages work. Examples are in
[reverse-proxies.md](reverse-proxies.md).

## Endpoints

| Path | Port | Purpose |
|---|---|---|
| `GET /_once/<code>` | public | Confirm page (or "already used / cancelled / expired / not valid") |
| `POST /_once/<code>` | public | Use the link and start a session |
| `GET /_once/logout` | public | End this browser's session |
| `GET /_once/auth` | public | Forward-auth check |
| anything else | public | Proxied to the app if the session is valid, otherwise "You need an invite link" |
| `GET /` | admin | Links, sessions, the create form (sign-in form if signed out) |
| `POST /login`, `/logout` | admin | Admin sign-in and sign-out |
| `POST /invite`, `/revoke` | admin | Create a link; cancel a link or end a session |

## Status codes guests may see

| Code | Meaning |
|---|---|
| `401` | No session: "You need an invite link" |
| `403` | Signed in, but tried to change something (view-only) |
| `404` | That link doesn't exist |
| `410` | That link was used, cancelled or expired |
| `413` | Upload larger than `ONCEGATE_MAX_BODY` |
| `429` | Too many tries from one address in a minute |
| `502` | The app behind OnceGate isn't answering |
