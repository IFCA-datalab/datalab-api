from app.db import User #engine, async_session_maker
#from models import deployment
#from app.schemas import Deployment, DeploymentCreate
from app.users import (
    current_active_user, 
    fastapi_users,
#    keycloak_oauth_client
    github_oauth_client
)
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes import client, config
from kubernetes.client.models.v1_namespace import V1Namespace
from kubernetes.client.rest import ApiException

from sqlalchemy.orm import Session

import httpx
import logging, pathlib
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

def get_token():
    """boilerplate: get token from share file.
    Make sure to start jupyterhub in this directory first
    """
    here = pathlib.Path(__file__).parent
    token_file = here.joinpath("secret_token")
    log.info(f"Loading token from {token_file}")
    with token_file.open("r") as f:
        token = f.read().strip()
    return token


def make_session():
    """Create a requests.Session with our service token in the Authorization header"""
    token = get_token()
    session = requests.Session()
    session.headers = {"Authorization": f"token {token}"}
    return session

def create_kube_namespace(name: str):
    v1 = get_kubecoreapi()
    exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Namespace already exists", 
        headers={"WWW-Authenticate": "Bearer"})
    nameSpaceList = v1.list_namespace()
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


@router.post("/{namespace}")
def create_jupyterhub_deployment(namespace: str = DeploymentType.dummy):
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

    create_rbac(namespace_name)
    create_configmap(namespace_name)
    create_pvc(namespace_name)

    try:
        get_current_kubehubdeployments(namespace=namespace_name)
        create_hubdeployments(namespace_name)
    except HTTPException:
        print("There is hub created in the namespace")

@router.delete("/{namespace}")
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
@router.post("/{namespace}/rbac")
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
    

def create_proxydeployments(namespace = str):
    ## Create the proxy deployment
    with open("yamls/proxy/proxy-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep, 
                                             namespace=namespace)
        print("Proxy deployment created. status='%s'" % resp.metadata.name)

@router.post("/{namespace}/hub")
def create_hubdeployments(namespace = str):
    ## Create the hub deployment
    with open("yamls/hub/hub-deployment.yaml") as f:
        dep = yaml.safe_load(f)
        resp = k8s_apps_v1.create_namespaced_deployment(body=dep, 
                                                        namespace=namespace)
        print("Deployment Hub created. status='%s'" % resp)

    ## End: Once the jupyterhub is created the user can create new server

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


@router.post("/jupyter/{username}")
# #def start_server(session, hub_url, user, server_name=DeploymentType.ids):
def start_jupyterhub_server(username: str, 
                            server_name: str = "test",
                            session: requests.Session = Depends(make_session)):
    """Start a server in Jupyter Hub for user
    
    Returns the full URL for accessing the server
    """
    user = "aidaph"
    hub_url = "http://localhost:9000/hub/api"

    #session = make_session(get_token())
    user_url = f"{hub_url}/users/{username}"

    # step 1: get user status
    r = requests.get(user_url,
                     headers={
                        'Authorization': f'token {get_token()}',
                     },
                    )

    r.raise_for_status()
    user_model = r.json()

    # If server is not 'active' request launch
    if server_name not in user_model.get("servers", {}):
        log.info(f"Starting Server ")
        r_post = requests.post(user_url +"/server",
                               headers={
                                   'Authorization': f'token {get_token()}',
                               },
                               )
        r_post.raise_for_status()
        print(r_post.json)

    # # step 2: get user status
    r = requests.get(user_url,
                     headers={
                        'Authorization': f'token {get_token()}',
                     },
                    )

    r.raise_for_status()
    user_model = r.json()
    return user_model


@router.get("/{username}")
def deployments_per_user(username: str = None):
    """
    Get deployments/jupyter servers for each user
    """
    hub_url = "http://localhost:9000/hub/api"

    #session = make_session(get_token())
    user_url = f"{hub_url}/users/{username}"

     # step 1: get user status
    r = requests.get(user_url,
                     headers={
                        'Authorization': f'token {get_token()}',
                     },
                    )

    r.raise_for_status()
    user_model = r.json()
    servers = user_model.get("servers", {})

    return servers
