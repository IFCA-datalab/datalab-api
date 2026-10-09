"""JupyterHub environments, one per deployment type (namespace ``jupyterhub-<type>``)."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from kubernetes import client

from ..catalog import (
    CATALOG,
    NAMESPACE_PREFIX,
    CatalogEntry,
    DeploymentType,
    jupyterhub_available,
    namespace_for,
)
from ..config import Settings
from ..k8s import KubeDep
from ..schemas import DeploymentTypeInfo, Environment, EnvironmentStatus
from ..security import (
    CurrentUser,
    OptionalUserDep,
    SettingsDep,
    UserDep,
    ensure_can_manage,
)
from ..services import environments as envs

router = APIRouter(prefix="/deployments", tags=["deployments"])


def _ensure_deployable(deployment_type: DeploymentType) -> None:
    if not jupyterhub_available(deployment_type):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"JupyterHub environments are not available for type '{deployment_type}'",
        )


def ensure_allowed(user: CurrentUser, settings: Settings, service_id: str) -> None:
    """Users may only create the services of their groups (catalog.yaml)."""
    if not settings.service_catalog.allows(service_id, user.groups, user.is_admin):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"'{service_id}' is not available to your groups",
        )


def _type_info(entry: CatalogEntry) -> DeploymentTypeInfo:
    if entry.kind == "link":
        return DeploymentTypeInfo(
            type=entry.id,
            label=entry.label or entry.id,
            description=entry.description or "",
            icon=entry.icon or "🔗",
            available=bool(entry.url),
            kind="link",
            url=entry.url,
        )
    deployment_type = DeploymentType(entry.id)
    spec = CATALOG[deployment_type]
    return DeploymentTypeInfo(
        type=deployment_type.value,
        label=spec.label,
        description=spec.description,
        icon=spec.icon,
        hub_username_claim=spec.hub_username_claim,
        keycloak_only=spec.keycloak_only,
        available=(
            deployment_type is DeploymentType.kafka
            or jupyterhub_available(deployment_type)
        ),
        kind=entry.kind,
    )


@router.get("/types")
def list_deployment_types(
    user: OptionalUserDep, settings: SettingsDep
) -> list[DeploymentTypeInfo]:
    """Services offered by the DataLab.

    Signed in: only those of the user's groups (all for administrators).
    Anonymous: the whole catalog, for the public help page.
    """
    catalog = settings.service_catalog
    entries = (
        catalog.visible(user.groups, user.is_admin) if user else list(catalog.entries)
    )
    return [_type_info(entry) for entry in entries]


@router.get("/running")
def list_running_types(
    _: UserDep,
    kube: KubeDep,
) -> list[str]:
    """Types that currently have a JupyterHub namespace (kept for the portal)."""
    return [
        ns.metadata.name.removeprefix(NAMESPACE_PREFIX)
        for ns in envs.list_environment_namespaces(kube)
    ]


@router.get("")
def list_environments(
    _: UserDep,
    kube: KubeDep,
    settings: SettingsDep,
) -> list[Environment]:
    """All JupyterHub environments with their status."""
    return [
        envs.describe_environment(kube, settings, ns)
        for ns in envs.list_environment_namespaces(kube)
    ]


@router.post(
    "/{deployment_type}/jupyterhub",
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"description": "The environment already exists"}},
)
def create_environment(
    user: UserDep,
    deployment_type: DeploymentType,
    background: BackgroundTasks,
    kube: KubeDep,
    settings: SettingsDep,
) -> Environment:
    """Start provisioning a JupyterHub environment.

    Returns immediately; poll ``GET`` on the same path until ``status`` is
    ``ready`` (or ``failed``).
    """
    ensure_allowed(user, settings, deployment_type.value)
    _ensure_deployable(deployment_type)
    try:
        namespace = envs.reserve_namespace(
            kube, settings, deployment_type, owner=user.sub
        )
    except envs.EnvironmentExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Environment '{deployment_type}' already exists"
        ) from None

    background.add_task(envs.provision_environment, kube, settings, deployment_type)
    return Environment(
        type=deployment_type.value,
        namespace=namespace,
        status=EnvironmentStatus.provisioning,
        hub_url=envs.hub_url(settings, deployment_type),
        created_by=user.sub,
    )


def _get_namespace(
    kube: KubeDep, deployment_type: DeploymentType
) -> client.V1Namespace:
    namespace = kube.get_namespace(namespace_for(deployment_type))
    if namespace is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Environment '{deployment_type}' does not exist"
        )
    return namespace


@router.get("/{deployment_type}/jupyterhub")
def get_environment(
    _: UserDep,
    deployment_type: DeploymentType,
    kube: KubeDep,
    settings: SettingsDep,
) -> Environment:
    """Status and URL of a JupyterHub environment."""
    namespace = _get_namespace(kube, deployment_type)
    return envs.describe_environment(kube, settings, namespace)


@router.post(
    "/{deployment_type}/jupyterhub/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_environment(
    user: UserDep,
    deployment_type: DeploymentType,
    background: BackgroundTasks,
    kube: KubeDep,
    settings: SettingsDep,
) -> Environment:
    """Resume a failed or partial provisioning (only creates what is missing)."""
    _ensure_deployable(deployment_type)
    namespace = _get_namespace(kube, deployment_type)
    ensure_can_manage(user, namespace, settings)
    background.add_task(envs.provision_environment, kube, settings, deployment_type)
    env = envs.describe_environment(kube, settings, namespace)
    return env.model_copy(
        update={"status": EnvironmentStatus.provisioning, "error": None}
    )


@router.delete("/{deployment_type}/jupyterhub", status_code=status.HTTP_202_ACCEPTED)
def delete_environment(
    user: UserDep,
    deployment_type: DeploymentType,
    kube: KubeDep,
    settings: SettingsDep,
) -> None:
    """Delete the environment and everything in its namespace (asynchronous)."""
    namespace = _get_namespace(kube, deployment_type)
    ensure_can_manage(user, namespace, settings)
    kube.core.delete_namespace(namespace.metadata.name)
