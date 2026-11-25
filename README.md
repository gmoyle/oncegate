# OnceGate

**Single-use invite links in front of any web app.**

[![ci](https://github.com/gmoyle/oncegate/actions/workflows/ci.yml/badge.svg)](https://github.com/gmoyle/oncegate/actions/workflows/ci.yml)
![license: MIT](https://img.shields.io/badge/license-MIT-blue)
![dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)

Send someone a link. The first time they open it and tap **Open**, the link is used up and their
browser gets a session for a few hours. Anyone who tries the same link afterwards gets
*"This link has already been used"*. Everyone without a session gets nothing at all.

It's made for home servers: a family dashboard, a photo page, a status board. You want to let one
person in, once, from anywhere, without making them install a VPN or create an account.

```
guest ──https──▶ Tailscale Funnel / your proxy ──▶ OnceGate :9099 ──▶ your app
you (home network) ────────────────────────────▶ OnceGate admin :9098
```

## Why

Existing tools come close but miss the point:

- **Share links in reverse-proxy and tunnel tools** expire by date, and anyone holding the link can
  use it until then.
- **Identity providers** (Authentik and similar) have single-use *invitations*. Those create an
  account, which is a lot of machinery for "let my sister see this page tonight".
- **Tailscale Funnel** makes something public but has no notion of who's allowed in.

OnceGate is the missing small piece: a link that works **once**, swapped for a short session, with
a page to make, cancel and audit links.

## Features

- **Single use.** A forwarded or leaked link is useless once opened. If two people press **Open**
  at the same moment, exactly one wins.
- **Preview-proof.** iMessage, Slack, WhatsApp and mail scanners open links to build previews.
  Opening the link only shows a confirm page; nothing is used up until a person presses the button.
- **Sessions, not tokens in URLs.** The code is swapped for an `HttpOnly`, `SameSite=Lax` cookie
  (plus `Secure` with the `__Host-` prefix over HTTPS). The code never appears in the address bar
  again.
- **View-only by default.** Guests can `GET` and `HEAD`; anything else gets `403`. This applies in
  both proxy and forward-auth modes.
- **Revocable.** Cancel unused links and end live sessions from the admin page or the command line.
- **Nothing reusable at rest.** Only SHA-256 fingerprints of codes and sessions are stored.
- **Audit log.** One JSON line per link created, used or revoked, and per admin sign-in, without
  the codes themselves.
- **No dependencies.** One Python file, standard library only, runs on Python 3.10+. Container
  images for amd64, arm64 and armv7.
- **Two ways to plug in.** Use it as a small reverse proxy, or as a forward-auth check for Caddy,
  Traefik or nginx.

## Contents

- [Quick start](#quick-start)
- [Making and managing links](#making-and-managing-links)
- [What guests see](#what-guests-see)
- [Getting a public address](#getting-a-public-address)
- [Configuration](#configuration)
- [Letting your app know about guests](#letting-your-app-know-about-guests)
- [Data, backups and upgrades](#data-backups-and-upgrades)
- [Security](#security)
- Guides: [How it works](docs/how-it-works.md) · [Tailscale Funnel](docs/tailscale-funnel.md) ·
  [Reverse proxies & forward auth](docs/reverse-proxies.md) · [NAS platforms](docs/nas-platforms.md) ·
  [Troubleshooting](docs/troubleshooting.md)

## Quick start

```bash
mkdir oncegate && cd oncegate && mkdir data secrets
echo 'choose-a-long-admin-password' > secrets/admin-password

docker run -d --name oncegate --restart unless-stopped --network host \
  -e ONCEGATE_UPSTREAM=http://127.0.0.1:8080 \
  -e ONCEGATE_PUBLIC_URL=https://guests.example.com \
  -v "$PWD/data:/data" -v "$PWD/secrets:/secrets:ro" \
  ghcr.io/gmoyle/oncegate:latest
```

- `ONCEGATE_UPSTREAM` is the app you're protecting.
- `ONCEGATE_PUBLIC_URL` is the address guests will use. It's only needed so new links are complete.
- The public side listens on **:9099** and the admin page on **:9098**.

Then put it on the internet. [Tailscale Funnel](docs/tailscale-funnel.md) is free and needs no open
router ports; the [reverse-proxy guide](docs/reverse-proxies.md) covers Caddy, Traefik and nginx.

No Docker? `python3 oncegate.py serve` with the same environment variables works anywhere Python
3.10+ does.

## Making and managing links

### Admin page

Open `http://<server>:9098` from your home network and sign in with the admin password.

1. Under **New single-use link**, type who it's for (shown to them as "Hi Lily"), an optional
   session length in hours, and an optional note for yourself.
2. Click **Create link** and copy it. **It's shown once.** Only a fingerprint is stored, so it can't
   be displayed again; if you lose it, cancel it and make another.
3. Send it however you like: text, email, chat.

The page lists every link (*ready*, *used*, *expired* or *revoked*, with when and from where it was
used) and every session (*live* with time left, or *ended*). **Cancel** voids an unused link.
**End now** signs a guest out immediately.

The admin page only answers addresses in `ONCEGATE_ADMIN_ALLOW` (private ranges and Tailscale by
default). Sign-ins last 12 hours and are kept in memory, so restarting OnceGate signs you out.

### Command line

The same actions work from a shell, which is handy for scripts and automations:

```bash
docker exec oncegate python /app/oncegate.py invite --label "Lily" --hours 4 --note "hotel wifi"
# prints https://guests.example.com/_once/3vV0…

docker exec oncegate python /app/oncegate.py list
# link    1a2b3c4d5e6f  ready    Lily
# session 9f8e7d6c5b4a  live     Sam  3h 12m left

docker exec oncegate python /app/oncegate.py revoke 1a2b3c4d5e6f
```

| Command | Does |
|---|---|
| `serve` | Run the gate (the default) |
| `invite --label NAME [--hours H] [--note TEXT]` | Create a link and print it |
| `list` | Show links and sessions with their ids and states |
| `revoke ID` | Cancel a link or end a session |

The running server notices changes made from the command line straight away.

## What guests see

1. **The link page:** "Hi Lily. This link works once. Tap the button to open the site on this
   device for 4 hours." It has one **Open the site** button.
2. **Your app**, for the session length, view-only. `/_once/logout` signs them out early.
3. **Afterwards**, or with someone else's used link: *"This link has already been used. Ask for a
   new one if you need to get back in."*
4. **Without a link:** *"You need an invite link. This site is private."*

The pages are plain, work on phones, follow the system's light or dark mode, and use the name from
`ONCEGATE_TITLE`.

## Getting a public address

| Option | Cost | Open router ports | Guide |
|---|---|---|---|
| **Tailscale Funnel** sidecar | Free | None | [docs/tailscale-funnel.md](docs/tailscale-funnel.md) |
| Your existing Caddy / Traefik / nginx | Your domain | Usually 443 | [docs/reverse-proxies.md](docs/reverse-proxies.md) |
| Cloudflare Tunnel | Free with a domain on Cloudflare | None | Point the tunnel at `http://localhost:9099` |

Whichever you choose, only expose port **9099**. Never put **9098** (admin) on the internet.

## Configuration

All settings are environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `ONCEGATE_UPSTREAM` | `http://127.0.0.1:8080` | The app being protected (proxy mode) |
| `ONCEGATE_PUBLIC_URL` | *(empty)* | Public base URL used in new links. Use `https://`, so cookies are `Secure` |
| `ONCEGATE_LISTEN` / `ONCEGATE_PORT` | `0.0.0.0` / `9099` | Public side |
| `ONCEGATE_ADMIN_LISTEN` / `ONCEGATE_ADMIN_PORT` | `0.0.0.0` / `9098` | Admin page |
| `ONCEGATE_ADMIN_PASSWORD_FILE` | `/secrets/admin-password` | One line, 8+ characters. The admin page stays locked without it |
| `ONCEGATE_ADMIN_ALLOW` | private ranges, `100.64.0.0/10`, Tailscale IPv6 | Comma-separated networks allowed to reach the admin page |
| `ONCEGATE_DATA_DIR` | `/data` | Where `oncegate.json` and `audit.log` live |
| `ONCEGATE_SESSION_HOURS` | `4` | Default session length after a link is used |
| `ONCEGATE_MAX_SESSION_HOURS` | `168` | Upper limit for any session |
| `ONCEGATE_INVITE_DAYS` | `0` | Unused links expire after this many days (`0` = never) |
| `ONCEGATE_GUEST_METHODS` | `GET,HEAD` | HTTP methods guests may use |
| `ONCEGATE_TITLE` | `Private site` | Name shown on guest pages |
| `ONCEGATE_COOKIE` | `oncegate` | Session cookie name (gets a `__Host-` prefix over HTTPS) |
| `ONCEGATE_TRUST_PROXY` | `1` | Read the client IP from `X-Forwarded-For` (turn off if nothing sits in front) |
| `ONCEGATE_RATE_PER_MIN` | `30` | Link-page tries per IP per minute |
| `ONCEGATE_MAX_BODY` | `10485760` | Largest request body passed to the app, in bytes |

Docker [secrets](https://docs.docker.com/compose/how-tos/use-secrets/) work too: point
`ONCEGATE_ADMIN_PASSWORD_FILE` at `/run/secrets/<name>`.

## Letting your app know about guests

In proxy mode, OnceGate adds two headers:

- On **requests to your app:** `X-OnceGate-User: <label>`, the name the link was made for. Any copy
  of this header sent by the visitor is removed first, so it can't be faked.
- On **every response to a guest:** `X-OnceGate: guest`.

That lets a page hide things guests shouldn't see. The family portal OnceGate was built for hides
logins and "Add" buttons like this:

```js
fetch("/healthz").then(r => {
  if (r.headers.get("X-OnceGate") === "guest") document.documentElement.dataset.guest = "";
});
```

```css
[data-guest] .owner-only { display: none !important; }
```

Hiding is a convenience, not security. OnceGate already blocks guests from changing anything, but
**don't put anything behind OnceGate that a guest mustn't be able to read**.

## Data, backups and upgrades

| Path | Contents | Back up? |
|---|---|---|
| `data/oncegate.json` | Link and session fingerprints, labels, times, a signing secret (mode 600) | Optional. Losing it cancels all links and sessions |
| `data/audit.log` | One JSON line per event; never contains codes | If you want the history |
| `secrets/admin-password` | Your admin password | Yes, somewhere safe |

**Upgrading:** pull the new image and recreate the container. Data carries over; unused links and
live sessions keep working. You'll need to sign in to the admin page again.

**Clean-up:** used, revoked and expired entries older than 30 days are pruned whenever the admin page is opened. Unused links stay until
they're used, cancelled or reach `ONCEGATE_INVITE_DAYS`.

## Security

- **Codes and session tokens** are 256-bit random values (`secrets.token_urlsafe(32)`). Only their
  SHA-256 hashes are stored and compared, in constant time.
- **Using a link** is atomic under a lock, and checked again from disk, so simultaneous presses
  can't both win.
- **Guest pages** send `Referrer-Policy: no-referrer`, a strict Content-Security-Policy,
  `X-Frame-Options: DENY` and `Cache-Control: no-store`.
- **No access logs.** OnceGate doesn't write them because they would contain codes. Make sure the
  proxy in front doesn't log full URLs for `/_once/*` either.
- **Rate limit:** link pages allow 30 tries per IP per minute by default, and admin sign-in 10 per
  minute. Failed admin sign-ins go to the audit log.
- **Admin page:** password, CSRF token and Origin check, `SameSite=Strict` session cookie, and only
  reachable from `ONCEGATE_ADMIN_ALLOW`. On a home network without HTTPS, the admin password
  crosses your LAN unencrypted, like most NAS admin pages. Reach it over Tailscale if that matters
  to you.
- **Image:** the container runs as an unprivileged user.
- **Limits:** a session is only as private as the device it's on, so keep sessions short. Anyone
  who can read your `data/` folder can't use the fingerprints, but anyone who can change OnceGate's
  files or environment can change who gets in.

Found a problem? See [SECURITY.md](SECURITY.md).

## Development

```bash
python3 -m unittest discover -s tests -v   # 12 end-to-end tests, real HTTP, no dependencies
```

The tests start a fake upstream app and OnceGate on random ports. They cover single use, races,
link previews, view-only, cookie stripping, forged headers, revocation, expiry, forward-auth, the
admin sign-in and CSRF, and links made by another process. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).
