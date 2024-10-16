from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, Query, status
from kubernetes import client, config
from kubernetes.client.models.v1_namespace import V1Namespace
from kubernetes.client.models.v1_ingress_tls import V1IngressTLS
from kubernetes.client.rest import ApiException

from sqlalchemy.orm import Session
from typing import Annotated

import httpx
import logging, pathlib
import requests
import yaml

def get_kubecoreapi():
    config.load_kube_config()
    return client.CoreV1Api()

def get_kubeappsapi():
    config.load_kube_config()
    return client.AppsV1Api()

k8s_apps_v1 = get_kubeappsapi()
k8s_core_v1 = get_kubecoreapi()
namespace = "kafka"

router = APIRouter(
    prefix="/deployments/kafka",
)

@router.post("/")
def create_kafka(replicas: int, password: str):
     # Verify that namespace name is a valid type of the deployment type list
    namespace_name = create_kube_namespace(name="kafka") # Check if exists

    # Create the whole Jupyterhub namespace in k8s
    create_serviceAccount()

    try:
        get_current_kubeservices(namespace=namespace_name)
        create_kafka_services()
    except HTTPException:
        print("There is services created in the namespace")

    try:
        # Check if replicas are the same or need to launch a new cluster
        get_current_statefulset()
        create_statefulset(replicas, password)
    except HTTPException:
        print("Kafka cluster running in the project")
    

def create_kube_namespace(name: str):

    v1 = get_kubecoreapi()
    exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Namespace already exists", 
        headers={"WWW-Authenticate": "Bearer"})
    nameSpaceList = v1.list_namespace()
    ### TODO: Create it only if is an acceptable name
    for nameSpace in nameSpaceList.items:
        if nameSpace.metadata.name == name:
            raise exception
            return nameSpace.metadata.name
   
    body = client.V1Namespace(
        metadata=client.V1ObjectMeta(name=name))
    try: 
        api_response = v1.create_namespace(body)
        return api_response.metadata.name
    except ApiException as e:
        print("Exception when calling CoreV1Api->create_namespace: %s\n" % e)


def create_serviceAccount ():

     ## Create the proxy-api service
    serviceaccount = client.V1ServiceAccount()
    serviceaccount.api_version = "v1"
    serviceaccount.kind = "ServiceAccount"
    serviceaccount.metadata = client.V1ObjectMeta(name=namespace)

    k8s_core_v1.create_namespaced_service_account(namespace=namespace,
                                                  body=serviceaccount)

def create_kafka_services():
    namespace = "kafka"
    ## Create the proxy-api service
    service = client.V1Service()
    service.api_version = "v1"
    service.kind = "Service"
    service.metadata = client.V1ObjectMeta(name="kafka-headless", labels={"app":"kafka"})

    spec = client.V1ServiceSpec()
    spec.external_traffic_policy = "Cluster"
    spec.ip_families = ["IPv4"]
    spec.ip_family_policy = "SingleStack"
    
    spec.ports = [client.V1ServicePort(name="tcp-kafka-sasl",
                                       node_port=30092,
                                       protocol="TCP", 
                                       port=9092,
                                       target_port=9092)] 
    spec.selector = {"app": "kafka"}
    spec.type = "NodePort"
    service.spec = spec
    k8s_core_v1.create_namespaced_service(namespace=namespace,
                                          body=service)


#@router.get("/{namespace}/services")
def get_current_kubeservices(namespace: str):
    print("list services from namespace: ", namespace)
    ret = k8s_core_v1.list_namespaced_service(namespace=namespace)
    exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Services already exist", 
        headers={"WWW-Authenticate": "Bearer"})
    if len(ret.items) > 0:
        raise exception
    else:
        return("No services in the namespace")
    

