"""DataLab API: provisions JupyterHub and Kafka environments on Kubernetes."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from kubernetes.client.rest import ApiException
from kubernetes.config import ConfigException

from . import __version__
from .config import Settings, get_settings
from .k8s import KubeClient, kube_error_handler
from .routers import auth, deployments, jupyters, kafka, users

log = logging.getLogger(__name__)

HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _make_lifespan(
    kube: KubeClient | None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings: Settings = app.state.settings
        app.state.http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        app.state.keycloak_http = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, verify=settings.keycloak_verify_tls
        )
        app.state.kube = kube
        if app.state.kube is None:
            try:
                app.state.kube = KubeClient.from_environment()
            except ConfigException:
                log.warning(
                    "No Kubernetes configuration found; cluster endpoints disabled"
                )
        try:
            yield
        finally:
            await app.state.http.aclose()
            await app.state.keycloak_http.aclose()
            if app.state.kube is not None:
                app.state.kube.close()

    return lifespan


async def _upstream_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, httpx.HTTPError)
    log.error("Upstream HTTP error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        {"detail": "Upstream service error"}, status_code=status.HTTP_502_BAD_GATEWAY
    )


def create_app(
    settings: Settings | None = None, kube: KubeClient | None = None
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(
        title="DataLab API",
        version=__version__,
        description="Create and manage DataLab environments in a Kubernetes cluster.",
        debug=settings.debug,
        lifespan=_make_lifespan(kube),
    )
    app.state.settings = settings
    app.dependency_overrides[get_settings] = lambda: settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_exception_handler(ApiException, kube_error_handler)
    app.add_exception_handler(httpx.HTTPError, _upstream_error_handler)

    for module in (auth, users, deployments, kafka, jupyters):
        app.include_router(module.router)

    @app.get("/healthz", tags=["health"], include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"], include_in_schema=False)
    def readyz(request: Request) -> JSONResponse:
        ready = request.app.state.kube is not None
        return JSONResponse(
            {"kubernetes": "configured" if ready else "missing"},
            status_code=status.HTTP_200_OK
            if ready
            else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return app


def app() -> FastAPI:
    """Factory for ``uvicorn --factory datalab_api.main:app``."""
    return create_app()
