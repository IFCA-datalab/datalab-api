from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from kubernetes.client.rest import ApiException

from datalab_api.config import Settings
from datalab_api.services import kafka


def test_statefulset_never_contains_passwords(settings: Settings) -> None:
    sts = kafka.build_statefulset(settings, replicas=3)
    container = sts.spec.template.spec.containers[0]
    env = {e.name: e for e in container.env}

    for listener in ("INTERNAL", "EXTERNAL"):
        jaas = env[f"KAFKA_LISTENER_NAME_{listener}_PLAIN_SASL_JAAS_CONFIG"].value
        assert "$(KAFKA_CLIENT_PASSWORD)" in jaas
    # $(VAR) is only expanded for variables defined earlier in the list.
    names = [e.name for e in container.env]
    assert names.index("KAFKA_CLIENT_PASSWORD") < names.index(
        "KAFKA_LISTENER_NAME_EXTERNAL_PLAIN_SASL_JAAS_CONFIG"
    )
    assert (
        env["KAFKA_CLIENT_PASSWORD"].value_from.secret_key_ref.name
        == "kafka-credentials"
    )
    voters = env["KAFKA_CONTROLLER_QUORUM_VOTERS"].value.split(",")
    assert len(voters) == 3
    assert voters[2].startswith("2@kafka-2.kafka-headless.kafka.svc")


def test_external_listener_is_sasl_ssl_with_wildcard_cert(settings: Settings) -> None:
    sts = kafka.build_statefulset(settings, replicas=3)
    pod = sts.spec.template.spec
    env = {e.name: e.value for e in pod.containers[0].env}

    protocols = dict(
        item.split(":")
        for item in env["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"].split(",")
    )
    assert protocols == {
        "CONTROLLER": "PLAINTEXT",
        "INTERNAL": "SASL_PLAINTEXT",
        "EXTERNAL": "SASL_SSL",
    }
    assert env["KAFKA_INTER_BROKER_LISTENER_NAME"] == "INTERNAL"
    assert env["KAFKA_LISTENER_NAME_EXTERNAL_SSL_KEYSTORE_TYPE"] == "PEM"
    tls = next(v for v in pod.volumes if v.name == "tls")
    assert tls.secret.secret_name == settings.tls_secret_name
    assert env["EXTERNAL_ADDRESSES"].split() == [
        "kafka0.datalab.test:30090",
        "kafka1.datalab.test:30091",
        "kafka2.datalab.test:30092",
    ]


def test_each_broker_has_its_own_node_port(settings: Settings) -> None:
    headless, *brokers = kafka.build_services(settings, replicas=3)
    assert headless.spec.cluster_ip == "None"
    assert [s.spec.ports[0].node_port for s in brokers] == [30090, 30091, 30092]
    assert [s.spec.selector["statefulset.kubernetes.io/pod-name"] for s in brokers] == [
        "kafka-0",
        "kafka-1",
        "kafka-2",
    ]


def test_public_hosts_can_be_configured(settings: Settings) -> None:
    settings.kafka_public_hosts = ["a.example.org", "b.example.org"]
    assert (
        kafka.bootstrap_servers(settings, 2)
        == "a.example.org:30090,b.example.org:30091"
    )
    # Status of a cluster bigger than the configured hosts must not fail.
    assert kafka.bootstrap_servers(settings, 3).count(",") == 1


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
    assert body["security_protocol"] == "SASL_SSL"
    assert body["bootstrap_servers"] == (
        "kafka0.datalab.test:30090,kafka1.datalab.test:30091"
    )
    services = [
        c.kwargs["body"].metadata.name
        for c in kube.core.create_namespaced_service.call_args_list
    ]
    assert services == ["kafka-headless", "kafka-0-external", "kafka-1-external"]


def test_create_kafka_validates_input(app_client: TestClient, token_for) -> None:
    payloads = (
        {"replicas": 0},
        {"replicas": 9},
        {"replicas": 4},  # only 3 public hosts
        {"client_password": "short"},
    )
    for payload in payloads:
        response = app_client.post(
            "/deployments/kafka", json=payload, headers=token_for()
        )
        assert response.status_code == 422


def test_get_kafka_404_when_absent(app_client: TestClient, token_for) -> None:
    assert app_client.get("/deployments/kafka", headers=token_for()).status_code == 404


def test_kafka_reports_its_creator(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    from .conftest import make_namespace

    created = app_client.post(
        "/deployments/kafka", json={"replicas": 1}, headers=token_for()
    )
    assert created.json()["created_by"] == "github:1"

    kube.get_namespace.return_value = make_namespace("kafka", owner="github:1")
    kube.apps.read_namespaced_stateful_set_status.side_effect = ApiException(status=404)
    body = app_client.get("/deployments/kafka", headers=token_for()).json()
    assert body["created_by"] == "github:1"


def test_clients_choose_their_user(
    app_client: TestClient, kube: MagicMock, settings: Settings, token_for
) -> None:
    from .conftest import make_namespace

    response = app_client.post(
        "/deployments/kafka",
        json={
            "replicas": 3,
            "client_username": "bbuser",
            "client_password": "A)kfJ1Ob-test",
        },
        headers=token_for(),
    )
    assert response.status_code == 202
    assert response.json()["client_username"] == "bbuser"

    namespace = kube.core.create_namespace.call_args.kwargs["body"]
    annotation = kafka.client_username_annotation(settings)
    assert namespace.metadata.annotations[annotation] == "bbuser"

    sts = kube.apps.create_namespaced_stateful_set.call_args.kwargs["body"]
    env = {e.name: e.value for e in sts.spec.template.spec.containers[0].env}
    jaas = env["KAFKA_LISTENER_NAME_EXTERNAL_PLAIN_SASL_JAAS_CONFIG"]
    assert 'user_bbuser="$(KAFKA_CLIENT_PASSWORD)"' in jaas
    assert "A)kfJ1Ob-test" not in jaas

    kube.get_namespace.return_value = make_namespace(
        "kafka", owner="github:1", **{annotation: "bbuser"}
    )
    kube.apps.read_namespaced_stateful_set_status.side_effect = ApiException(status=404)
    body = app_client.get("/deployments/kafka", headers=token_for()).json()
    assert body["client_username"] == "bbuser"


def test_client_user_and_password_are_validated(
    app_client: TestClient, token_for
) -> None:
    for payload in (
        {"client_username": "admin"},
        {"client_username": "1user"},
        {"client_username": "a b"},
        {"client_password": 'with"quote1234'},
        {"client_password": "with space 1234"},
    ):
        response = app_client.post(
            "/deployments/kafka", json=payload, headers=token_for()
        )
        assert response.status_code == 422, payload
