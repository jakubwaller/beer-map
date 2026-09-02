"""Mint the OSM OAuth 2 token that `pipeline.osm_push` uploads with.

One-shot, run on a machine with a browser: it prints the authorization URL,
you approve the Zapfkompass app on openstreetmap.org (scope `write_api`, the
only one it asks for), paste the code the page shows back here, and the
token lands in the env file — never on the terminal. Copy the `OSM_TOKEN=`
line into `deploy/beermap.env` on the server afterwards.

The app is registered with the out-of-band redirect `urn:ietf:wg:oauth:2.0:oob`
because OSM refuses non-HTTPS redirect URIs, so there is no local callback
server; the code travels by clipboard. OSM tokens do not expire; revoke at
https://www.openstreetmap.org/oauth2/authorized_applications.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse

import httpx

from . import config

SCOPE = "write_api"
OOB = "urn:ietf:wg:oauth:2.0:oob"
DEFAULT_ENV_FILE = "~/.zapfkompass-osm.env"


def load_env_file(path: str) -> dict[str, str]:
    """`KEY=value` lines (comments and blanks skipped; surrounding quotes
    dropped). Real environment variables win over the file."""
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip().strip("'\"")
    return values


def write_env_line(path: str, key: str, value: str) -> None:
    """Replace or append `key=value`, leaving the file at mode 600."""
    lines: list[str] = []
    if os.path.exists(path):
        with open(path) as f:
            lines = [ln.rstrip("\n") for ln in f]
    lines = [ln for ln in lines if not ln.startswith(key + "=")]
    lines.append(f"{key}={value}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def authorize_url(client_id: str, redirect_uri: str = OOB, auth_url: str | None = None) -> str:
    q = urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "scope": SCOPE,
    })
    return f"{(auth_url or config.OSM_AUTH_URL).rstrip('/')}/oauth2/authorize?{q}"


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str = OOB,
                  auth_url: str | None = None, transport=None) -> str:
    base = (auth_url or config.OSM_AUTH_URL).rstrip("/")
    with httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=60,
                      transport=transport) as c:
        r = c.post(f"{base}/oauth2/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id, "client_secret": client_secret,
        })
        r.raise_for_status()
        return r.json()["access_token"]


def permissions(token: str, api_url: str | None = None, transport=None) -> list[str]:
    """What the token may do, as OSM reports it (`allow_write_api` is the
    one the upload needs)."""
    base = (api_url or config.OSM_API_URL).rstrip("/")
    with httpx.Client(headers={"User-Agent": config.USER_AGENT,
                               "Authorization": f"Bearer {token}"},
                      timeout=60, transport=transport) as c:
        r = c.get(f"{base}/api/0.6/permissions.json")
        r.raise_for_status()
        return list(r.json().get("permissions", []))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--env-file", default=DEFAULT_ENV_FILE,
                   help="where OSM_CLIENT_ID/OSM_CLIENT_SECRET are read from and "
                        "OSM_TOKEN is written to (default %(default)s)")
    args = p.parse_args(argv)
    path = os.path.expanduser(args.env_file)
    env = {**load_env_file(path), **{k: v for k, v in os.environ.items() if k.startswith("OSM_")}}
    client_id, secret = env.get("OSM_CLIENT_ID", ""), env.get("OSM_CLIENT_SECRET", "")
    if not client_id or not secret:
        print(f"OSM_CLIENT_ID and OSM_CLIENT_SECRET must be set in {path} (mode 600) "
              "or the environment; they come from the app's page under "
              f"{config.OSM_AUTH_URL}/oauth2/applications", file=sys.stderr)
        return 2
    redirect = env.get("OSM_REDIRECT_URI") or OOB

    print("Open this in a browser logged in to the OSM account the edits should belong to,")
    print("approve the write_api permission, and paste the code the page shows:\n")
    print("  " + authorize_url(client_id, redirect))
    print()
    code = input("Authorization code: ").strip()
    if not code:
        print("no code given", file=sys.stderr)
        return 1
    try:
        token = exchange_code(code, client_id, secret, redirect)
    except httpx.HTTPStatusError as exc:
        print(f"token exchange refused ({exc.response.status_code}): {exc.response.text[:300]}",
              file=sys.stderr)
        return 1
    granted = permissions(token)
    write_env_line(path, "OSM_TOKEN", token)
    print(f"OSM_TOKEN written to {path} (permissions: {', '.join(granted) or 'none'})")
    if "allow_write_api" not in granted:
        print("that token cannot write — the app was authorized without write_api", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
