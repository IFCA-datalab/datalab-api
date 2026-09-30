"""Shared Kafka cluster (KRaft, SASL/PLAIN) in its own namespace."""

import secrets

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from ..k8s import KubeDep
from ..schemas import EnvironmentStatus, KafkaCluster, KafkaCreate, KafkaCredentials
from ..security import SettingsDep, UserDep, ensure_can_manage
from ..services import kafka as kafka_svc

router = APIRouter(prefix="/deployments/kafka", tags=["kafka"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"description": "A Kafka cluster already exists"}},
)
def create_kafka(
    user: UserDep,
    body: KafkaCreate,
    background: BackgroundTasks,
    kube: KubeDep,
    settings: SettingsDep,
) -> KafkaCredentials:
    """Start provisioning the Kafka cluster.

    The client password is returned **only in this response**; it is stored in
    the ``kafka-credentials`` Secret of the cluster namespace.
    """
    password = (
        body.client_password.get_secret_value()
        if body.client_password
        else secrets.token_urlsafe(18)
    )
    try:
        kafka_svc.reserve_namespace(kube, settings, owner=user.sub)
    except kafka_svc.KafkaExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A Kafka cluster already exists"
        ) from None

    background.add_task(
        kafka_svc.provision_kafka, kube, settings, body.replicas, password
    )
    return KafkaCredentials(
        namespace=settings.kafka_namespace,
        status=EnvironmentStatus.provisioning,
        replicas=body.replicas,
        ready_replicas=0,
        bootstrap_servers=kafka_svc.bootstrap_servers(settings),
        client_username=kafka_svc.CLIENT_USERNAME,
        client_password=password,
    )


@router.get("")
def get_kafka(
    _: UserDep,
    kube: KubeDep,
    settings: SettingsDep,
) -> KafkaCluster:
    """Status and connection details of the Kafka cluster."""
    cluster = kafka_svc.describe_kafka(kube, settings)
    if cluster is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no Kafka cluster")
    return cluster


@router.delete("", status_code=status.HTTP_202_ACCEPTED)
def delete_kafka(
    user: UserDep,
    kube: KubeDep,
    settings: SettingsDep,
) -> None:
    """Delete the Kafka cluster, including its data volumes."""
    namespace = kube.get_namespace(settings.kafka_namespace)
    if namespace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no Kafka cluster")
    ensure_can_manage(user, namespace, settings)
    kube.core.delete_namespace(settings.kafka_namespace)
