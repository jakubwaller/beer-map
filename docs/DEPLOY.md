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

The full path — rebuild the image, then rebuild the dataset inside it:

```bash
cd ~/beer-map && git pull && ./docker-run.sh   # rebuilds image + dataset
```

**A change that does not touch the data does not need the dataset rebuild.** The image build takes
seconds; `pipeline.run` re-queries Overpass and takes minutes. So for a frontend, API or copy
change:

```bash
cd ~/beer-map && git pull --ff-only && ASSET_VERSION="$(git rev-parse --short HEAD)" docker compose up -d --build
```

**Setting `ASSET_VERSION` is not optional.** It is a build arg, `sed`-substituted into
`web/index.html` and `web/app.js` at image build time (`Dockerfile`); omit it and Compose falls back
to `${ASSET_VERSION:-dev}`, so every deploy serves `app.js?v=dev` — a URL that never changes, which
means Cloudflare keeps handing out the previous copy for hours. `docker-run.sh` exports it for you;
this command has to do it itself. Check what is actually live with:

```bash
curl -s https://zapfkompass.de/ | grep -oE 'app\.js\?v=[a-f0-9]+'   # must match the deployed SHA
```

Take the full path whenever the dataset itself has to change: a new or edited finder, a hand-edited
`curation.yaml`, a new city. The nightly cron below does the same rebuild, so a data change that can
wait until 04:00 needs no deploy at all.

## Auto-deploy (GitHub Actions)

Every push to `main` — in practice every squash-merge — runs
`.github/workflows/deploy.yml` once the CI workflow has passed for that commit:
it SSHes to the VPS and runs the web-only path above, then checks the result.
Deploys queue rather than overlap. You can also fire one by hand from the
Actions tab (`workflow_dispatch`), from `main` only — the VPS tracks `main`, so
a dispatch from another branch would deploy `main` and then report red.

**Do not edit tracked files on the VPS.** `curation.yaml` is bind-mounted into
the container, which makes editing it in place tempting; one such edit makes
`git pull --ff-only` refuse, and every merge from then on fails until someone
commits or discards the change. The workflow names the dirty file in its error
instead of failing with git's generic message.

It deliberately **does not rebuild the dataset**: `pipeline.run` re-queries
Overpass and takes minutes, and the 04:00 cron does it anyway. So a change that
alters the data itself — a new or edited finder, a hand-edited `curation.yaml`,
a new city — is live in the app immediately but shows its new *data* after the
next nightly run. Take the full path by hand when that is too slow.

The workflow fails loudly rather than reporting a green deploy that did not
happen. It checks the VPS checkout contains the pushed SHA (a descendant is
fine: two quick merges leave the earlier run looking at the later commit), that
`https://zapfkompass.de/` answers 200, that the page serves `app.js?v=` with the
short hash the VPS built (proof the new image is live and Cloudflare is not
still replaying the old one), and that the sqlite DB answers 404.

### Auto-deploy secrets

Set on the repository (Settings -> Secrets and variables -> Actions):

| Secret | Value | Required |
|---|---|---|
| `DEPLOY_SSH_KEY` | private half of a dedicated deploy keypair, unencrypted | yes |
| `DEPLOY_SSH_TARGET` | `user@host` of the VPS | yes |
| `DEPLOY_SSH_PORT` | SSH port, if not 22 | no |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -p <port> <host>` output; pins the host key | no, but do it |

The public half goes in the VPS user's `~/.ssh/authorized_keys`. Use a keypair
minted for this repo rather than an existing one, so it can be revoked on its
own. Without `DEPLOY_KNOWN_HOSTS` the workflow falls back to
`StrictHostKeyChecking accept-new`, which trusts whatever answers on the first
run.

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
