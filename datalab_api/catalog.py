"""Catalog of environment types offered by the DataLab."""

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Literal

import yaml

MANIFESTS_DIR = Path(__file__).parent / "manifests"
NAMESPACE_PREFIX = "jupyterhub-"


class DeploymentType(StrEnum):
    ids = "ids"
    climate = "ipcc"
    master = "datasciencehub"
    dummy = "dummy"
    kafka = "kafka"
    spark = "spark"


@dataclass(frozen=True)
class DeploymentTypeSpec:
    label: str
    description: str
    icon: str
    # Mount a shared ReadWriteMany volume ("<type>-data-shared") in user pods.
    shared_storage: bool = False
    # Claim of the DataLab user that the hub uses as its username (must match
    # the hub's authenticator config). None for types that are not hubs.
    hub_username_claim: Literal["login", "email"] | None = None
    # The hub only accepts Keycloak (SSO) logins.
    keycloak_only: bool = False


CATALOG: dict[DeploymentType, DeploymentTypeSpec] = {
    DeploymentType.ids: DeploymentTypeSpec(
        label="IDS",
        description=(
            "Entorno orientado al análisis y visualización de datos de ciberseguridad."
        ),
        icon="📊",
        shared_storage=True,
        hub_username_claim="login",  # preferred_username in configmap-ids
        keycloak_only=True,
    ),
    DeploymentType.climate: DeploymentTypeSpec(
        label="Climate",
        description=(
            "Entorno para análisis de datos climáticos y experimentación científica."
        ),
        icon="🌍",
        shared_storage=True,
        hub_username_claim="email",  # username_claim = "email" in configmap-ipcc
        keycloak_only=True,
    ),
    DeploymentType.master: DeploymentTypeSpec(
        label="Data Science Hub",
        description=(
            "Entorno generalista para el Máster de Ciencia de Datos, con "
            "herramientas y datasets variados."
        ),
        icon="📈",
        hub_username_claim="login",
        keycloak_only=True,
    ),
    DeploymentType.dummy: DeploymentTypeSpec(
        label="Dummy",
        description=(
            "Entorno de prueba para validación funcional y despliegues de demostración."
        ),
        icon="🧪",
        # DummyAuthenticator accepts any name; email keeps existing servers.
        hub_username_claim="email",
    ),
    DeploymentType.kafka: DeploymentTypeSpec(
        label="Kafka",
        description=(
            "Entorno orientado a mensajería, streaming y pruebas con brokers Kafka."
        ),
        icon="📨",
    ),
    DeploymentType.spark: DeploymentTypeSpec(
        label="Spark",
        description="Entorno para procesamiento distribuido y analítica sobre Apache Spark.",
        icon="⚡",
    ),
}


def hub_configmap_path(deployment_type: DeploymentType) -> Path:
    return MANIFESTS_DIR / "hub" / "configmaps" / f"configmap-{deployment_type}.yaml"


@cache
def jupyterhub_available(deployment_type: DeploymentType) -> bool:
    """A JupyterHub can only be deployed for types that ship a hub config."""
    return hub_configmap_path(deployment_type).is_file()


def namespace_for(deployment_type: DeploymentType | str) -> str:
    return f"{NAMESPACE_PREFIX}{deployment_type}"


# --- Who sees what: catalog.yaml ------------------------------------------------

SERVICE_CATALOG_FILE = Path(__file__).parent / "catalog.yaml"
ServiceKind = Literal["jupyterhub", "kafka", "link"]


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    kind: ServiceKind
    groups: frozenset[str]
    # Only for links (deployable types take them from CATALOG).
    label: str | None = None
    description: str | None = None
    icon: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ServiceCatalog:
    default_group: str
    entries: tuple[CatalogEntry, ...]

    def groups_of(self, user_groups: list[str]) -> set[str]:
        """Every signed-in user also belongs to the default group."""
        return {*user_groups, self.default_group}

    def visible(self, user_groups: list[str], is_admin: bool) -> list[CatalogEntry]:
        groups = self.groups_of(user_groups)
        return [e for e in self.entries if is_admin or e.groups & groups]

    def allows(self, service_id: str, user_groups: list[str], is_admin: bool) -> bool:
        return any(e.id == service_id for e in self.visible(user_groups, is_admin))


def _kind_of(service_id: str) -> ServiceKind:
    return "kafka" if service_id == DeploymentType.kafka else "jupyterhub"


@cache
def load_service_catalog(path: Path = SERVICE_CATALOG_FILE) -> ServiceCatalog:
    """Read catalog.yaml. Deployable types left out of it are admin-only."""
    data = yaml.safe_load(path.read_text())
    deployable = {t.value for t in DeploymentType}
    entries = []
    for item in data["services"]:
        service_id = str(item["id"])
        kind: ServiceKind | None = item.get("kind") or (
            _kind_of(service_id) if service_id in deployable else None
        )
        if kind != "link" and service_id not in deployable:
            raise ValueError(f"{path}: '{service_id}' is not a deployment type")
        if kind is None or kind not in ("jupyterhub", "kafka", "link"):
            raise ValueError(f"{path}: '{service_id}' has an invalid kind")
        if kind == "link" and not item.get("label"):
            raise ValueError(f"{path}: link '{service_id}' needs a label")
        entries.append(
            CatalogEntry(
                id=service_id,
                kind=kind,
                groups=frozenset(item.get("groups") or []),
                label=item.get("label"),
                description=item.get("description"),
                icon=item.get("icon"),
                url=item.get("url") or None,
            )
        )
    listed = {e.id for e in entries}
    entries += [
        CatalogEntry(id=t, kind=_kind_of(t), groups=frozenset())
        for t in sorted(deployable - listed)
    ]
    return ServiceCatalog(
        default_group=str(data.get("default_group", "general")),
        entries=tuple(entries),
    )
