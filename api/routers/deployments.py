from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
YAMLS_DIR = BASE_DIR / "yamls/"

from ..app.db import User #engine, async_session_maker
#from models import deployment
#from app.schemas import Deployment, DeploymentCreate
from ..app.users import (
    current_active_user, 
    fastapi_users,
#    keycloak_oauth_client
#    github_oauth_client
)
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, Query, status
from kubernetes import client, config
from kubernetes.client.models.v1_namespace import V1Namespace
from kubernetes.client.rest import ApiException
from pydantic import BaseModel

from sqlalchemy.orm import Session
import time
from typing import Annotated

import httpx
import logging, pathlib
import requests
import yaml

# Inicializa la DB creando las tablas
#deployment.Base.metadata.create_all(bind=engine)

class DeploymentType(str, Enum):
    ids = "ids"
    climate = "ipcc"
    master = "datasciencehub"
    dummy = "dummy"
    ## Add more
    #face = "FACE"
    kafka = "kafka"
    spark = "spark"
    #thredds = "thredds"


class DeploymentTypeInfo(BaseModel):
    type: str
    label: str
    description: str
    icon: str

DEPLOYMENT_TYPE_INFO = {
    DeploymentType.ids: {
        "label": "IDS",
        "description": "Entorno orientado al análisis y visualización de datos de ciberseguridad.",
        "icon": "📊",
    },
    DeploymentType.climate: {
        "label": "Climate",
        "description": "Entorno para análisis de datos climáticos y experimentación científica.",
        "icon": "🌍",
    },
    DeploymentType.master: {
        "label": "Data Science Hub",
        "description": "Entorno generalista para el Máster de Ciencia de Datos, con herramientas y datasets variados.",
        "icon": "📈",
    },
    DeploymentType.dummy: {
        "label": "Dummy",
        "description": "Entorno de prueba para validación funcional y despliegues de demostración.",
        "icon": "🧪",
    },
    DeploymentType.kafka: {
        "label": "Kafka",
        "description": "Entorno orientado a mensajería, streaming y pruebas con brokers Kafka.",
        "icon": "📨",
    },
    DeploymentType.spark: {
        "label": "Spark",
        "description": "Entorno para procesamiento distribuido y analítica sobre Apache Spark.",
        "icon": "⚡",
    },
}

router = APIRouter(
    prefix="/deployments",
)

log = logging.getLogger(__name__)

def get_kubecoreapi():
    config.load_kube_config()
    return client.CoreV1Api()

def get_kubeappsapi():
    config.load_kube_config()
    return client.AppsV1Api()

k8s_apps_v1 = get_kubeappsapi()
k8s_core_v1 = get_kubecoreapi()

def valid_deployment_types() -> list[str]:
    return [item.value for item in DeploymentType]


def ensure_valid_deployment_type(deployment_type: str) -> None:
    if deployment_type not in valid_deployment_types():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deployment name is not valid, please choose a valid type",
        )


def build_k8s_namespace(deployment_type: str) -> str:
    return f"jupyterhub-{deployment_type}"


def create_kube_namespace(deployment_type: str) -> str:

    v1 = get_kubecoreapi()

    k8s_namespace = build_k8s_namespace(deployment_type)

    existing = v1.list_namespace()
    for item in existing.items:
        if item.metadata.name == k8s_namespace:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Namespace already exists",
                headers={"WWW-Authenticate": "Bearer"},
            )

    body = client.V1Namespace(
        metadata=client.V1ObjectMeta(name=k8s_namespace)
    )

    try:
        response = v1.create_namespace(body=body)
        return response.metadata.name
    except ApiException as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating namespace: {e}",
        ) from e


@router.get("/types", response_model=list[DeploymentTypeInfo])
def get_deployment_types():
    return [
        DeploymentTypeInfo(
            type=deployment_type.value,
            label=DEPLOYMENT_TYPE_INFO[deployment_type]["label"],
            description=DEPLOYMENT_TYPE_INFO[deployment_type]["description"],
            icon=DEPLOYMENT_TYPE_INFO[deployment_type]["icon"],
        )
        for deployment_type in DeploymentType
    ]

