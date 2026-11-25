# Changelog

All notable changes to OnceGate. Versions follow [Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/gmoyle/oncegate/releases/tag/v0.1.0
