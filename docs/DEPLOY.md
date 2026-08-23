# Deploying beermap (Docker)

DNS is already set: `zapfkompass.de` (apex A to the host, Cloudflare-proxied, plus a
`www` CNAME), kept current by the host's dynamic-DNS updater. The old
`beermap.jakubwaller.eu` name still resolves and permanently redirects here.

## One-time

```bash
ssh <host>
cd ~ && git clone <repo-url> beer-map && cd beer-map
cp deploy/beermap.env.example deploy/beermap.env
# edit deploy/beermap.env: strong BEERMAP_ADMIN_PW, a random BEERMAP_IP_SALT,
# and the Telegram bot token + chat id
./docker-run.sh          # build image, start container, build the first dataset
```

Wire Caddy (auto-TLS). Caddy runs in its own container on the shared `web_proxy`
network and reverse-proxies to containers by name. Append the beermap block to
its Caddyfile and reload:

```bash
cat deploy/beermap.caddy >> <path-to-Caddyfile>
docker exec <caddy-container> caddy reload --config /etc/caddy/Caddyfile
```

## Data layout (important)

Two separate host directories, and they must stay separate:

| Host path    | In container    | Contents          | Public?          |
|--------------|-----------------|-------------------|------------------|
| `./data`     | `/app/data`     | `beer-map.sqlite` | **No — private** |
| `./web-data` | `/app/web/data` | `venues.json`     | Yes (served)     |

Everything under `/app/web` is served at `/` by StaticFiles, so **nothing but the
exported GeoJSON belongs in `web-data/`**. The app refuses to start if
`BEERMAP_DB_PATH` points inside the served directory.

## Daily data refresh

`crontab -e`:
```cron
0 4 * * * cd ~/beer-map && docker compose run --rm pipeline python -m pipeline.run >> ~/beer-map/pipeline.log 2>&1
0 5 * * 0 cd ~/beer-map && docker compose run --rm pipeline python -m pipeline.country >> ~/beer-map/country.log 2>&1
```
The `pipeline` service is the same image on the host network (IPv6, see
`docker-compose.yml`). The weekly sweep sits at Sunday 05:00 so it never starts alongside
papa-map's 02:00 sweep on the same host: Overpass bans per source IP, and did on 2026-08-23.

## Moderation

`https://zapfkompass.de/admin` — HTTP Basic (`admin` / `BEERMAP_ADMIN_PW`).
Approvals apply the edit and re-export `venues.json` instantly.

## Update the app

```bash
cd ~/beer-map && git pull && ./docker-run.sh   # rebuilds image + dataset
```

## Cloudflare cache

The site is behind Cloudflare, which caches static assets (~4h). `docker-run.sh`
stamps asset URLs with the git SHA so `app.js`/`style.css` bust themselves, but
`venues.json` and anything else under `web-data/` can serve stale for a while.
To force it, purge from the Cloudflare dashboard (*Caching → Configuration →
Purge Everything*, or purge individual URLs), or:

```bash
curl -X POST "https://api.cloudflare.com/client/v4/zones/<zone-id>/purge_cache" \
  -H "Authorization: Bearer <api-token>" -H "Content-Type: application/json" \
  --data '{"files":["https://zapfkompass.de/data/venues.json"]}'
```

If a file was ever served that should not have been, purging it is part of the
fix — Cloudflare keeps handing out its cached copy otherwise, long after the
origin stops serving it.

## Verify

```bash
curl -s https://zapfkompass.de/api/brands | head
curl -s -o /dev/null -w "%{http_code}\n" https://zapfkompass.de/
# the DB must never be reachable:
curl -s -o /dev/null -w "%{http_code}\n" https://zapfkompass.de/data/beer-map.sqlite
```
