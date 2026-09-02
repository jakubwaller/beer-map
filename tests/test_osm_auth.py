import os
import stat
import urllib.parse

import httpx

from pipeline import osm_auth
from pipeline.osm_auth import (
    OOB, authorize_url, exchange_code, load_env_file, permissions, write_env_line,
)


def test_authorize_url_asks_for_write_api_only():
    url = authorize_url("abc", auth_url="https://osm.test/")
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.netloc == "osm.test"
    assert parsed.path == "/oauth2/authorize"
    assert q == {"response_type": ["code"], "client_id": ["abc"],
                 "redirect_uri": [OOB], "scope": ["write_api"]}


def test_exchange_code_posts_the_authorization_code_grant():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["form"] = urllib.parse.parse_qs(request.content.decode())
        return httpx.Response(200, json={"access_token": "tok", "token_type": "Bearer"})

    token = exchange_code("c0de", "id", "s3cret", auth_url="https://osm.test",
                          transport=httpx.MockTransport(handler))
    assert token == "tok"
    assert seen["path"] == "/oauth2/token"
    assert seen["form"] == {"grant_type": ["authorization_code"], "code": ["c0de"],
                            "redirect_uri": [OOB], "client_id": ["id"],
                            "client_secret": ["s3cret"]}


def test_permissions_reads_what_the_token_may_do():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.url.path == "/api/0.6/permissions.json"
        return httpx.Response(200, json={"version": "0.6", "permissions": ["allow_write_api"]})

    assert permissions("tok", api_url="https://osm.test",
                       transport=httpx.MockTransport(handler)) == ["allow_write_api"]


def test_env_file_round_trip(tmp_path):
    path = str(tmp_path / "osm.env")
    with open(path, "w") as f:
        f.write("# comment\nOSM_CLIENT_ID='id'\nOSM_CLIENT_SECRET=\"s\"\nOSM_TOKEN=old\n\nJUNK\n")
    assert load_env_file(path) == {"OSM_CLIENT_ID": "id", "OSM_CLIENT_SECRET": "s",
                                   "OSM_TOKEN": "old"}
    write_env_line(path, "OSM_TOKEN", "new")
    assert load_env_file(path)["OSM_TOKEN"] == "new"
    with open(path) as f:
        text = f.read()
    assert text.count("OSM_TOKEN=") == 1 and "OSM_CLIENT_ID='id'" in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert load_env_file(str(tmp_path / "missing")) == {}
    assert sorted(os.listdir(tmp_path)) == ["osm.env"]  # no temp file left behind


def test_write_env_line_tightens_a_loose_file(tmp_path):
    path = str(tmp_path / "osm.env")
    with open(path, "w") as f:
        f.write("OSM_CLIENT_ID=id\n")
    os.chmod(path, 0o644)
    write_env_line(path, "OSM_TOKEN", "t")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert load_env_file(path) == {"OSM_CLIENT_ID": "id", "OSM_TOKEN": "t"}


def test_main_writes_the_token_to_the_file_and_never_prints_it(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "osm.env")
    write_env_line(path, "OSM_CLIENT_ID", "id")
    write_env_line(path, "OSM_CLIENT_SECRET", "s3cret")
    monkeypatch.delenv("OSM_TOKEN", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "  c0de \n")
    monkeypatch.setattr(osm_auth, "exchange_code",
                        lambda code, cid, secret, redirect: f"tok-for-{code}-{cid}-{secret}")
    monkeypatch.setattr(osm_auth, "permissions", lambda token: ["allow_write_api"])

    assert osm_auth.main(["--env-file", path]) == 0
    out = capsys.readouterr()
    assert "tok-for-" not in out.out and "tok-for-" not in out.err
    assert "/oauth2/authorize?" in out.out and "client_id=id" in out.out
    assert load_env_file(path)["OSM_TOKEN"] == "tok-for-c0de-id-s3cret"


def test_main_keeps_the_token_when_the_permission_check_fails(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "osm.env")
    write_env_line(path, "OSM_CLIENT_ID", "id")
    write_env_line(path, "OSM_CLIENT_SECRET", "s")
    monkeypatch.setattr("builtins.input", lambda prompt="": "c")
    monkeypatch.setattr(osm_auth, "exchange_code", lambda *a: "tok")

    def boom(token):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(osm_auth, "permissions", boom)
    assert osm_auth.main(["--env-file", path]) == 1
    assert load_env_file(path)["OSM_TOKEN"] == "tok"  # the single-use code is not wasted
    err = capsys.readouterr().err
    assert "written" in err and "tok" not in err.replace("token", "")


def test_main_refuses_to_run_without_client_credentials(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OSM_CLIENT_ID", raising=False)
    monkeypatch.delenv("OSM_CLIENT_SECRET", raising=False)
    assert osm_auth.main(["--env-file", str(tmp_path / "none.env")]) == 2
    assert "OSM_CLIENT_ID" in capsys.readouterr().err


def test_main_flags_a_token_without_write_permission(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "osm.env")
    write_env_line(path, "OSM_CLIENT_ID", "id")
    write_env_line(path, "OSM_CLIENT_SECRET", "s")
    monkeypatch.setattr("builtins.input", lambda prompt="": "c")
    monkeypatch.setattr(osm_auth, "exchange_code", lambda *a: "tok")
    monkeypatch.setattr(osm_auth, "permissions", lambda token: ["allow_read_prefs"])
    assert osm_auth.main(["--env-file", path]) == 1
    assert "write_api" in capsys.readouterr().err