def create_statefulset(replicas, password):

    # spec.pod_management_policy = "Parallel"
    # spec.replicas = replicas # Could be managed via form
    # spec.revision_history_limit = 10
    
    # selector = client.V1LabelSelector()
    # selector.match_labels({"app":"kafka"})
    # spec.selector = selector
    
    container = client.V1Container(command = ["/usr/bin/sh"],
                                   args=["-exc", "export KAFKA_NODE_ID=${HOSTNAME##*-} && export KAFKA_ADVERTISED_LISTENERS=SASL://$HOST_IP:30092;  /usr/bin/rm -rf /var/lib/kafka/data/lost+found; exec /etc/confluent/docker/run" ],  ## CHECK IT
                                   env = [client.V1EnvVar(name="HOST_IP", 
                                   value_from=client.V1EnvVarSource(
                                                field_ref = client.V1ObjectFieldSelector(
                                                    field_path = "status.hostIP"
                                                )          
                                   )),
                    client.V1EnvVar(name="KAFKA_NODE_ID",
                                    value="$KAFKA_NODE_ID"),
                    client.V1EnvVar(name="KAFKA_ADVERTISED_LISTENERS",
                                    value="SASL://$HOST_IP:30092"),
                    client.V1EnvVar(name="KAFKA_HEAP_OPTS",
                                    value="-Xms4g -Xmx4g"),
                    client.V1EnvVar(name="KAFKA_OPTS",
                                    value="-Djavax.net.debug=all"),
                    client.V1EnvVar(name="KAFKA_SASL_ENABLED_MECHANISMS",
                                    value="PLAIN"),
                    client.V1EnvVar(name="KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL",
                                    value="PLAIN"),
                    client.V1EnvVar(name="KAFKA_LISTENER_SECURITY_PROTOCOL_MAP",
                                    value="CONTROLLER:PLAINTEXT,SASL:SASL_PLAINTEXT"),
                    client.V1EnvVar(name="CLUSTER_ID",
                                    value="QZ0WG-zFRYquI54uiCfiTg"),
                    client.V1EnvVar(name="KAFKA_CONTROLLER_QUORUM_VOTERS",
                    #                value="0@kafka-0.kafka-headless.kafka.svc.cluster.local:29093,1@kafka-1.kafka-headless.kafka.svc.cluster.local:29093,2@kafka-2.kafka-headless.kafka.svc.cluster.local:29093"),
                                    value="0@kafka-0.kafka-headless.kafka.svc.cluster.local:29093"),
                    client.V1EnvVar(name="KAFKA_PROCESS_ROLES",
                                    value="broker,controller"),
                    client.V1EnvVar(name="KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR",
                                    value=str(replicas)),
                    client.V1EnvVar(name="KAFKA_NUM_PARTITIONS",
                                    value="3"),
                    client.V1EnvVar(name="KAFKA_DEFAULT_REPLICATION_FACTOR",
                                    value=str(replicas)),
                    client.V1EnvVar(name="KAFKA_LISTENERS",
                                    value="CONTROLLER://0.0.0.0:29093,SASL://0.0.0.0:9092"),
                    client.V1EnvVar(name="KAFKA_INTER_BROKER_LISTENER_NAME",
                                    value="SASL"),
                    client.V1EnvVar(name="KAFKA_CONTROLLER_LISTENER_NAMES",
                                    value="CONTROLLER"),
                    client.V1EnvVar(name="KAFKA_LISTENER_NAME_SASL_PLAIN_SASL_JAAS_CONFIG",
                                    value="org.apache.kafka.common.security.plain.PlainLoginModule required username='admin' password='admin-secret' user_admin='admin-secret' user_kafkaclient1='"+password+"';"),
                    client.V1EnvVar(name="POD_NAME", 
                                    value_from=client.V1EnvVarSource(
                                                field_ref = client.V1ObjectFieldSelector(
                                                    api_version = "v1",
                                                    field_path = "metadata.name"
                                                )          
                                    ))
                    ],
                    name = "kafka",
                    image = "docker.io/confluentinc/cp-kafka:7.6.0",
                    image_pull_policy = "IfNotPresent",
                    liveness_probe = client.V1Probe (failure_threshold=6,
                                               initial_delay_seconds=60,
                                               period_seconds=60,
                                               success_threshold=1,
                                               tcp_socket=client.V1TCPSocketAction(port="tcp-kafka-int"),
                                               timeout_seconds=5),
                    ports = [
                        client.V1ContainerPort(container_port=9092,name="tcp-kafka-sasl",protocol="TCP"),
                        client.V1ContainerPort(container_port=30093,name="tcp-kafka-ctrl",protocol="TCP")
                    ],
                    resources = client.V1ResourceRequirements(limits={"cpu":"2","memory":"4096Mi"},
                                                        requests={"cpu":"250m","memory":"512Mi"}),
                    security_context = client.V1SecurityContext(allow_privilege_escalation=False,
                                                          capabilities=client.V1Capabilities(drop=["ALL"]),
                                                          run_as_group=1000,
                                                          run_as_user=1000),
                    termination_message_path = "/dev/termination-log",
                    termination_message_policy = "File",
                    volume_mounts = [
                        #client.V1VolumeMount(mount_path="/etc/kafka/secrets/", name="kafka-client"),
                        client.V1VolumeMount(mount_path="/etc/kafka/", name="config"),
                        client.V1VolumeMount(mount_path="/var/lib/kafka/data", name="data"),
                        client.V1VolumeMount(mount_path="/var/log", name="logs")
                    ])

    specT = client.V1PodSpec(service_account_name = "kafka",
                            containers = [container],
                            dns_policy = "ClusterFirst",
                            restart_policy = "Always",
                            scheduler_name = "default-scheduler",
                            security_context = client.V1PodSecurityContext(fs_group=1000),
                            termination_grace_period_seconds=30,
                            volumes = [
        client.V1Volume(empty_dir=client.V1EmptyDirVolumeSource(), name="config"),
        client.V1Volume(empty_dir=client.V1EmptyDirVolumeSource(), name="logs")
        #client.V1Volume(empty_dir=client.V1ConfigMapVolumeSource(name="kafka-client"), name="kafka-client"),
    ])
    
    template = client.V1PodTemplateSpec(metadata = client.V1ObjectMeta(labels={"app":"kafka"}),
                                        spec=specT)

    spec = client.V1StatefulSetSpec(pod_management_policy = "Parallel",
                                    replicas = replicas,
                                    revision_history_limit = 10,
                                    selector=client.V1LabelSelector(match_labels={"app":"kafka"}),
                                    service_name="kafka-headless",
                                    template=template,
                                    update_strategy= client.V1StatefulSetUpdateStrategy(type="RollingUpdate"),
                                    volume_claim_templates = [client.V1PersistentVolumeClaim(api_version="v1",
                                       kind="PersistentVolumeClaim",
                                       metadata=client.V1ObjectMeta(name="data"),
                                       spec=client.V1PersistentVolumeClaimSpec(
                                           access_modes=["ReadWriteOnce"],
                                           resources=client.V1VolumeResourceRequirements(
                                               requests={"storage":"10Gi"}
                                           ),
                                           storage_class_name = "cinder-csi",
                                           volume_mode="Filesystem"
                                       ))
    ])

    statefulset = client.V1StatefulSet(api_version = "apps/v1",
                                    kind = "StatefulSet",
                                    metadata = client.V1ObjectMeta(name="kafka", labels={"app":"kafka"},
                                               namespace="kafka"),
                                    spec = spec,
                                    status = client.V1StatefulSetStatus(replicas=replicas)                            
    )

    with open("yamls/kafka/kafka-statefulset.yaml") as f:
        dep = yaml.safe_load(f)
    k8s_apps_v1.create_namespaced_stateful_set(
        namespace="kafka", body=statefulset
    )


