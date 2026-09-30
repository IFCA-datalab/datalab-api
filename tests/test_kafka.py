from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from datalab_api.config import Settings
from datalab_api.services import kafka


def test_statefulset_never_contains_passwords(settings: Settings) -> None:
    sts = kafka.build_statefulset(settings, replicas=3)
    container = sts.spec.template.spec.containers[0]
    env = {e.name: e for e in container.env}

    jaas = env["KAFKA_LISTENER_NAME_SASL_PLAIN_SASL_JAAS_CONFIG"].value
    assert "$(KAFKA_CLIENT_PASSWORD)" in jaas
    assert (
        env["KAFKA_CLIENT_PASSWORD"].value_from.secret_key_ref.name
        == "kafka-credentials"
    )
    voters = env["KAFKA_CONTROLLER_QUORUM_VOTERS"].value.split(",")
    assert len(voters) == 3
    assert voters[2].startswith("2@kafka-2.kafka-headless.kafka.svc")


def test_headless_service_for_statefulset_dns(settings: Settings) -> None:
    headless, external = kafka.build_services(settings)
    assert headless.spec.cluster_ip == "None"
    assert external.spec.ports[0].node_port == 30092


def test_create_kafka_returns_password_once(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    response = app_client.post(
        "/deployments/kafka", json={"replicas": 2}, headers=token_for()
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body["client_password"]) >= 16
    secret = kube.core.create_namespaced_secret.call_args.kwargs["body"]
    assert secret.string_data["client-password"] == body["client_password"]
    assert kube.apps.create_namespaced_stateful_set.called


def test_create_kafka_validates_input(app_client: TestClient, token_for) -> None:
    for payload in ({"replicas": 0}, {"replicas": 9}, {"client_password": "short"}):
        response = app_client.post(
            "/deployments/kafka", json=payload, headers=token_for()
        )
        assert response.status_code == 422


def test_get_kafka_404_when_absent(app_client: TestClient, token_for) -> None:
    assert app_client.get("/deployments/kafka", headers=token_for()).status_code == 404
