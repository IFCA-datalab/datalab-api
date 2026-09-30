from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from kubernetes import client

from datalab_api.config import Settings
from datalab_api.main import create_app
from datalab_api.security import create_access_token

JWT_SECRET = "test-secret-with-at-least-32-characters!"
BASE_DOMAIN = "datalab.test"
# Keys produced by the default ``label_prefix``.
OWNER_ANNOTATION = "datalab/created-by"
ERROR_ANNOTATION = "datalab/provisioning-error"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret=JWT_SECRET,
        base_domain=BASE_DOMAIN,
        admin_users=["admin"],
        frontend_url="http://portal.test",
        github_client_id="gh-id",
        github_client_secret="gh-secret",
        github_redirect_uri="http://api.test/auth/github/callback",
        hub_oauth_client_secrets={"ids": "ids-oauth-secret"},
    )


@pytest.fixture
def kube() -> MagicMock:
    fake = MagicMock()
    fake.get_namespace.return_value = None
    fake.core.list_namespace.return_value = client.V1NamespaceList(items=[])
    return fake


@pytest.fixture
def app_client(settings: Settings, kube: MagicMock) -> Iterator[TestClient]:
    app = create_app(settings, kube=kube)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def token_for(settings: Settings) -> Callable[..., dict[str, str]]:
    def make(
        login: str = "alice", sub: str = "1", email: str | None = None
    ) -> dict[str, str]:
        token = create_access_token(
            settings, sub=sub, login=login, email=email, provider="github"
        )
        return {"Authorization": f"Bearer {token}"}

    return make


@pytest.fixture
def mock_http(
    app_client: TestClient,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], None]:
    """Replace the app's shared httpx client with one backed by a handler."""

    def install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        app_client.app.state.http = httpx.AsyncClient(  # type: ignore[attr-defined]
            transport=httpx.MockTransport(handler)
        )

    return install


def make_namespace(
    name: str, owner: str | None = None, **annotations: str
) -> client.V1Namespace:
    if owner:
        annotations[OWNER_ANNOTATION] = owner
    return client.V1Namespace(
        metadata=client.V1ObjectMeta(name=name, annotations=annotations or None),
        status=client.V1NamespaceStatus(phase="Active"),
    )
