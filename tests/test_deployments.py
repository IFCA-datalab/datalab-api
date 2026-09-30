from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from kubernetes import client
from kubernetes.client.rest import ApiException

from datalab_api.catalog import DeploymentType
from datalab_api.services import environments as envs

from .conftest import ERROR_ANNOTATION, OWNER_ANNOTATION, make_namespace


def test_healthz(app_client: TestClient) -> None:
    assert app_client.get("/healthz").json() == {"status": "ok"}


def test_types_is_public_and_flags_availability(app_client: TestClient) -> None:
    response = app_client.get("/deployments/types")
    assert response.status_code == 200
    available = {item["type"]: item["available"] for item in response.json()}
    assert available["ids"] and available["dummy"] and available["ipcc"]
    assert available["kafka"]
    assert not available["spark"]


def test_cluster_endpoints_require_auth(app_client: TestClient) -> None:
    assert app_client.get("/deployments/running").status_code == 401
    assert app_client.post("/deployments/dummy/jupyterhub").status_code == 401
    assert app_client.delete("/deployments/dummy/jupyterhub").status_code == 401


def test_invalid_token_is_rejected(app_client: TestClient) -> None:
    response = app_client.get(
        "/deployments/running", headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_running_lists_jupyterhub_namespaces(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    kube.core.list_namespace.return_value = client.V1NamespaceList(
        items=[make_namespace("jupyterhub-ids"), make_namespace("kube-system")]
    )
    response = app_client.get("/deployments/running", headers=token_for())
    assert response.json() == ["ids"]


def test_create_environment_is_async_and_labels_owner(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    response = app_client.post("/deployments/dummy/jupyterhub", headers=token_for())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "provisioning"
    assert body["hub_url"] == "https://dummy.datalab.test"
    ns_body = kube.core.create_namespace.call_args.kwargs["body"]
    assert ns_body.metadata.name == "jupyterhub-dummy"
    assert ns_body.metadata.annotations[OWNER_ANNOTATION] == "github:1"
    # The background task ran and created the hub deployment.
    assert kube.apps.create_namespaced_deployment.call_count == 2


def test_create_existing_environment_conflicts(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    kube.core.create_namespace.side_effect = ApiException(status=409)
    response = app_client.post("/deployments/dummy/jupyterhub", headers=token_for())
    assert response.status_code == 409


def test_create_unknown_or_unavailable_type(app_client: TestClient, token_for) -> None:
    assert (
        app_client.post("/deployments/nope/jupyterhub", headers=token_for()).status_code
        == 422
    )
    assert (
        app_client.post(
            "/deployments/spark/jupyterhub", headers=token_for()
        ).status_code
        == 422
    )


def test_delete_requires_owner_or_admin(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    kube.get_namespace.return_value = make_namespace("jupyterhub-ids", owner="github:1")

    other = app_client.delete(
        "/deployments/ids/jupyterhub", headers=token_for("bob", sub="2")
    )
    assert other.status_code == 403
    kube.core.delete_namespace.assert_not_called()

    owner = app_client.delete("/deployments/ids/jupyterhub", headers=token_for())
    assert owner.status_code == 202
    admin = app_client.delete(
        "/deployments/ids/jupyterhub", headers=token_for("admin", sub="99")
    )
    assert admin.status_code == 202


def test_get_missing_environment_is_404(app_client: TestClient, token_for) -> None:
    assert (
        app_client.get("/deployments/ids/jupyterhub", headers=token_for()).status_code
        == 404
    )


def test_get_environment_reports_failure(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    kube.get_namespace.return_value = make_namespace(
        "jupyterhub-ids", **{ERROR_ANNOTATION: "configmap: Forbidden"}
    )
    body = app_client.get("/deployments/ids/jupyterhub", headers=token_for()).json()
    assert body["status"] == "failed"
    assert body["error"] == "configmap: Forbidden"


def test_kubernetes_errors_become_502(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    kube.core.list_namespace.side_effect = ApiException(status=403, reason="Forbidden")
    response = app_client.get("/deployments/running", headers=token_for())
    assert response.status_code == 502


def test_provision_is_idempotent_and_records_errors(settings, kube: MagicMock) -> None:
    kube.core.create_namespaced_service.side_effect = ApiException(status=409)
    envs.provision_environment(kube, settings, DeploymentType.ids)
    # Existing services are skipped, the rest is still created.
    assert kube.networking.create_namespaced_ingress.called
    # ids mounts a shared volume: hub pvc + shared pvc.
    assert kube.core.create_namespaced_persistent_volume_claim.call_count == 2
    kube.annotate_namespace.assert_called_with(
        "jupyterhub-ids", {ERROR_ANNOTATION: None}
    )

    kube.reset_mock()
    kube.apps.create_namespaced_deployment.side_effect = ApiException(
        status=500, reason="Boom"
    )
    envs.provision_environment(kube, settings, DeploymentType.ids)
    kube.annotate_namespace.assert_called_with(
        "jupyterhub-ids", {ERROR_ANNOTATION: "proxy deployment: Boom"}
    )


def test_hub_secret_is_random_and_includes_oauth_secret(settings) -> None:
    first = envs.build_hub_secret(settings, DeploymentType.ids)["stringData"]
    second = envs.build_hub_secret(settings, DeploymentType.ids)["stringData"]
    assert first["api-token"] != second["api-token"]
    assert first["oauth-client-secret"] == "ids-oauth-secret"
    assert (
        "oauth-client-secret"
        not in envs.build_hub_secret(settings, DeploymentType.dummy)["stringData"]
    )


def test_ingress_is_rendered_per_type(settings) -> None:
    ingress = envs.build_ingress(settings, DeploymentType.climate)
    assert ingress["spec"]["rules"][0]["host"] == "ipcc.datalab.test"
    assert ingress["spec"]["tls"][0]["hosts"] == ["ipcc.datalab.test"]
    # The bundled template is never mutated.
    assert (
        envs.manifest("hub/ingress.yaml")["spec"]["rules"][0]["host"] == "REPLACE_HOST"
    )


def test_auth_is_checked_before_cluster_access(settings) -> None:
    from datalab_api.main import create_app

    app = create_app(settings, kube=None)
    with TestClient(app) as unconfigured:
        app.state.kube = None
        assert unconfigured.get("/deployments/running").status_code == 401


def test_hub_deployment_reads_secrets_and_site_settings(settings) -> None:
    body = envs.build_hub_deployment(settings)
    env = {
        item["name"]: item
        for item in body["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["HUB_BASE_DOMAIN"]["value"] == "datalab.test"
    assert env["KEYCLOAK_REALM_URL"]["value"] == settings.keycloak_realm_url
    # Secrets are referenced from hub-secret, never inlined.
    for name in ("CONFIGPROXY_AUTH_TOKEN", "JPY_COOKIE_SECRET", "DATALAB_API_TOKEN"):
        assert env[name]["valueFrom"]["secretKeyRef"]["name"] == envs.HUB_SECRET_NAME
        assert "value" not in env[name]