@router.get("/running")
def get_running_jupyterhubs():
    """
    Get current Jupyterhub environments running
    """
    v1 = get_kubecoreapi()
    exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Namespace already exists", 
        headers={"WWW-Authenticate": "Bearer"})
    nameSpaceList = v1.list_namespace()
    ### TODO: Create it only if is an acceptable name
    deploys = []
    for name in nameSpaceList.items:
        if "jupyterhub-" in name.metadata.name:
            n = name.metadata.name.replace("jupyterhub-", "")
            deploys.append(n)
    return deploys 


@router.post("/{namespace}/jupyterhub")
def create_jupyterhub_environment(namespace: DeploymentType = DeploymentType.dummy) -> dict[str, Any]:
    """
    Create new jupyterhub environment inside the namespace = {server_name}

    Valid types = dummy, ids, ipcc, datasciencehub, kakfa, spark
    """
    deployment_value = namespace.value
    ensure_valid_deployment_type(deployment_value)
    k8s_namespace = create_kube_namespace(deployment_value)

    # Check if the services are already created
    try:
        get_current_kubeservices(namespace=k8s_namespace)
        create_services(deployment_value, k8s_namespace)
    except HTTPException:
        print("There is services created in the namespace")
    try:
        get_current_kubeproxydeployments(namespace=k8s_namespace)
        create_proxydeployments(k8s_namespace)
    except HTTPException:
        print("There is proxy created in the namespace")

    # Create the whole Jupyterhub namespace in k8s
    create_rbac(deployment_value,k8s_namespace)
    create_configmap(deployment_value,k8s_namespace)
    create_pvc(deployment_value,k8s_namespace)
    create_ingress(deployment_value,k8s_namespace)
    wait_for_pvc_bound(k8s_namespace, "hub-db-dir")

    try:
        get_current_kubehubdeployments(namespace=k8s_namespace)
        create_hubdeployments(k8s_namespace)
    except HTTPException:
        print("Hub exists in the namespace")

    url = f"https://{deployment_value}.es"
    return {"datalab-url: ": f"{url}"}

@router.delete("/{namespace}/jupyterhub")
def delete_jupyterhub_namespace(namespace: str = DeploymentType.dummy):
    return k8s_core_v1.delete_namespace(name="jupyterhub-"+namespace)


@router.get("/{namespace}/jupyterhub")
def get_url_jupyterhub_namespace(namespace: str = DeploymentType.dummy):
    """
    Get info of the jupyterhub environment

    Valid types = dummy, ids, ipcc, datasciencehub, face
    """
    deployment_value = namespace.value

    return {
        "hubUrl":
            f"https://{deployment_value}.es"
    }

def create_services(deployment_type: str, k8s_namespace: str) -> dict[str, Any]:
    
    ## Create the proxy-api service
    service = client.V1Service()
    service.api_version = "v1"
    service.kind = "Service"
    service.metadata = client.V1ObjectMeta(name="proxy-api")

    spec = client.V1ServiceSpec()
    spec.selector = {"component": "proxy"}
    spec.ports = [client.V1ServicePort(protocol="TCP", 
                                       port=8001,
                                       target_port=8001)]
    service.spec = spec
    k8s_core_v1.create_namespaced_service(namespace=k8s_namespace,
                                          body=service)

    with open(YAMLS_DIR / "proxy" / "service.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_core_v1.create_namespaced_service(body=dep,
                                                     namespace=k8s_namespace)

    ## Create the hub service
    with open(YAMLS_DIR / "hub" / "hub-service.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_core_v1.create_namespaced_service(body=dep,
                                                     namespace=k8s_namespace)

