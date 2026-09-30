"""Single-user Jupyter servers inside a JupyterHub environment."""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from ..catalog import DeploymentType, namespace_for
from ..k8s import KubeDep
from ..schemas import JupyterServer, ServerStatus
from ..security import SettingsDep, UserDep
from ..services.environments import get_hub_api_token, hub_url
from ..services.hub_api import HubAPI

router = APIRouter(prefix="/deployments/{deployment_type}/jupyters", tags=["jupyters"])


async def get_hub(
    deployment_type: DeploymentType,
    request: Request,
    kube: KubeDep,
    settings: SettingsDep,
) -> HubAPI:
    namespace = namespace_for(deployment_type)
    token = await run_in_threadpool(get_hub_api_token, kube, settings, namespace)
    if token is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Environment '{deployment_type}' does not exist or has no API token",
        )
    http: httpx.AsyncClient = request.app.state.http
    api_url = settings.hub_api_url_template.format(
        name=deployment_type.value,
        namespace=namespace,
        base_domain=settings.base_domain,
    )
    return HubAPI(http, api_url, hub_url(settings, deployment_type), token)


HubDep = Annotated[HubAPI, Depends(get_hub)]


def _authorize(
    user: UserDep,
    username: str,
) -> None:
    if not user.can_act_as(username):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You can only manage your own Jupyter server"
        )


@router.get("/{username}")
async def get_server(
    user: UserDep,
    username: str,
    hub: HubDep,
) -> JupyterServer:
    """Status and URL of the user's Jupyter server."""
    _authorize(user, username)
    return await hub.get_server(username)


@router.post(
    "/{username}",
    status_code=status.HTTP_202_ACCEPTED,
    responses={201: {"description": "Server started immediately"}},
)
async def start_server(
    user: UserDep,
    username: str,
    hub: HubDep,
    response: Response,
) -> JupyterServer:
    """Start the user's Jupyter server (creating the hub user if needed)."""
    _authorize(user, username)
    server = await hub.start_server(username)
    if server.status is ServerStatus.running:
        response.status_code = status.HTTP_201_CREATED
    return server


@router.delete("/{username}", status_code=status.HTTP_202_ACCEPTED)
async def stop_server(
    user: UserDep,
    username: str,
    hub: HubDep,
) -> None:
    """Stop the user's Jupyter server. Their data volume is kept."""
    _authorize(user, username)
    await hub.stop_server(username)
