from app.db import User #engine, async_session_maker
#from models import deployment
#from app.schemas import Deployment, DeploymentCreate
from app.users import (
    current_active_user, 
    fastapi_users,
    keycloak_oauth_client
#    github_oauth_client
)
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes import client, config
from kubernetes.client.models.v1_namespace import V1Namespace
from kubernetes.client.rest import ApiException

from sqlalchemy.orm import Session

import httpx
import logging, pathlib
import os
import requests
import yaml

# Inicializa la DB creando las tablas
#deployment.Base.metadata.create_all(bind=engine)

class DeploymentType(str, Enum):
    ids = "IDS"
    climate = "Climate"
    master = "DataScienceHub"
    dummy = "dummy"
    ## Add more
    face = "FACE"

router = APIRouter(
    prefix="/deployments",
)

log = logging.getLogger(__name__)

def get_kubecoreapi():
    config.load_kube_config(config_file=os.environ['KUBECONFIG'])
    return client.CoreV1Api()

def get_kubeappsapi():
    config.load_kube_config(config_file=os.environ['KUBECONFIG'])
    return client.AppsV1Api()

k8s_apps_v1 = get_kubeappsapi()
k8s_core_v1 = get_kubecoreapi()

def create_kube_namespace(name: str):
    v1 = get_kubecoreapi()
    exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Namespace already exists", 
        headers={"WWW-Authenticate": "Bearer"})
    nameSpaceList = v1.list_namespace()
    ### TODO: Create it only if is an acceptable name
    for nameSpace in nameSpaceList.items:
        if nameSpace.metadata.name == "jupyterhub-"+name:
            print("Namespace already exists")
            return nameSpace.metadata.name
   
    body = client.V1Namespace(
        metadata=client.V1ObjectMeta(name="jupyterhub-"+name))
    try: 
        api_response = v1.create_namespace(body)
        return api_response.metadata.name
    except ApiException as e:
        print("Exception when calling CoreV1Api->create_namespace: %s\n" % e)


@router.post("/{namespace}/jupyterhub")
def create_jupyterhub_environment(namespace: str = DeploymentType.dummy):
    """
    Create new jupyterhub environment inside the namespace = {server_name}
    """
    namespace_name = create_kube_namespace(namespace) # Check if exists

    # Check if the services are already created
    try:
        get_current_kubeservices(namespace=namespace_name)
        create_services(namespace_name)
    except HTTPException:
        print("There is services created in the namespace")
    try:
        get_current_kubeproxydeployments(namespace=namespace_name)
        create_proxydeployments(namespace_name)
    except HTTPException:
        print("There is proxy created in the namespace")

    # Create the whole Jupyterhub namespace in k8s
    create_rbac(namespace_name)
    create_configmap(namespace_name)
    create_pvc(namespace_name)
    create_ingress(namespace_name)

    try:
        get_current_kubehubdeployments(namespace=namespace_name)
        create_hubdeployments(namespace_name)
    except HTTPException:
        print("Hub exists in the namespace")

    # if namespace = DeploymentType.IDS:
    #     if check_kafka():
    #         print("Kafka already exists")
    #     else:
    #         create_kafka(namespace)


@router.delete("/{namespace}/jupyterhub")
def delete_jupyterhub_namespace(namespace: str = DeploymentType.dummy):
    return k8s_core_v1.delete_namespace(name=namespace)


def create_services(namespace = DeploymentType.dummy):
    
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
    k8s_core_v1.create_namespaced_service(namespace=namespace,
                                          body=service)

    with open("yamls/proxy/service.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_core_v1.create_namespaced_service(body=dep,
                                                     namespace=namespace)

    ## Create the hub service
    with open("yamls/hub/hub-service.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_core_v1.create_namespaced_service(body=dep,
                                                     namespace=namespace)
        
def create_configmap(namespace: str):
    # Create the configmap hub
    with open("yamls/hub/configmap.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_core_v1.create_namespaced_config_map(body=dep,
                                                     namespace=namespace)
        
def create_pvc(namespace: str):
    with open("yamls/hub/pvc.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_core_v1.create_namespaced_persistent_volume_claim(body=dep,
                                                                     namespace=namespace)