def create_configmap(deployment_type: str, k8s_namespace: str) -> dict[str, Any]:
    # Create the configmap hub
    with open(YAMLS_DIR / "hub" / "configmaps" / f"configmap-{deployment_type}.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_core_v1.create_namespaced_config_map(body=dep,
                                                     namespace=k8s_namespace)


def create_pvc(deployment_type: str, k8s_namespace: str) -> dict[str, Any]:
    with open(YAMLS_DIR / "hub" / "pvc.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_core_v1.create_namespaced_persistent_volume_claim(body=dep,
                                                                     namespace=k8s_namespace)


def create_rbac(deployment_value: str, k8s_namespace: str):
    ## Create the proxy-api service
    serviceaccount = client.V1ServiceAccount()
    serviceaccount.api_version = "v1"
    serviceaccount.kind = "ServiceAccount"
    serviceaccount.metadata = client.V1ObjectMeta(name="hub", labels={"component":"jupyter"})

    k8s_core_v1.create_namespaced_service_account(namespace=k8s_namespace,
                                                  body=serviceaccount)
    
    # Enter a context with an instance of the API kubernetes.client
    with client.ApiClient(config.load_kube_config()) as api_client:
        # Create an instance of the API class
        api_instance = client.RbacAuthorizationV1Api(api_client)
    role = client.V1Role()
    role.api_version = "rbac.authorization.k8s.io/v1"
    role.metadata = client.V1ObjectMeta(name="hub", labels={"component":"jupyter"})

    apigroup = [""]
    resources1 = ["pods", "persistentvolumeclaims"]
    resources2 = ["events"]

    verb1 = ["get", "watch", "list", "create", "delete"]
    verb2 = ["get", "watch", "list"]
    rule1 = client.V1PolicyRule(api_groups=apigroup, resources=resources1, verbs=verb1)
    rule2 = client.V1PolicyRule(api_groups=apigroup, resources=resources2, verbs=verb2)
    role.rules = [rule1, rule2]

    resp = api_instance.create_namespaced_role(namespace=k8s_namespace,
                                               body = role)
    
    # Enter a context with an instance of the API kubernetes.client
    with client.ApiClient(config.load_kube_config()) as api_client:
        # Create an instance of the API class
        api_instance = client.RbacAuthorizationV1Api(api_client)

    metadata = client.V1ObjectMeta(name="hub",
                                               labels={"component":"jupyter"})
    subjects = client.RbacV1Subject(kind="ServiceAccount", 
                                            name="hub")
    roleref = client.V1RoleRef(api_group="rbac.authorization.k8s.io", 
                                            kind ="Role", 
                                            name="hub")
    rolebinding = client.V1RoleBinding(metadata = metadata, subjects=[subjects], role_ref=roleref)
    resp = api_instance.create_namespaced_role_binding(namespace=k8s_namespace,
                                                       body=rolebinding)
    

def create_ingress(deployment_type: str, k8s_namespace: str):
    manifest = yaml.safe_load((YAMLS_DIR / "hub" / "ingress.yaml").read_text())

    manifest.setdefault("metadata", {})
    manifest["metadata"]["namespace"] = k8s_namespace
    manifest["metadata"]["name"] = "jupyterhub"

    manifest["spec"]["rules"][0]["host"] = f"{deployment_type}.datalab.example.org"
    manifest["spec"]["tls"][0]["hosts"][0] = f"{deployment_type}.datalab.example.org"
    manifest["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] = "proxy-public"
    manifest["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"]["number"] = 80

    networking_v1 = client.NetworkingV1Api()
    return networking_v1.create_namespaced_ingress(
        namespace=k8s_namespace,
        body=manifest,
    )


def create_proxydeployments(namespace = str):
    ## Create the proxy deployment

    with open(YAMLS_DIR / "proxy" / "proxy-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep, 
                                             namespace=namespace)
        print("Proxy deployment created. status='%s'" % resp.metadata.name)


def create_hubdeployments(namespace = str):
    ## Create the hub deployment
    with open(YAMLS_DIR / "hub" / "hub-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep, 
                                                        namespace=namespace)
        print("Deployment Hub created. status='%s'" % resp)

    ## End: Once the jupyterhub is created the user can create new server


def wait_for_pvc_bound(namespace: str, pvc_name: str, timeout: int = 120, interval: int = 3) -> None:
    v1 = get_kubecoreapi()
    deadline = time.time() + timeout

    while time.time() < deadline:
        pvc = v1.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
        phase = pvc.status.phase

        if phase == "Bound":
            return

        time.sleep(interval)

    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail=f"PVC {pvc_name} in namespace {namespace} did not reach Bound state in time",
    )

