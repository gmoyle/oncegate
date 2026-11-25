# Contributing

Thanks for helping. OnceGate aims to stay small, dependency-free and easy to read, because people
need to be able to trust it.

## Ground rules

- **Standard library only.** No third-party packages, in the app or the tests.
- **One file.** `oncegate.py` holds the app. Keep it readable top to bottom.
- **Python 3.10+.**
- **Security first.** Anything touching codes, sessions, cookies or the admin page needs a test,
  and a sentence in the PR on why it's safe.
- **Plain words** on guest-facing pages and in the docs.

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

They start real HTTP servers on random ports and finish in a couple of seconds. CI runs them on
Python 3.10, 3.12 and 3.13, then builds the container image.

## Running it locally

```bash
mkdir -p /tmp/og/secrets && echo 'local-test-password' > /tmp/og/secrets/admin-password
ONCEGATE_DATA_DIR=/tmp/og/data ONCEGATE_ADMIN_PASSWORD_FILE=/tmp/og/secrets/admin-password \
ONCEGATE_UPSTREAM=http://127.0.0.1:8000 python3 oncegate.py serve
# in another terminal, something to protect:
python3 -m http.server 8000
```

Then open `http://127.0.0.1:9098` for the admin page.

## Pull requests

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Add or update tests and docs, plus a line in `CHANGELOG.md` under an *Unreleased* heading.
3. Keep PRs focused on one change.

## Releasing (maintainers)

1. Move *Unreleased* notes in `CHANGELOG.md` under the new version and update `__version__` in
   `oncegate.py`.
2. Commit, tag `vX.Y.Z`, and push the tag. CI builds and publishes
   `ghcr.io/gmoyle/oncegate:X.Y.Z` and `:latest`.