def create_rbac(namespace:str):
    ## Create the proxy-api service
    serviceaccount = client.V1ServiceAccount()
    serviceaccount.api_version = "v1"
    serviceaccount.kind = "ServiceAccount"
    serviceaccount.metadata = client.V1ObjectMeta(name="hub", labels={"component":"jupyter"})

    k8s_core_v1.create_namespaced_service_account(namespace=namespace,
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

    resp = api_instance.create_namespaced_role(namespace=namespace,
                                               body = role)
    
    # Enter a context with an instance of the API kubernetes.client
    with client.ApiClient(config.load_kube_config()) as api_client:
        # Create an instance of the API class
        api_instance = client.RbacAuthorizationV1Api(api_client)

    metadata = client.V1ObjectMeta(name="hub",
                                               labels={"component":"jupyter"})
    subjects = client.V1Subject(kind="ServiceAccount", 
                                            name="hub")
    roleref = client.V1RoleRef(api_group="rbac.authorization.k8s.io", 
                                            kind ="Role", 
                                            name="hub")
    rolebinding = client.V1RoleBinding(metadata = metadata, subjects=[subjects], role_ref=roleref)
    resp = api_instance.create_namespaced_role_binding(namespace=namespace,
                                                       body=rolebinding)
    
def create_ingress(namespace: str):
    networking_v1_api = client.NetworkingV1Api()
    with open("yamls/ingress.yaml") as f:
        dep = yaml.safe_load(f)
        resp = networking_v1_api.create_namespaced_ingress(namespace=namespace,
                                                               body=dep)

def create_proxydeployments(namespace = str):
    ## Create the proxy deployment
    with open("yamls/proxy/proxy-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep, 
                                             namespace=namespace)
        print("Proxy deployment created. status='%s'" % resp.metadata.name)


def create_hubdeployments(namespace = str):
    ## Create the hub deployment
    with open("yamls/hub/hub-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep, 
                                                        namespace=namespace)
        print("Deployment Hub created. status='%s'" % resp)

    ## End: Once the jupyterhub is created the user can create new server

@router.post("/{namespace}/kafka")
def create_kafka(namespace = str):

    ## Create zookeeper service and deployment
    with open("yamls/kafka/zookeeper-service.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_service(body=dep,
                                                     namespace=namespace)
    with open("yamls/kafka/zookeeper-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep,
                                                        namespace=namespace)
    
    print("Zookeper created. status='%s'" % resp)

    ## Create zookeeper service and deployment
    with open("yamls/kafka/broker-service.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_service(body=dep,
                                                     namespace=namespace)
    with open("yamls/kafka/broker-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep,
                                                        namespace=namespace)
    
    print("Zookeper created. status='%s'" % resp)

@router.post("/{namespace}/thredds")
def create_thredds(namespace = str):
    pass

@router.get("/{namespace}/services")
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
    
@router.get("/{namespace}/deployments")   
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


def main():
    """Start server"""
    user = "aidaph"
    hub_url = "https://node.example.org:8000/hub/api"

    session = make_session()
    server_url = start_jupyterhub_server(session)
    print(server_url)

if __name__ == "__main__":
    main()

# @router.post("/deployments", response_model=Deployment)
# async def create_deployment(
#     deployment: client.V1Deployment = Depends(create_deployment_object), 
#     db: Session = Depends(get_db), 
#     api: client.AppsV1Api = Depends(get_kubeapi)):
#     # Create Deployment
#     resp = api.create_namespaced_deployment(
#         body=deployment, namespace="ids"
#     )

#     print("\n[INFO] deployment `ids-deployment` created.\n")
#     print("%s\t%s\t\t\t%s\t%s" % ("NAMESPACE", "NAME", "REVISION", "IMAGE"))
#     print(
#         "%s\t\t%s\t%s\t\t%s\n"
#         % (
#             resp.metadata.namespace,
#             resp.metadata.name,
#             resp.metadata.generation,
#             resp.spec.template.spec.containers[0].image,
#         )
#     )
    