@router.post("/{namespace}/kafka")
def create_kafka(namespace = str):

    pvc = client.V1PersistentVolumeClaim(metadata=client.V1ObjectMeta(name=namespace+"-data-shared",
                                                        namespace="jupyterhub-"+namespace),
                                    spec=client.V1PersistentVolumeClaimSpec(
                                    access_modes=["ReadWriteMany"],
                                    resources=client.V1VolumeResourceRequirements(requests={"storage":"100Gi"}),
                                    storage_class_name="longhorn",
                                    volume_mode="Filesystem"),
                                status=client.V1PersistentVolumeClaimStatus(access_modes=[])
    )
    k8s_core_v1.create_namespaced_persistent_volume_claim(namespace="jupyterhub-"+namespace, 
                                                        body=pvc)              

    with open(YAMLS_DIR + "hub" + "/hub-deployment.yaml", "r") as f:
        dep = yaml.safe_load(f)
    for container in dep["spec"]["template"]["spec"]["containers"]:
        if "env" in container:
            container["env"].append({
                "name": "NAMESPACE",
                "value": namespace
            })
            container["env"].append({
                "name": "ACCESS_TOKEN",
                "value": token
            })
    # Write the updated YAML data back to the file
    with open(YAMLS_DIR + "hub" + "/hub-deployment.yaml", 'w') as file:
        yaml.dump(dep, file)

    with open(YAMLS_DIR + "hub" + "/hub-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep,
                                                        namespace=namespace)
    
    print("Zookeper created. status='%s'" % resp)

    resp = k8s_apps_v1.create_namespaced_deployment(body=dep,
                                                    namespace="jupyterhub-"+namespace)
    for container in dep["spec"]["template"]["spec"]["containers"]:
        if "env" in container:
            print("Try to remove container %s'" % container)
            container["env"].remove({
                "name": "NAMESPACE",
                "value": namespace
            })
            container["env"].remove({
                "name": "ACCESS_TOKEN",
                "value": token
            })

    # Write the updated YAML data back to the file
    with open(YAMLS_DIR + "hub" + "/hub-deployment.yaml", 'w') as file:
        yaml.dump(dep, file)


@router.post("/{namespace}/thredds")
def create_thredds(namespace = str):
    pass

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


def get_current_kubeproxydeployments(namespace: str):
    print("list deployments from namespace: ", namespace)
    ret = k8s_apps_v1.list_namespaced_deployment(namespace=namespace)
    exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Proxy deployment already exist", 
        headers={"WWW-Authenticate": "Bearer"})
    print(ret.items)
    for i in range(len(ret.items)):
        if  ret.items[i].metadata.name == "proxy":
            raise exception
        else:
            continue
    return("No proxy in the namespace")
    
#@router.get("/{namespace}/deployments")   
def get_current_kubehubdeployments(namespace: str):
    print("list deployments from namespace: ", namespace)
    ret = k8s_apps_v1.list_namespaced_deployment(namespace=namespace)
    exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Hub deployment already exist", 
        headers={"WWW-Authenticate": "Bearer"})
    for i in range(len(ret.items)):
        if  ret.items[i].metadata.name == "hub":
            raise exception
        else:
            return("No hub in the namespace")
