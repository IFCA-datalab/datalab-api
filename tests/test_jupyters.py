import base64
from unittest.mock import MagicMock

import httpx
from fastapi.testclient import TestClient
from kubernetes import client

URL = "/deployments/ids/jupyters/alice"


def _with_hub_token(kube: MagicMock) -> None:
    kube.core.read_namespaced_secret.return_value = client.V1Secret(
        data={"api-token": base64.b64encode(b"hub-token").decode()}
    )


def test_cannot_manage_someone_elses_server(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    _with_hub_token(kube)
    assert app_client.post(URL, headers=token_for("bob")).status_code == 403


def test_start_server_creates_hub_user_when_missing(
    app_client: TestClient, kube: MagicMock, mock_http, token_for
) -> None:
    _with_hub_token(kube)
    calls: list[tuple[str, str]] = []

    def hub(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "token hub-token"
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path.endswith("/server"):
            return httpx.Response(202)
        return httpx.Response(201, json={"name": "alice"})

    mock_http(hub)
    response = app_client.post(URL, headers=token_for("alice"))

    assert response.status_code == 202
    assert response.json() == {
        "username": "alice",
        "status": "pending",
        "url": "https://ids.datalab.test/user/alice/",
    }
    assert calls == [
        ("GET", "/hub/api/users/alice"),
        ("POST", "/hub/api/users/alice"),
        ("POST", "/hub/api/users/alice/server"),
    ]


def test_start_running_server_conflicts(
    app_client: TestClient, kube: MagicMock, mock_http, token_for
) -> None:
    _with_hub_token(kube)
    mock_http(lambda _: httpx.Response(200, json={"servers": {"": {"ready": True}}}))
    assert app_client.post(URL, headers=token_for("alice")).status_code == 409


def test_get_server_status(
    app_client: TestClient, kube: MagicMock, mock_http, token_for
) -> None:
    _with_hub_token(kube)
    mock_http(lambda _: httpx.Response(200, json={"servers": {}}))
    body = app_client.get(URL, headers=token_for("alice")).json()
    assert body["status"] == "stopped"


def test_hub_unreachable_is_502(
    app_client: TestClient, kube: MagicMock, mock_http, token_for
) -> None:
    _with_hub_token(kube)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    mock_http(down)
    assert app_client.get(URL, headers=token_for("alice")).status_code == 502


def test_missing_environment_is_404(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    from kubernetes.client.rest import ApiException

    kube.core.read_namespaced_secret.side_effect = ApiException(status=404)
    assert app_client.get(URL, headers=token_for("alice")).status_code == 404
