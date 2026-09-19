# Deploying

**Current deployment (2026-09-19):** Google Cloud project `avelo-route-optimizer`,
always-free `e2-micro` VM `avelo` in `us-central1-a`, public IP `35.232.111.254`.
The whole stack (Caddy, API, both OSRM routers, and the web app) runs there from
pre-built images; see "Free-tier VM" below. Google keys live in that project only,
capped at 300 map loads / 300 autocompletes / 300 place lookups per day, with a
$5 budget alert.

Two halves: the **web app on Vercel** (free) and the **API + routers on a VPS**,
fronted by one Caddy instance that can serve any number of projects on subdomains
of `imranakki.com`.

```
imranakki.com               -> (your portfolio page, Vercel or static)
avelo.imranakki.com         -> Vercel   (web/)
avelo-api.imranakki.com     -> VPS      (api + osrm-bike + osrm-foot, via Caddy)
<next>.imranakki.com        -> Vercel   (next project's front-end)
<next>-api.imranakki.com    -> VPS      (next project's back-end, same Caddy)
```

## VPS, once

Hetzner CX22 (2 vCPU, 4 GB) is enough for this project; CX32 (8 GB) if two or
three more will share the box. Ubuntu 24.04.

```bash
# as root, fresh machine
apt update && apt install -y docker.io docker-compose-v2 git ufw
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
docker network create edge

mkdir -p /srv/caddy && cd /srv/caddy
# copy deploy/Caddyfile and deploy/docker-compose.caddy.yml here
docker compose -f docker-compose.caddy.yml up -d
```

## Free-tier VM (what is running now)

An `e2-micro` has 1 GB of RAM, so images are built here and streamed over SSH
rather than built on the box, and the web app runs on the VM too (behind Caddy)
until it moves to Vercel. `deploy/docker-compose.vm.yml` is the override in use;
`deploy/Caddyfile.ip` serves on the bare IP until DNS exists.

```bash
Z="--project avelo-route-optimizer --zone us-central1-a"
docker build -t avelo-api:prod .
docker build -t avelo-web:prod --build-arg NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=<browser key> ./web
docker save avelo-api:prod avelo-web:prod | gzip -1 | gcloud compute ssh avelo $Z --command "gunzip | docker load"
gcloud compute ssh avelo $Z --command "cd /srv/avelo && docker compose -f docker-compose.yml -f docker-compose.vm.yml up -d"
```

Data (`data/osrm/{bicycle,foot}`, `data/cache/*.json`) and `.env` were copied
with `tar | gcloud compute ssh` / `gcloud compute scp` into `/srv/avelo/`.

When DNS is ready: point `avelo.imranakki.com` at the IP, replace `:80` in
`/srv/caddy/Caddyfile` with the hostname, `docker compose restart caddy`; Caddy
obtains the certificate. Then set `AVELO_CORS_ORIGINS=https://avelo.imranakki.com`
in `/srv/avelo/.env` and restrict the browser key's referrers to that host.

## This project

```bash
cd /srv && git clone https://github.com/imranakki/avelo-route-optimizer.git avelo && cd avelo
./scripts/prepare_osrm.sh                    # ~10 min: Québec extract -> bike + foot routing data
printf 'AVELO_GOOGLE_MAPS_API_KEY=%s\n' '<server key, Places API (New), restricted to this IP>' > .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build osrm-bike osrm-foot api
docker compose exec api python scripts/build_cache.py   # optional: pre-fetch every polyline (~5 min)
curl -s https://avelo-api.imranakki.com/health
```

Updates: `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build api`.

## DNS (at your registrar, or Cloudflare if the domain's nameservers point there)

| record | name | value |
|---|---|---|
| A | `avelo-api` | VPS IPv4 |
| AAAA | `avelo-api` | VPS IPv6 (optional) |
| CNAME | `avelo` | `cname.vercel-dns.com` (Vercel shows the exact target) |

If Cloudflare proxies the record (orange cloud), keep it **DNS only** for
`avelo-api` on first deploy so Caddy can obtain its certificate; you can turn the
proxy on afterwards with SSL mode "Full (strict)".

## Vercel

Import the GitHub repo, **Root Directory = `web`**, framework Next.js. Environment
variables:

| name | value |
|---|---|
| `API_URL` | `https://avelo-api.imranakki.com` |
| `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` | browser key: Maps JavaScript API only, HTTP referrer `https://avelo.imranakki.com/*` |

Add the domain `avelo.imranakki.com` under *Settings → Domains*.

## Google Cloud (project `joinina`, or a new one per project)

1. Two keys: a **browser** key (Maps JavaScript API; referrer-restricted) and a
   **server** key (Places API (New); IP-restricted to the VPS).
2. Quotas: Maps JavaScript API *map loads per day* = 300; Places API (New)
   *requests per day* = 300. This is the hard stop on spending.
3. Billing → Budgets: alert at $5.

The API also caps billed calls per day itself (`AVELO_GOOGLE_DAILY_CAP`, 300) and
rate-limits search and routing per client.

## Adding the next project

1. Clone it into `/srv/<name>`; give its compose file the `edge` network as above.
2. Add a block to `/srv/caddy/Caddyfile` and `docker compose -f docker-compose.caddy.yml restart` (Caddy picks up the new hostname and certificate).
3. Add the DNS record. Front-end on Vercel as a separate Vercel project with its own subdomain.