def create_ingress():
    networking_v1_api = client.NetworkingV1Api()

    body = client.V1Ingress(
        api_version="networking.k8s.io/v1",
        kind="Ingress",
        metadata=client.V1ObjectMeta(name="ingress", annotations={
            "kubernetes.io/ingress.class": "nginx"}),
        spec = client.V1IngressSpec(
            rules=[client.V1IngressRule(
                host=f"kafka.datalab.ifca.es",
                http=client.V1HTTPIngressRuleValue(
                    paths=[client.V1HTTPIngressPath(
                        path="/",
                        path_type="Prefix",
                        backend=client.V1IngressBackend(
                            service=client.V1IngressServiceBackend(
                                port=client.V1ServiceBackendPort(
                                    number=80,
                                ),
                                name="kafka-headless")
                            )
                    )]
                )
            )],
            tls=[client.V1IngressTLS(
                hosts=[f"kafka.datalab.ifca.es"],
                secret_name="cert-secret"
            )]
        )
    )

    resp = networking_v1_api.create_namespaced_ingress(namespace="kafka",
                                                               body=body)


def get_current_statefulset():
    print("list deployments from namespace: ", namespace)
    ret = k8s_apps_v1.list_namespaced_stateful_set(namespace="kafka")
    exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Kafka statefulset already exist", 
        headers={"WWW-Authenticate": "Bearer"})
    if len(ret.items) > 0:
        raise exception
    else:
        return("No statefulset in the namespace")  

# def get_kafkaInfo():
#     '''
#     Return information about current bootstrap servers to connect from clients
#     '''

# @router.post("/{namespace}/{main_topic}")
# def create_main_topic_per_project():
#     '''
#     Create main topic for streaming data as required
#     '''


# @router.post("/{namespace}/{other_topic}")
# def add_other_topic(namespace: str, other_topic: str):
#     '''
#     Create other topics as required on demand
#     '''


# @router.get(/topics)
# def get_kafkaTopics():