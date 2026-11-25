# Reverse proxies and forward auth

Two ways to use OnceGate behind a proxy that already handles HTTPS:

- **Proxy mode (simplest):** send the whole public site to OnceGate (`:9099`). OnceGate forwards
  allowed requests to `ONCEGATE_UPSTREAM`.
- **Forward-auth mode:** the proxy keeps serving your app and asks OnceGate `/_once/auth` before
  each request. Use this when the app is already wired into your proxy, or for several apps behind
  one gate.

In both modes, send **`/_once/*`** to OnceGate itself (the link, confirm and logout pages), set
`ONCEGATE_PUBLIC_URL` to the public `https://` address, and keep the admin port (`9098`) off the
internet.

Don't log full request URLs for `/_once/*` in your proxy; they contain the link codes.

## Caddy

**Proxy mode**

```caddyfile
guests.example.com {
	reverse_proxy oncegate:9099
}
```

**Forward-auth mode**

```caddyfile
guests.example.com {
	handle /_once/* {
		reverse_proxy oncegate:9099
	}
	handle {
		forward_auth oncegate:9099 {
			uri /_once/auth
			copy_headers X-OnceGate-User
		}
		reverse_proxy myapp:8080
	}
}
```

Caddy sends `X-Forwarded-Method` itself, so view-only is enforced.

## Traefik (Docker labels)

**Proxy mode**

```yaml
services:
  oncegate:
    image: ghcr.io/gmoyle/oncegate:latest
    environment:
      ONCEGATE_UPSTREAM: http://myapp:8080
      ONCEGATE_PUBLIC_URL: https://guests.example.com
    volumes: ["./data:/data", "./secrets:/secrets:ro"]
    labels:
      - traefik.enable=true
      - traefik.http.routers.guests.rule=Host(`guests.example.com`)
      - traefik.http.routers.guests.entrypoints=websecure
      - traefik.http.routers.guests.tls.certresolver=letsencrypt
      - traefik.http.services.guests.loadbalancer.server.port=9099
```

**Forward-auth mode**

```yaml
services:
  oncegate:
    image: ghcr.io/gmoyle/oncegate:latest
    environment:
      ONCEGATE_PUBLIC_URL: https://guests.example.com
    volumes: ["./data:/data", "./secrets:/secrets:ro"]
    labels:
      - traefik.enable=true
      - traefik.http.routers.oncegate.rule=Host(`guests.example.com`) && PathPrefix(`/_once/`)
      - traefik.http.routers.oncegate.priority=100
      - traefik.http.routers.oncegate.tls.certresolver=letsencrypt
      - traefik.http.services.oncegate.loadbalancer.server.port=9099
      - traefik.http.middlewares.oncegate-auth.forwardauth.address=http://oncegate:9099/_once/auth
      - traefik.http.middlewares.oncegate-auth.forwardauth.authResponseHeaders=X-OnceGate-User

  myapp:
    image: example/myapp
    labels:
      - traefik.enable=true
      - traefik.http.routers.myapp.rule=Host(`guests.example.com`)
      - traefik.http.routers.myapp.tls.certresolver=letsencrypt
      - traefik.http.routers.myapp.middlewares=oncegate-auth
```

Traefik sends `X-Forwarded-Method` automatically.

## nginx

**Proxy mode**

```nginx
server {
    listen 443 ssl;
    server_name guests.example.com;
    # ssl_certificate …; ssl_certificate_key …;

    location / {
        proxy_pass http://127.0.0.1:9099;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_buffering off;
    }
}
```

**Forward-auth mode** (`auth_request`)

```nginx
server {
    listen 443 ssl;
    server_name guests.example.com;

    location /_once/ {
        proxy_pass http://127.0.0.1:9099;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location = /_oncegate_check {
        internal;
        proxy_pass http://127.0.0.1:9099/_once/auth;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Forwarded-Method $request_method;   # keeps guests view-only
        proxy_set_header Cookie $http_cookie;
    }

    location / {
        auth_request /_oncegate_check;
        auth_request_set $oncegate_user $upstream_http_x_oncegate_user;
        proxy_set_header X-OnceGate-User $oncegate_user;
        error_page 401 = @no_invite;
        proxy_pass http://127.0.0.1:8080;
    }

    location @no_invite {
        proxy_pass http://127.0.0.1:9099;   # shows OnceGate's "You need an invite link" page
    }
}
```

## Cloudflare Tunnel

Point the tunnel's public hostname at `http://localhost:9099` (proxy mode). Set
`ONCEGATE_PUBLIC_URL` to that hostname. Leave `ONCEGATE_TRUST_PROXY=1`; Cloudflare sends
`X-Forwarded-For`.

## Client IP addresses

OnceGate reads the visitor's address from `X-Forwarded-For` when `ONCEGATE_TRUST_PROXY=1` (the
default). That's right whenever a proxy sits in front. If OnceGate faces the internet directly, set
it to `0`, or visitors could fake the address shown on the admin page and used for rate limiting.
