"""Thin wrapper around the Kubernetes client.

The client is built once (in the app lifespan) and injected into routes, so it
can be replaced by a fake in tests and never touches a cluster at import time.
"""

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from kubernetes import client, config
from kubernetes.client.rest import ApiException

log = logging.getLogger(__name__)

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "datalab-api"


class KubeClient:
    def __init__(self, api_client: client.ApiClient) -> None:
        self.api_client = api_client
        self.core = client.CoreV1Api(api_client)
        self.apps = client.AppsV1Api(api_client)
        self.rbac = client.RbacAuthorizationV1Api(api_client)
        self.networking = client.NetworkingV1Api(api_client)

    @classmethod
    def from_environment(cls) -> "KubeClient":
        """Use the in-cluster service account when available, else kubeconfig."""
        try:
            config.load_incluster_config()
            log.info("Using in-cluster Kubernetes configuration")
        except config.ConfigException:
            config.load_kube_config()
            log.info("Using local kubeconfig")
        return cls(client.ApiClient())

    def close(self) -> None:
        self.api_client.close()

    # -- helpers -----------------------------------------------------------

    def get_namespace(self, name: str) -> client.V1Namespace | None:
        try:
            return self.core.read_namespace(name)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def annotate_namespace(self, name: str, annotations: dict[str, str | None]) -> None:
        self.core.patch_namespace(name, {"metadata": {"annotations": annotations}})


def create_if_absent(create: Callable[..., Any], /, **kwargs: Any) -> bool:
    """Call a ``create_namespaced_*`` method, treating 409 Conflict as success.

    Returns True if the object was created, False if it already existed.
    """
    try:
        create(**kwargs)
        return True
    except ApiException as exc:
        if exc.status == 409:
            return False
        raise


async def kube_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate an unhandled Kubernetes API error into an HTTP response."""
    assert isinstance(exc, ApiException)
    log.error(
        "Kubernetes error on %s %s: %s %s",
        request.method,
        request.url.path,
        exc.status,
        exc.reason,
    )
    # 401/403 mean the API's own service account lacks permissions: that is a
    # server-side problem, not the caller's, so it maps to 502 like the rest.
    code = {404: status.HTTP_404_NOT_FOUND, 409: status.HTTP_409_CONFLICT}.get(
        exc.status, status.HTTP_502_BAD_GATEWAY
    )
    return JSONResponse(
        {"detail": f"Kubernetes API error: {exc.reason}"}, status_code=code
    )


def get_kube(request: Request) -> KubeClient:
    kube: KubeClient | None = getattr(request.app.state, "kube", None)
    if kube is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kubernetes client is not configured",
        )
    return kube


KubeDep = Annotated[KubeClient, Depends(get_kube)]
