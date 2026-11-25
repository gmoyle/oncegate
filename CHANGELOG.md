# Changelog

All notable changes to OnceGate. Versions follow [Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-11-25

### Added

- **Show note to the guest**: a tick box next to the note on the admin page (`--show-note` on the
  command line). When ticked, the note appears under "Hi <name>" on the link page, as plain text.
  Notes stay private by default, and the admin list marks the ones guests can see.

## [0.1.1] - 2026-11-25

### Added

- **Copy link** button next to a newly created link on the admin page. It works on plain-http home
  networks, where browsers don't offer the modern clipboard, and falls back to selecting the link.
  The admin page's Content-Security-Policy allows only that one script, by per-response nonce.

## [0.1.0] - 2026-11-25

First release.

### Added

- Single-use invite links (`/_once/<code>`) swapped for a session cookie on first use.
- A confirm button so link previews and mail scanners can't use up a link.
- Atomic redemption: simultaneous presses can't both win.
- Proxy mode with view-only guests (`ONCEGATE_GUEST_METHODS`), `X-OnceGate-User` to the app, and
  `X-OnceGate: guest` on responses.
- Forward-auth endpoint `/_once/auth` for Caddy, Traefik and nginx, view-only via
  `X-Forwarded-Method`.
- Admin page (LAN only): sign-in form, create links, cancel links, end sessions; CSRF token, Origin
  check and `SameSite=Strict` cookie.
- Command line: `serve`, `invite`, `list`, `revoke`. The running server picks up changes straight
  away.
- Only SHA-256 fingerprints stored; audit log without codes; per-IP rate limits.
- Container image (non-root, health check) for amd64, arm64 and armv7.
- Guides for Tailscale Funnel, reverse proxies, NAS platforms and troubleshooting.

[0.1.2]: https://github.com/gmoyle/oncegate/releases/tag/v0.1.2
[0.1.1]: https://github.com/gmoyle/oncegate/releases/tag/v0.1.1
[0.1.0]: https://github.com/gmoyle/oncegate/releases/tag/v0.1.0
