# Running OnceGate on a NAS

OnceGate is one small container with two folders (`data`, `secrets`). Anything that runs Docker
can run it. The image is built for `amd64`, `arm64` and `armv7`.

## QNAP (Container Station 3)

This is the setup OnceGate was first deployed on, guarding a family portal, with a Tailscale Funnel
sidecar. It was tested end to end on a TS-453Be running QTS 5.2.

1. **Make the folders** in File Station: `Container/oncegate/` with `data/`, `secrets/` and
   `tailscale/` inside. Copy `examples/serve.json` to `Container/oncegate/serve.json`.
2. **Secrets:** create `secrets/admin-password` (one line, 8+ characters). For Funnel, also
   `secrets/ts-authkey`; see [tailscale-funnel.md](tailscale-funnel.md).
   - On a Mac, create these in TextEdit with **Format → Make Plain Text**, and don't let it add
     `.txt`.
3. **Container Station → Applications → Create**, name it `oncegate`, and paste
   [`examples/qnap-container-station.yml`](../examples/qnap-container-station.yml). Edit
   `ONCEGATE_UPSTREAM`, `ONCEGATE_PUBLIC_URL` and `ONCEGATE_TITLE`, then click **Validate** and
   **Create**.
4. The admin page is at `http://<nas>:9098`.

QNAP notes:

- **Use host networking.** On QNAP, containers on Docker's bridge network often can't reach other
  services through the NAS's own IP address, or even through the Docker gateway. Host networking
  avoids that. It's why both services in the example use `network_mode: host`.
- **Changing settings:** Applications → ⋯ → **Recreate** lets you edit the YAML. **Edit** only
  changes the web-link setting.
- **The QNAP Tailscale app** can keep running. The sidecar runs in userspace mode on its own port
  and socket.
- **Files written by the container** are owned by root with mode 600, so you can't open
  `data/oncegate.json` over SMB. That's intended. Use the admin page or `docker exec` for the
  command line.
- **The example runs from a plain `python:3.12-alpine` image** with `oncegate.py` mounted from
  `Container/oncegate/app/`. You can also use `ghcr.io/gmoyle/oncegate:latest` and drop the `app`
  volume and `command`.
- **Watchtower** or similar auto-updaters will keep the image current. Data carries over.

## Synology (Container Manager)

1. Create `docker/oncegate/{data,secrets}` in File Station, then add `secrets/admin-password`.
2. **Container Manager → Project → Create**, point it at `docker/oncegate`, and paste
   `examples/docker-compose.yml` or `examples/tailscale-funnel.yml`, adjusting the paths.
3. If you use bridge networking, publish only `9099` to your reverse proxy and keep `9098` on
   the LAN.

## Unraid

Add a container from `ghcr.io/gmoyle/oncegate:latest`:

- Paths: `/data` → `/mnt/user/appdata/oncegate/data`, and `/secrets` →
  `/mnt/user/appdata/oncegate/secrets` (read-only).
- Ports: `9099`, plus `9098` on the LAN only.
- Variables: `ONCEGATE_UPSTREAM`, `ONCEGATE_PUBLIC_URL`, `ONCEGATE_TITLE`.

## Raspberry Pi / any Linux

```bash
docker compose -f examples/docker-compose.yml up -d
```

Or, without Docker:

```bash
ONCEGATE_DATA_DIR=$HOME/.oncegate ONCEGATE_ADMIN_PASSWORD_FILE=$HOME/.oncegate/admin-password \
ONCEGATE_UPSTREAM=http://127.0.0.1:8080 python3 oncegate.py serve
```
