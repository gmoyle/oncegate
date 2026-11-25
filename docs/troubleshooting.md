# Troubleshooting

## Admin page

| Symptom | Cause and fix |
|---|---|
| "Set an admin password first" | `ONCEGATE_ADMIN_PASSWORD_FILE` is missing, empty, has more than one line, or is under 8 characters. Fix the file and reload; no restart is needed. |
| "Not from here" (403) | Your address isn't in `ONCEGATE_ADMIN_ALLOW`. Use the admin page from your home network or Tailscale, or widen the setting. |
| "That password didn't match" with the right password | The file was saved with formatting (rich text) or a byte-order mark plus a second line. Re-save it as plain text, one line. |
| "Too many tries" | 10 sign-in attempts per minute per address. Wait a minute. |
| Signed out unexpectedly | Admin sign-ins live in memory, so a restart or upgrade signs you out. They also expire after 12 hours. |
| "That request came from another site" | The form was posted from a different origin than the admin page, often through an extra proxy. Open the admin page directly at `http://<server>:9098`. |
| "The form was out of date" | Reload the admin page and try again. |
| New links start with `/_once/` instead of `https://…` | `ONCEGATE_PUBLIC_URL` isn't set. |

## Guests

| Symptom | Cause and fix |
|---|---|
| "This link has already been used" the first time they open it | Someone, or something, pressed **Open** before them: a shared device, a forwarded message, or an unusually aggressive security scanner that submits forms. Make a new link and send it directly to the person. The admin page shows when and from where it was used. |
| Opens the confirm page, then "You need an invite link" after pressing Open | The browser didn't keep the cookie. Over `https://`, the cookie is `Secure` and `__Host-` prefixed, so make sure `ONCEGATE_PUBLIC_URL` matches the address actually used. Over plain `http://`, leave `ONCEGATE_PUBLIC_URL` unset or `http://…`. Private browsing modes that block all cookies won't work. |
| Something on the page won't save | Guests are view-only (`403`). That's intended. To allow more, change `ONCEGATE_GUEST_METHODS`, carefully. |
| "The site isn't answering" (502) | OnceGate can't reach `ONCEGATE_UPSTREAM`. From inside the container, check the address. On some NASes, bridge-network containers can't reach the host's own IP; use host networking. |
| "Too many tries" (429) | More than `ONCEGATE_RATE_PER_MIN` link-page requests from one address in a minute. Many people behind one public IP (an office, a hotel) can hit this; raise the setting. |
| Session ended early | Someone pressed **End now**, the session reached its length, or they visited `/_once/logout`. |

## Command line

| Symptom | Cause and fix |
|---|---|
| `PermissionError` on `oncegate.json` | You're running the command outside the container, against files the container owns. Use `docker exec oncegate python /app/oncegate.py …`. |
| `invite` prints a link but the gate says "isn't valid" | The command and the server use different `ONCEGATE_DATA_DIR`s. Run it inside the container. |

## Checking things directly

```bash
# Is the public side up? (401 without a session is healthy)
curl -i http://<server>:9099/_once/auth

# What happened recently? (no codes are ever logged)
docker exec oncegate tail -n 20 /data/audit.log
```
