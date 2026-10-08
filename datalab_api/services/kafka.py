"""Provisioning of a KRaft Kafka cluster with SASL/PLAIN authentication.

Every broker has two client listeners:

* ``INTERNAL`` (SASL_PLAINTEXT) on the headless Service, for the brokers
  themselves and for clients inside the cluster.
* ``EXTERNAL`` (SASL_SSL) for clients outside: broker N is advertised as the
  N-th public host on NodePort ``kafka_node_port_base + N``, through a Service
  that only selects that broker's pod. TLS uses the wildcard certificate
  (``tls_secret_name``) replicated into the namespace.
"""

import logging
import secrets

from kubernetes import client
from kubernetes.client.rest import ApiException

from ..config import Settings
from ..k8s import (
    MANAGED_BY_LABEL,
    MANAGED_BY_VALUE,
    KubeClient,
    create_if_absent,
)
from ..schemas import EnvironmentStatus, KafkaCluster

log = logging.getLogger(__name__)

APP_LABELS = {"app": "kafka"}
NAME = "kafka"
HEADLESS_SERVICE = "kafka-headless"
SECRET_NAME = "kafka-credentials"
CLIENT_USERNAME = "kafkaclient1"
INTERNAL_PORT = 9092
EXTERNAL_PORT = 9094
CONTROLLER_PORT = 29093
TLS_DIR = "/etc/kafka-tls"
# Written at startup (key + certificate) because Kafka's PEM keystore wants
# both in a single file.
KEYSTORE_FILE = "/etc/kafka/keystore.pem"
# Fixed so that pods keep their identity across restarts of the StatefulSet.
CLUSTER_ID = "QZ0WG-zFRYquI54uiCfiTg"


class TooManyBrokersError(ValueError):
    pass


def external_addresses(settings: Settings, replicas: int) -> list[str]:
    """``host:port`` that each broker advertises to clients outside the cluster."""
    hosts = settings.kafka_broker_hosts
    if replicas > len(hosts):
        raise TooManyBrokersError(
            f"At most {len(hosts)} brokers (one per public host: {', '.join(hosts)})"
        )
    return [
        f"{host}:{settings.kafka_node_port_base + i}"
        for i, host in enumerate(hosts[:replicas])
    ]


def bootstrap_servers(settings: Settings, replicas: int) -> str:
    # Clamped: a cluster that is still starting (0) or was created with other
    # settings must not break its status page.
    count = min(max(replicas, 1), len(settings.kafka_broker_hosts))
    return ",".join(external_addresses(settings, count))


def quorum_voters(settings: Settings, replicas: int) -> str:
    ns = settings.kafka_namespace
    return ",".join(
        f"{i}@{NAME}-{i}.{HEADLESS_SERVICE}.{ns}.svc.cluster.local:{CONTROLLER_PORT}"
        for i in range(replicas)
    )


def jaas_config() -> str:
    # $(VAR) is expanded by Kubernetes from the variables defined before it,
    # so passwords never appear in the StatefulSet spec.
    return (
        "org.apache.kafka.common.security.plain.PlainLoginModule required "
        'username="admin" password="$(KAFKA_ADMIN_PASSWORD)" '
        'user_admin="$(KAFKA_ADMIN_PASSWORD)" '
        f'user_{CLIENT_USERNAME}="$(KAFKA_CLIENT_PASSWORD)";'
    )


def build_secret(client_password: str) -> client.V1Secret:
    return client.V1Secret(
        metadata=client.V1ObjectMeta(name=SECRET_NAME, labels=APP_LABELS),
        type="Opaque",
        string_data={
            "admin-password": secrets.token_urlsafe(24),
            "client-password": client_password,
        },
    )


def broker_service_name(index: int) -> str:
    return f"{NAME}-{index}-external"


