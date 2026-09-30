"""Catalog of environment types offered by the DataLab."""

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path

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


CATALOG: dict[DeploymentType, DeploymentTypeSpec] = {
    DeploymentType.ids: DeploymentTypeSpec(
        label="IDS",
        description=(
            "Entorno orientado al análisis y visualización de datos de ciberseguridad."
        ),
        icon="📊",
        shared_storage=True,
    ),
    DeploymentType.climate: DeploymentTypeSpec(
        label="Climate",
        description=(
            "Entorno para análisis de datos climáticos y experimentación científica."
        ),
        icon="🌍",
    ),
    DeploymentType.master: DeploymentTypeSpec(
        label="Data Science Hub",
        description=(
            "Entorno generalista para el Máster de Ciencia de Datos, con "
            "herramientas y datasets variados."
        ),
        icon="📈",
    ),
    DeploymentType.dummy: DeploymentTypeSpec(
        label="Dummy",
        description=(
            "Entorno de prueba para validación funcional y despliegues de demostración."
        ),
        icon="🧪",
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
