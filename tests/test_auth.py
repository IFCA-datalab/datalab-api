from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from datalab_api.config import Settings
from datalab_api.security import create_access_token, decode_access_token


def test_token_roundtrip_and_admin_flag(settings: Settings) -> None:
    token = create_access_token(settings, sub="7", login="admin", provider="github")
    user = decode_access_token(settings, token)
    assert user.sub == "github:7"
    assert user.is_admin


def test_token_signed_with_other_secret_is_rejected(settings: Settings) -> None:
    other = Settings(_env_file=None, jwt_secret="x" * 40)
    token = create_access_token(other, sub="7", login="eve", provider="github")
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(settings, token)
    assert exc_info.value.status_code == 401


def test_users_me(app_client: TestClient, token_for) -> None:
    body = app_client.get("/users/me", headers=token_for("alice")).json()
    assert body["login"] == "alice"
    assert body["is_admin"] is False


def test_keycloak_disabled_when_not_configured(app_client: TestClient) -> None:
    response = app_client.get("/auth/keycloak/login", follow_redirects=False)
    assert response.status_code == 404


def test_github_login_sets_state_cookie(app_client: TestClient) -> None:
    response = app_client.get("/auth/github/login", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"][0] == response.cookies["datalab_oauth_state"]


def test_github_callback_rejects_bad_state(app_client: TestClient) -> None:
    app_client.cookies.set("datalab_oauth_state", "expected")
    response = app_client.get(
        "/auth/github/callback?code=c&state=forged", follow_redirects=False
    )
    assert response.status_code == 400


def test_github_callback_issues_token_in_fragment(
    app_client: TestClient, mock_http, settings: Settings
) -> None:
    def github(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gh-token"})
        assert request.headers["authorization"] == "Bearer gh-token"
        return httpx.Response(200, json={"id": 42, "login": "octo", "avatar_url": "a"})

    mock_http(github)
    app_client.cookies.set("datalab_oauth_state", "s1")
    response = app_client.get(
        "/auth/github/callback?code=c&state=s1", follow_redirects=False
    )

    assert response.status_code == 307
    location = urlsplit(response.headers["location"])
    assert location.query == ""
    params = parse_qs(location.fragment)
    assert params["user"] == ["octo"]
    user = decode_access_token(settings, params["token"][0])
    assert user.sub == "github:42"


def test_keycloak_callback(settings: Settings, kube) -> None:
    from datalab_api.main import create_app

    kc = Settings(
        _env_file=None,
        jwt_secret=settings.jwt_secret.get_secret_value(),
        frontend_url="http://portal.test",
        keycloak_url="https://kc.test",
        keycloak_client_id="datalab-api",
        keycloak_client_secret="kc-secret",
        keycloak_callback_url="http://api.test/auth/keycloak/callback",
    )

    def keycloak(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "kc-token"})
        return httpx.Response(
            200,
            json={
                "sub": "abc",
                "preferred_username": "ana",
                "email": "ana@x.es",
                "groups": ["clima", "/ciberseguridad"],
            },
        )

    with TestClient(create_app(kc, kube=kube)) as client:
        client.app.state.keycloak_http = httpx.AsyncClient(
            transport=httpx.MockTransport(keycloak)
        )
        client.cookies.set("datalab_oauth_state", "s1")
        response = client.get(
            "/auth/keycloak/callback?code=c&state=s1", follow_redirects=False
        )

    params = parse_qs(urlsplit(response.headers["location"]).fragment)
    user = decode_access_token(kc, params["token"][0])
    assert user.sub == "keycloak:abc"
    assert user.can_act_as("ana@x.es")
    # Group names, with or without the "full path" of the Keycloak mapper.
    assert user.groups == ["ciberseguridad", "clima"]


def test_empty_env_vars_leave_integrations_disabled(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "y" * 40)
    monkeypatch.setenv("KEYCLOAK_URL", "https://kc.test")
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "datalab-api")
    monkeypatch.setenv("KEYCLOAK_CLIENT_SECRET", "")
    monkeypatch.setenv("KEYCLOAK_CALLBACK_URL", "http://api.test/cb")
    monkeypatch.setenv("ADMIN_USERS", "ana, root@x.es")
    monkeypatch.setenv("HUB_OAUTH_CLIENT_SECRETS", '{"ids": "s"}')
    loaded = Settings(_env_file=None)
    assert not loaded.keycloak_enabled
    assert loaded.admin_users == ["ana", "root@x.es"]
    assert loaded.hub_oauth_client_secrets["ids"].get_secret_value() == "s"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("False", False),
        ("0", False),
        ("/etc/ssl/ca.pem", "/etc/ssl/ca.pem"),
    ],
)
def test_keycloak_verify_tls_parses_booleans(monkeypatch, raw, expected) -> None:
    # "false" must disable verification, not be taken as a CA bundle path.
    monkeypatch.setenv("JWT_SECRET", "y" * 40)
    monkeypatch.setenv("KEYCLOAK_VERIFY_TLS", raw)
    assert Settings(_env_file=None).keycloak_verify_tls == expected