def build_services(settings: Settings, replicas: int) -> list[client.V1Service]:
    headless = client.V1Service(
        metadata=client.V1ObjectMeta(name=HEADLESS_SERVICE, labels=APP_LABELS),
        spec=client.V1ServiceSpec(
            cluster_ip="None",
            publish_not_ready_addresses=True,
            selector=APP_LABELS,
            ports=[
                client.V1ServicePort(
                    name="tcp-internal", port=INTERNAL_PORT, target_port=INTERNAL_PORT
                ),
                client.V1ServicePort(
                    name="tcp-ctrl", port=CONTROLLER_PORT, target_port=CONTROLLER_PORT
                ),
            ],
        ),
    )
    # One Service per broker: clients must reach the exact broker they were
    # told about in the metadata, which a shared Service cannot guarantee.
    brokers = [
        client.V1Service(
            metadata=client.V1ObjectMeta(
                name=broker_service_name(i), labels=APP_LABELS
            ),
            spec=client.V1ServiceSpec(
                type="NodePort",
                selector={
                    **APP_LABELS,
                    "statefulset.kubernetes.io/pod-name": f"{NAME}-{i}",
                },
                ports=[
                    client.V1ServicePort(
                        name="tcp-external",
                        port=EXTERNAL_PORT,
                        target_port=EXTERNAL_PORT,
                        node_port=settings.kafka_node_port_base + i,
                    )
                ],
            ),
        )
        for i in range(replicas)
    ]
    return [headless, *brokers]


def _env(name: str, value: str) -> client.V1EnvVar:
    return client.V1EnvVar(name=name, value=value)


def _secret_env(name: str, key: str) -> client.V1EnvVar:
    return client.V1EnvVar(
        name=name,
        value_from=client.V1EnvVarSource(
            secret_key_ref=client.V1SecretKeySelector(name=SECRET_NAME, key=key)
        ),
    )


