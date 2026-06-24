# Deploying beermap to the Pi (Docker)

DNS is already set: `beermap.jakubwaller.eu` (A `<pi-ip>` + AAAA, proxied:false),
kept current by `~/pivalert/ip-address/check-ip-address.sh` on the Pi.

## One-time

```bash
ssh <deploy-host>
cd ~ && git clone <repo-url> beer-map && cd beer-map
cp deploy/beermap.env.example deploy/beermap.env
# edit deploy/beermap.env: set a strong BEERMAP_ADMIN_PW and RESEND_API_KEY
./docker-run.sh          # build image, start container, build the first dataset
```

Wire Caddy (auto-TLS). Caddy runs as the `elternschule-caddy-1` container over the
shared `web_proxy` network and reverse-proxies to containers by name. Append the
beermap block to its Caddyfile and reload:

```bash
cat deploy/beermap.caddy >> /home/ubuntu/elternschule/Caddyfile
docker exec elternschule-caddy-1 caddy reload --config /etc/caddy/Caddyfile
```

## Daily data refresh

`crontab -e`:
```cron
0 4 * * * cd /home/ubuntu/beer-map && docker compose exec -T beermap python -m pipeline.run >> /home/ubuntu/beer-map/pipeline.log 2>&1
```

## Moderation

`https://beermap.jakubwaller.eu/admin` — HTTP Basic (`admin` / `BEERMAP_ADMIN_PW`).
Approvals apply the edit and re-export `venues.json` instantly.

## Update the app

```bash
cd ~/beer-map && git pull && ./docker-run.sh   # rebuilds image + dataset
```

## Verify

```bash
curl -s https://beermap.jakubwaller.eu/api/brands | head
curl -s -o /dev/null -w "%{http_code}\n" https://beermap.jakubwaller.eu/
```
