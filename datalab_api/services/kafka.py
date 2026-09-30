"""Provisioning of a KRaft Kafka cluster with SASL/PLAIN authentication."""

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
EXTERNAL_SERVICE = "kafka-external"
SECRET_NAME = "kafka-credentials"
CLIENT_USERNAME = "kafkaclient1"
SASL_PORT = 9092
CONTROLLER_PORT = 29093
# Fixed so that pods keep their identity across restarts of the StatefulSet.
CLUSTER_ID = "QZ0WG-zFRYquI54uiCfiTg"


def quorum_voters(settings: Settings, replicas: int) -> str:
    ns = settings.kafka_namespace
    return ",".join(
        f"{i}@{NAME}-{i}.{HEADLESS_SERVICE}.{ns}.svc.cluster.local:{CONTROLLER_PORT}"
        for i in range(replicas)
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


def build_services(settings: Settings) -> list[client.V1Service]:
    headless = client.V1Service(
        metadata=client.V1ObjectMeta(name=HEADLESS_SERVICE, labels=APP_LABELS),
        spec=client.V1ServiceSpec(
            cluster_ip="None",
            publish_not_ready_addresses=True,
            selector=APP_LABELS,
            ports=[
                client.V1ServicePort(
                    name="tcp-kafka-sasl", port=SASL_PORT, target_port=SASL_PORT
                ),
                client.V1ServicePort(
                    name="tcp-kafka-ctrl",
                    port=CONTROLLER_PORT,
                    target_port=CONTROLLER_PORT,
                ),
            ],
        ),
    )
    external = client.V1Service(
        metadata=client.V1ObjectMeta(name=EXTERNAL_SERVICE, labels=APP_LABELS),
        spec=client.V1ServiceSpec(
            type="NodePort",
            # Keep traffic on the node that received it, matching the advertised HOST_IP.
            external_traffic_policy="Local",
            selector=APP_LABELS,
            ports=[
                client.V1ServicePort(
                    name="tcp-kafka-sasl",
                    port=SASL_PORT,
                    target_port=SASL_PORT,
                    node_port=settings.kafka_node_port,
                )
            ],
        ),
    )
    return [headless, external]


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
    port = settings.kafka_node_port
    env = [
        client.V1EnvVar(
            name="HOST_IP",
            value_from=client.V1EnvVarSource(
                field_ref=client.V1ObjectFieldSelector(field_path="status.hostIP")
            ),
        ),
        _secret_env("KAFKA_ADMIN_PASSWORD", "admin-password"),
        _secret_env("KAFKA_CLIENT_PASSWORD", "client-password"),
        _env("KAFKA_HEAP_OPTS", "-Xms1g -Xmx3g"),
        _env("KAFKA_SASL_ENABLED_MECHANISMS", "PLAIN"),
        _env("KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL", "PLAIN"),
        _env(
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP",
            "CONTROLLER:PLAINTEXT,SASL:SASL_PLAINTEXT",
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
            f"CONTROLLER://0.0.0.0:{CONTROLLER_PORT},SASL://0.0.0.0:{SASL_PORT}",
        ),
        _env("KAFKA_INTER_BROKER_LISTENER_NAME", "SASL"),
        _env("KAFKA_CONTROLLER_LISTENER_NAMES", "CONTROLLER"),
        # $(VAR) is expanded by Kubernetes from the variables defined above,
        # so passwords never appear in the StatefulSet spec.
        _env(
            "KAFKA_LISTENER_NAME_SASL_PLAIN_SASL_JAAS_CONFIG",
            "org.apache.kafka.common.security.plain.PlainLoginModule required "
            'username="admin" password="$(KAFKA_ADMIN_PASSWORD)" '
            'user_admin="$(KAFKA_ADMIN_PASSWORD)" '
            f'user_{CLIENT_USERNAME}="$(KAFKA_CLIENT_PASSWORD)";',
        ),
    ]
    startup = (
        "export KAFKA_NODE_ID=${HOSTNAME##*-}; "
        f"export KAFKA_ADVERTISED_LISTENERS=SASL://$HOST_IP:{port}; "
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
            client.V1ContainerPort(container_port=SASL_PORT, name="tcp-kafka-sasl"),
            client.V1ContainerPort(
                container_port=CONTROLLER_PORT, name="tcp-kafka-ctrl"
            ),
        ],
        readiness_probe=client.V1Probe(
            tcp_socket=client.V1TCPSocketAction(port="tcp-kafka-sasl"),
            initial_delay_seconds=20,
            period_seconds=10,
        ),
        liveness_probe=client.V1Probe(
            tcp_socket=client.V1TCPSocketAction(port="tcp-kafka-sasl"),
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
            client.V1VolumeMount(mount_path="/var/lib/kafka/data", name="data"),
            client.V1VolumeMount(mount_path="/var/log", name="logs"),
        ],
    )
    pod_spec = client.V1PodSpec(
        service_account_name=NAME,
        containers=[container],
        security_context=client.V1PodSecurityContext(fs_group=1000),
        termination_grace_period_seconds=30,
        volumes=[
            client.V1Volume(name="config", empty_dir=client.V1EmptyDirVolumeSource()),
            client.V1Volume(name="logs", empty_dir=client.V1EmptyDirVolumeSource()),
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


def bootstrap_servers(settings: Settings) -> str:
    return ",".join(
        f"{host}:{settings.kafka_node_port}"
        for host in settings.kafka_public_hosts or ["<node-ip>"]
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
            for svc in build_services(settings)
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
        bootstrap_servers=bootstrap_servers(settings),
        client_username=CLIENT_USERNAME,
    )