def build_statefulset(settings: Settings, replicas: int) -> client.V1StatefulSet:
    ns = settings.kafka_namespace
    env = [
        _secret_env("KAFKA_ADMIN_PASSWORD", "admin-password"),
        _secret_env("KAFKA_CLIENT_PASSWORD", "client-password"),
        # Space-separated; the startup script picks the one of this broker.
        _env("EXTERNAL_ADDRESSES", " ".join(external_addresses(settings, replicas))),
        _env("KAFKA_HEAP_OPTS", "-Xms1g -Xmx3g"),
        _env("KAFKA_SASL_ENABLED_MECHANISMS", "PLAIN"),
        _env("KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL", "PLAIN"),
        _env(
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP",
            "CONTROLLER:PLAINTEXT,INTERNAL:SASL_PLAINTEXT,EXTERNAL:SASL_SSL",
        ),
        _env("CLUSTER_ID", CLUSTER_ID),
        _env("KAFKA_CONTROLLER_QUORUM_VOTERS", quorum_voters(settings, replicas)),
        _env("KAFKA_PROCESS_ROLES", "broker,controller"),
        _env("KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR", str(replicas)),
        _env("KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", str(replicas)),
        _env("KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", str(max(1, replicas - 1))),
        _env("KAFKA_DEFAULT_REPLICATION_FACTOR", str(replicas)),
        _env("KAFKA_NUM_PARTITIONS", "3"),
        _env(
            "KAFKA_LISTENERS",
            f"CONTROLLER://0.0.0.0:{CONTROLLER_PORT},"
            f"INTERNAL://0.0.0.0:{INTERNAL_PORT},"
            f"EXTERNAL://0.0.0.0:{EXTERNAL_PORT}",
        ),
        _env("KAFKA_INTER_BROKER_LISTENER_NAME", "INTERNAL"),
        _env("KAFKA_CONTROLLER_LISTENER_NAMES", "CONTROLLER"),
        _env("KAFKA_LISTENER_NAME_INTERNAL_PLAIN_SASL_JAAS_CONFIG", jaas_config()),
        _env("KAFKA_LISTENER_NAME_EXTERNAL_PLAIN_SASL_JAAS_CONFIG", jaas_config()),
        _env("KAFKA_LISTENER_NAME_EXTERNAL_SSL_KEYSTORE_TYPE", "PEM"),
        _env("KAFKA_LISTENER_NAME_EXTERNAL_SSL_KEYSTORE_LOCATION", KEYSTORE_FILE),
    ]
    # Listener names (INTERNAL/EXTERNAL) rather than protocol names keep the
    # Confluent image from demanding JKS keystores and a JAAS file.
    startup = (
        "export KAFKA_NODE_ID=${HOSTNAME##*-}; "
        "set -- $EXTERNAL_ADDRESSES; shift $KAFKA_NODE_ID; "
        "export KAFKA_ADVERTISED_LISTENERS="
        f"INTERNAL://$HOSTNAME.{HEADLESS_SERVICE}.{ns}.svc.cluster.local:{INTERNAL_PORT},"
        "EXTERNAL://$1; "
        f"cat {TLS_DIR}/tls.key {TLS_DIR}/tls.crt > {KEYSTORE_FILE}; "
        "rm -rf /var/lib/kafka/data/lost+found; "
        "exec /etc/confluent/docker/run"
    )
    container = client.V1Container(
        name="kafka",
        image=settings.kafka_image,
        image_pull_policy="IfNotPresent",
        command=["/bin/sh", "-ec", startup],
        env=env,
        ports=[
            client.V1ContainerPort(container_port=INTERNAL_PORT, name="tcp-internal"),
            client.V1ContainerPort(container_port=EXTERNAL_PORT, name="tcp-external"),
            client.V1ContainerPort(container_port=CONTROLLER_PORT, name="tcp-ctrl"),
        ],
        readiness_probe=client.V1Probe(
            tcp_socket=client.V1TCPSocketAction(port="tcp-internal"),
            initial_delay_seconds=20,
            period_seconds=10,
        ),
        liveness_probe=client.V1Probe(
            tcp_socket=client.V1TCPSocketAction(port="tcp-internal"),
            initial_delay_seconds=60,
            period_seconds=30,
            failure_threshold=6,
            timeout_seconds=5,
        ),
        resources=client.V1ResourceRequirements(
            limits={"cpu": "2", "memory": "4096Mi"},
            requests={"cpu": "250m", "memory": "1536Mi"},
        ),
        security_context=client.V1SecurityContext(
            allow_privilege_escalation=False,
            capabilities=client.V1Capabilities(drop=["ALL"]),
            run_as_non_root=True,
            run_as_user=1000,
            run_as_group=1000,
        ),
        volume_mounts=[
            client.V1VolumeMount(mount_path="/etc/kafka/", name="config"),
            client.V1VolumeMount(mount_path=TLS_DIR, name="tls", read_only=True),
            client.V1VolumeMount(mount_path="/var/lib/kafka/data", name="data"),
            client.V1VolumeMount(mount_path="/var/log", name="logs"),
        ],
    )
    # Spread brokers over nodes so that losing one node keeps the quorum.
    spread = client.V1Affinity(
        pod_anti_affinity=client.V1PodAntiAffinity(
            preferred_during_scheduling_ignored_during_execution=[
                client.V1WeightedPodAffinityTerm(
                    weight=100,
                    pod_affinity_term=client.V1PodAffinityTerm(
                        label_selector=client.V1LabelSelector(match_labels=APP_LABELS),
                        topology_key="kubernetes.io/hostname",
                    ),
                )
            ]
        )
    )
    pod_spec = client.V1PodSpec(
        service_account_name=NAME,
        affinity=spread,
        containers=[container],
        security_context=client.V1PodSecurityContext(fs_group=1000),
        termination_grace_period_seconds=30,
        volumes=[
            client.V1Volume(name="config", empty_dir=client.V1EmptyDirVolumeSource()),
            client.V1Volume(name="logs", empty_dir=client.V1EmptyDirVolumeSource()),
            client.V1Volume(
                name="tls",
                secret=client.V1SecretVolumeSource(
                    secret_name=settings.tls_secret_name
                ),
            ),
        ],
    )
    return client.V1StatefulSet(
        metadata=client.V1ObjectMeta(name=NAME, labels=APP_LABELS),
        spec=client.V1StatefulSetSpec(
            replicas=replicas,
            pod_management_policy="Parallel",
            service_name=HEADLESS_SERVICE,
            selector=client.V1LabelSelector(match_labels=APP_LABELS),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=APP_LABELS), spec=pod_spec
            ),
            volume_claim_templates=[
                client.V1PersistentVolumeClaim(
                    metadata=client.V1ObjectMeta(name="data"),
                    spec=client.V1PersistentVolumeClaimSpec(
                        access_modes=["ReadWriteOnce"],
                        resources=client.V1VolumeResourceRequirements(
                            requests={"storage": settings.kafka_storage_size}
                        ),
                        storage_class_name=settings.kafka_storage_class,
                    ),
                )
            ],
        ),
    )


