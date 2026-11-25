# Public links with Tailscale Funnel

[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) gives a device on your tailnet a public
`https://<name>.<tailnet>.ts.net` address, with a real certificate, and no router ports to open. It's
free on Tailscale's personal plan. OnceGate pairs well with it: Funnel makes the gate reachable, and
OnceGate decides who gets past it.

The setup below runs Tailscale as a **sidecar container in userspace mode**. That matters if your
server (a NAS, for example) already runs Tailscale: the sidecar is a separate tailnet device with
its own name, and it doesn't touch the host's network interfaces or the host's Tailscale.

## 1. Tailnet settings (once)

In the [Tailscale admin console](https://login.tailscale.com/admin):

1. **DNS:** make sure **MagicDNS** is on, then click **Enable HTTPS…**. Device names that get a
   certificate are published in public Certificate Transparency logs, so `oncegate.<tailnet>.ts.net`
   will be publicly visible. Nothing else about your network is.
2. **Access controls:** allow Funnel. Newer tailnets include this by default; if your policy has no
   `nodeAttrs` with `"funnel"`, add:

   ```jsonc
   "nodeAttrs": [
     { "target": ["autogroup:member"], "attr": ["funnel"] },
   ],
   ```

   This lets your own devices use Funnel. It doesn't change who can reach what inside your tailnet.
3. **Settings → Keys → Generate auth key.** The defaults are fine (one-time use, no tags). Copy it.

## 2. Files

```
oncegate/
├── docker-compose.yml       ← examples/tailscale-funnel.yml
├── serve.json               ← examples/serve.json (unchanged)
├── secrets/
│   ├── admin-password       ← one line, 8+ characters
│   └── ts-authkey           ← the tskey-auth-… key, one line
├── data/                    ← created on first run
└── tailscale/               ← Tailscale's state, created on first run
```

In `docker-compose.yml`, set:

- `ONCEGATE_UPSTREAM` to your app, for example `http://127.0.0.1:8080`;
- `ONCEGATE_PUBLIC_URL` to `https://oncegate.<your-tailnet>.ts.net`. Your tailnet name is on the
  DNS page; it looks like `tail1a2b3c.ts.net`.

`serve.json` tells the sidecar to publish port 443 with Funnel and forward it to OnceGate on
`127.0.0.1:9099`. `${TS_CERT_DOMAIN}` is filled in by Tailscale.

## 3. Start it

```bash
docker compose up -d
docker logs oncegate-tailscale    # look for the login succeeding and "serve config loaded"
```

The machine **oncegate** appears in your admin console's Machines list with a **Funnel** badge.
The first certificate can take a minute. Then check it from outside your network, for example on a
phone with Wi-Fi off:

```
https://oncegate.<your-tailnet>.ts.net/   →   "You need an invite link"
```

## 4. Tidy up

The auth key is only needed for the first sign-in; the sidecar keeps its identity in `tailscale/`.
Delete the key in **Settings → Keys** and empty `secrets/ts-authkey`. If you ever wipe `tailscale/`,
you'll need a new key.

## Why these settings

| Setting | Reason |
|---|---|
| `network_mode: host` on both | The sidecar reaches OnceGate at `127.0.0.1:9099`, and OnceGate reaches apps on the host. Some NASes stop bridge-network containers from reaching the host's own IP. |
| `TS_USERSPACE=true` | No TUN device or kernel routes; safe beside a host Tailscale |
| `TS_SOCKET=/tmp/oncegate-tailscaled.sock` | Avoids colliding with the host's `tailscaled` socket |
| `TS_TAILSCALED_EXTRA_ARGS=--port=41642` | The host's Tailscale usually has UDP 41641 |
| `TS_AUTHKEY=file:/secrets/ts-authkey` | Keeps the key out of the compose file |

## Funnel without the sidecar

If the host already runs Tailscale and you have a shell on it:

```bash
tailscale funnel --bg 9099
```

That publishes the **host's** tailnet name. It's simpler, but it ties the public address to the
host's name, and NAS packages don't always include the `funnel` command.

## Troubleshooting

| Symptom | Check |
|---|---|
| Machine never appears | The key was wrong, expired or already used. Make a new one, delete `tailscale/`, restart. |
| Appears but no Funnel badge | The policy is missing the `funnel` node attribute (step 1.2) |
| Browser shows a certificate error | HTTPS isn't enabled on the DNS page, or the first certificate is still being issued. Wait a minute. |
| Works inside your network but not outside | Test from a phone on mobile data. Devices on your tailnet reach the name over the tailnet instead of Funnel. |
| Name won't resolve at home | Your network may block outside DNS (common with Pi-hole or AdGuard rules). Test on mobile data. |
