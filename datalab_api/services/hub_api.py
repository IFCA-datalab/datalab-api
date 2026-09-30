"""Async client for the JupyterHub REST API of a DataLab environment."""

import logging
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException, status

from ..schemas import JupyterServer, ServerStatus

log = logging.getLogger(__name__)


class HubAPI:
    def __init__(
        self, http: httpx.AsyncClient, api_url: str, public_url: str, token: str
    ) -> None:
        self._http = http
        self._api_url = api_url.rstrip("/")
        self._public_url = public_url.rstrip("/")
        self._headers = {"Authorization": f"token {token}"}

    async def _request(self, method: str, path: str) -> httpx.Response:
        try:
            response = await self._http.request(
                method, f"{self._api_url}{path}", headers=self._headers
            )
        except httpx.HTTPError as exc:
            log.warning("JupyterHub unreachable at %s: %s", self._api_url, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="JupyterHub is not reachable",
            ) from exc
        if response.status_code in (401, 403):
            log.error("JupyterHub rejected the API token (%s)", response.status_code)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="JupyterHub rejected the API credentials",
            )
        return response

    @staticmethod
    def _user_path(username: str) -> str:
        return f"/users/{quote(username, safe='')}"

    def _server(self, username: str, model: dict[str, Any]) -> JupyterServer:
        default = (model.get("servers") or {}).get("")
        if default is None:
            state = ServerStatus.stopped
        elif default.get("ready"):
            state = ServerStatus.running
        else:
            state = ServerStatus.pending
        return JupyterServer(
            username=username,
            status=state,
            url=f"{self._public_url}/user/{quote(username, safe='')}/",
        )

    async def get_server(self, username: str) -> JupyterServer:
        response = await self._request("GET", self._user_path(username))
        if response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User does not exist in this JupyterHub",
            )
        response.raise_for_status()
        return self._server(username, response.json())

    async def start_server(self, username: str) -> JupyterServer:
        path = self._user_path(username)
        response = await self._request("GET", path)
        if response.status_code == 404:
            created = await self._request("POST", path)
            if created.status_code not in (201, 409):
                created.raise_for_status()
        elif response.is_success:
            current = self._server(username, response.json())
            if current.status is not ServerStatus.stopped:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Jupyter server is already {current.status}",
                )
        else:
            response.raise_for_status()

        started = await self._request("POST", f"{path}/server")
        if started.status_code == 400:
            # Hub answers 400 when a spawn is already in progress.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=started.text
            )
        started.raise_for_status()
        return JupyterServer(
            username=username,
            status=ServerStatus.running
            if started.status_code == 201
            else ServerStatus.pending,
            url=f"{self._public_url}/user/{quote(username, safe='')}/",
        )

    async def stop_server(self, username: str) -> None:
        response = await self._request("DELETE", f"{self._user_path(username)}/server")
        if response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User does not exist in this JupyterHub",
            )
        if response.status_code == 400:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Jupyter server is not running",
            )
        response.raise_for_status()