class KafkaExistsError(Exception):
    pass


def reserve_namespace(kube: KubeClient, settings: Settings, owner: str) -> None:
    body = client.V1Namespace(
        metadata=client.V1ObjectMeta(
            name=settings.kafka_namespace,
            labels={
                MANAGED_BY_LABEL: MANAGED_BY_VALUE,
                settings.type_label: "kafka",
            },
            annotations={settings.owner_annotation: owner},
        )
    )
    try:
        kube.core.create_namespace(body=body)
    except ApiException as exc:
        if exc.status == 409:
            raise KafkaExistsError(settings.kafka_namespace) from exc
        raise


def provision_kafka(
    kube: KubeClient, settings: Settings, replicas: int, client_password: str
) -> None:
    ns = settings.kafka_namespace
    steps = [
        ("secret", kube.core.create_namespaced_secret, build_secret(client_password)),
        (
            "service account",
            kube.core.create_namespaced_service_account,
            client.V1ServiceAccount(
                metadata=client.V1ObjectMeta(name=NAME, labels=APP_LABELS)
            ),
        ),
        *(
            (f"service {svc.metadata.name}", kube.core.create_namespaced_service, svc)
            for svc in build_services(settings, replicas)
        ),
        (
            "statefulset",
            kube.apps.create_namespaced_stateful_set,
            build_statefulset(settings, replicas),
        ),
    ]
    step = "starting"
    try:
        for step, create, body in steps:
            created = create_if_absent(create, namespace=ns, body=body)
            log.info("[%s] %s %s", ns, step, "created" if created else "already exists")
    except Exception as exc:
        log.exception("[%s] kafka provisioning failed at step %r", ns, step)
        reason = exc.reason if isinstance(exc, ApiException) else type(exc).__name__
        try:
            kube.annotate_namespace(
                ns, {settings.error_annotation: f"{step}: {reason}"}
            )
        except ApiException:
            log.exception("[%s] could not record provisioning error", ns)


def describe_kafka(kube: KubeClient, settings: Settings) -> KafkaCluster | None:
    ns_obj = kube.get_namespace(settings.kafka_namespace)
    if ns_obj is None:
        return None
    annotations = ns_obj.metadata.annotations or {}
    error = annotations.get(settings.error_annotation)
    replicas = ready = 0
    try:
        sts = kube.apps.read_namespaced_stateful_set_status(
            NAME, settings.kafka_namespace
        )
        replicas = sts.spec.replicas or 0
        ready = (sts.status.ready_replicas or 0) if sts.status else 0
    except ApiException as exc:
        if exc.status != 404:
            raise

    if ns_obj.status and ns_obj.status.phase == "Terminating":
        state = EnvironmentStatus.deleting
    elif error:
        state = EnvironmentStatus.failed
    elif replicas and ready >= replicas:
        state = EnvironmentStatus.ready
    else:
        state = EnvironmentStatus.provisioning

    return KafkaCluster(
        namespace=settings.kafka_namespace,
        status=state,
        replicas=replicas,
        ready_replicas=ready,
        bootstrap_servers=bootstrap_servers(settings, replicas),
        client_username=CLIENT_USERNAME,
        created_by=annotations.get(settings.owner_annotation),
    )
