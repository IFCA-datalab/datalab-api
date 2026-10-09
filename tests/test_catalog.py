"""Who sees and creates which service (catalog.yaml)."""

from fastapi.testclient import TestClient

from datalab_api.catalog import DeploymentType, load_service_catalog


def _types(response) -> set[str]:
    assert response.status_code == 200
    return {item["type"] for item in response.json()}


def test_every_deployment_type_is_in_the_catalog() -> None:
    # Types left out of catalog.yaml would silently become admin-only.
    entries = {e.id: e for e in load_service_catalog().entries}
    for deployment_type in DeploymentType:
        assert entries[deployment_type.value].groups, deployment_type


def test_anonymous_users_see_the_whole_catalog(app_client: TestClient) -> None:
    types = _types(app_client.get("/deployments/types"))
    assert {"dummy", "ids", "ipcc", "kafka", "openondemand"} <= types


def test_users_only_see_the_services_of_their_groups(
    app_client: TestClient, token_for
) -> None:
    general = _types(app_client.get("/deployments/types", headers=token_for()))
    assert "dummy" in general and "openondemand" in general
    assert not {"ids", "kafka", "ipcc"} & general

    cyber = _types(
        app_client.get(
            "/deployments/types", headers=token_for(groups=["ciberseguridad"])
        )
    )
    assert {"ids", "kafka", "dummy"} <= cyber and "ipcc" not in cyber


def test_admins_see_everything(app_client: TestClient, token_for) -> None:
    types = _types(app_client.get("/deployments/types", headers=token_for("admin")))
    assert {"ids", "ipcc", "kafka", "dummy"} <= types


def test_links_are_cards_with_a_url(app_client: TestClient, token_for) -> None:
    items = {i["type"]: i for i in app_client.get("/deployments/types").json()}
    ood = items["openondemand"]
    assert ood["kind"] == "link"
    assert ood["available"] is bool(ood["url"])
    assert items["kafka"]["kind"] == "kafka" and items["ids"]["kind"] == "jupyterhub"


def test_creating_outside_your_groups_is_forbidden(
    app_client: TestClient, token_for
) -> None:
    hub = app_client.post("/deployments/ipcc/jupyterhub", headers=token_for())
    assert hub.status_code == 403
    kafka = app_client.post("/deployments/kafka", json={}, headers=token_for())
    assert kafka.status_code == 403
