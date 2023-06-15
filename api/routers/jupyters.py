from app.db import User #engine, async_session_maker
from app.users import (
    current_active_user, 
    fastapi_users,
    keycloak_oauth_client
)
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import httpx
import logging, pathlib
import requests
import yaml


router = APIRouter(
    prefix="/deployments/{namespace}/jupyters",
)

log = logging.getLogger(__name__)

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

@router.post("/{username}")
def start_jupyterhub_server(namespace: str,
                            username: str):
    """Start a server in Jupyter Hub for user
    
    Returns the full URL for accessing the server
    """
    ## TODO: adapt the url for each jupyterhub environment
    hub_url = f"http://{namespace}.datalab.ifca.es"
    hub_url_api = f"http://{namespace}.datalab.ifca.es/hub/api"

    #session = make_session(get_token())
    user_url = f"{hub_url_api}/users/{username}"

    # step 1: get user status
    r = requests.get(user_url,
                     headers={
                        'Authorization': f'token {get_token()}',
                     },
                    )

    r.raise_for_status()
    user_model = r.json()

    # If server is not 'active' request launch
    if not user_model.get("servers", {}):
        log.info(f"Starting Server ")
        r_post = requests.post(user_url +"/server",
                               headers={
                                   'Authorization': f'token {get_token()}',
                               },
                               )
        r_post.raise_for_status()
        #print(r_post.json)
    else:
        exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Jupyter server already exists for this user", 
        headers={"WWW-Authenticate": "Bearer"})
        raise exception

    # # step 2: get user status
    r = requests.get(user_url,
                     headers={
                        'Authorization': f'token {get_token()}',
                     },
                    )

    r.raise_for_status()
    user_model = r.json()
    return {"HUB-URL: ": f"{hub_url}/user/{username}"}


@router.get("/{username}")
def deployments_per_user(namespace:str, 
                         username: str = None):
    """
    Get deployments/jupyter servers for each user
    """
    hub_url = f"http://{namespace}.datalab.ifca.es"
    hub_url_api = f"http://{namespace}.datalab.ifca.es/hub/api"

    #session = make_session(get_token())
    user_url = f"{hub_url_api}/users/{username}"

     # step 1: get user status
    r = requests.get(user_url,
                     headers={
                        'Authorization': f'token {get_token()}',
                     },
                    )

    r.raise_for_status()
    user_model = r.json()
    print(user_model)
    servers = user_model.get("servers", {})

    if user_model.get("servers", {}):
        return {"HUB-URL: ": f"{hub_url}/user/{username}"}
    else:
        exception = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
        detail="No servers for user", 
        headers={"WWW-Authenticate": "Bearer"})
        raise exception
