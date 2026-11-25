# Security policy

OnceGate decides who can see a site, so security reports are very welcome.

## Reporting a problem

Please **don't open a public issue** for a security problem. Use GitHub's private reporting
instead: on this repository, go to **Security → Report a vulnerability**. Include what you found,
how to reproduce it, and what an attacker could do with it.

You'll get a reply within a week. Fixes are released as soon as they're ready, and reporters are
credited unless they'd rather not be.

## Supported versions

Only the latest release gets security fixes.

## What's in scope

- Getting a session without a valid, unused link
- Using a link more than once, or reviving a cancelled link or ended session
- Getting a code or session token out of OnceGate, its files or its logs
- Making changes as a guest (anything other than `ONCEGATE_GUEST_METHODS`)
- Reaching or acting on the admin page without the password, or from outside `ONCEGATE_ADMIN_ALLOW`
- Faking `X-OnceGate-User` to the app behind it

## Out of scope

- The app behind OnceGate showing guests things it shouldn't. OnceGate controls who gets in, not
  what the app shows.
- Anyone who already controls the server, its files or OnceGate's environment
- Denial of service by sheer volume. Put a rate-limiting proxy or Tailscale in front.
- The admin password crossing an unencrypted home network. This is documented; use Tailscale or
  HTTPS for the admin page if it matters.
