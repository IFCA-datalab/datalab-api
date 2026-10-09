import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator


class DeploymentTypeInfo(BaseModel):
    type: str
    label: str
    description: str
    icon: str
    available: bool = Field(
        description="Whether this type can currently be deployed by the API."
    )
    hub_username_claim: Literal["login", "email"] | None = Field(
        default=None,
        description="User field the hub uses as username; null if not a hub.",
    )
    keycloak_only: bool = Field(
        default=False, description="The hub only accepts Keycloak (SSO) logins."
    )
    kind: Literal["jupyterhub", "kafka", "link"] = Field(
        default="jupyterhub",
        description="'link' services are only a card that opens `url`.",
    )
    url: str | None = Field(default=None, description="Address of a 'link' service.")


class EnvironmentStatus(StrEnum):
    provisioning = "provisioning"
    ready = "ready"
    failed = "failed"
    deleting = "deleting"


class SharedVolume(BaseModel):
    """The volume with the data shared by everyone in an environment."""

    name: str
    created: bool = Field(description="Whether the PersistentVolumeClaim exists")
    on_longhorn: bool = Field(description="Provisioned by Longhorn")
    ready: bool = Field(description="Bound and usable (Longhorn: not faulted)")
    storage_class: str | None = None
    size: str | None = Field(default=None, description="Capacity, e.g. 100Gi")
    used_bytes: int | None = Field(
        default=None, description="Space used on disk (Longhorn only)"
    )
    status: str | None = Field(
        default=None,
        description="Longhorn state/robustness, or the PVC phase otherwise",
    )


class Environment(BaseModel):
    type: str
    namespace: str
    status: EnvironmentStatus
    hub_url: str
    created_by: str | None = None
    error: str | None = None
    shared_volume: SharedVolume | None = Field(
        default=None, description="Only for types with shared storage"
    )


class ServerStatus(StrEnum):
    running = "running"
    pending = "pending"
    stopped = "stopped"


class JupyterServer(BaseModel):
    username: str
    status: ServerStatus
    url: str


class KafkaCreate(BaseModel):
    replicas: int = Field(
        default=3, ge=1, le=5, description="Brokers; at most one per public host."
    )
    client_username: str = Field(
        default="kafkaclient1",
        pattern=r"^[A-Za-z][A-Za-z0-9._-]{2,31}$",
        description="SASL/PLAIN user for clients ('admin' is reserved).",
    )
    client_password: SecretStr | None = Field(
        default=None,
        min_length=12,
        description="Password for the client user. Generated if omitted.",
    )

    @field_validator("client_username")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value.lower() == "admin":
            raise ValueError("'admin' is reserved for the brokers")
        return value

    @field_validator("client_password")
    @classmethod
    def _jaas_safe(cls, value: SecretStr | None) -> SecretStr | None:
        # It goes inside a quoted JAAS value: no quotes, backslashes or spaces.
        if value is not None and re.search(r'["\\\s]', value.get_secret_value()):
            raise ValueError("must not contain quotes, backslashes or spaces")
        return value


class KafkaCluster(BaseModel):
    namespace: str
    status: EnvironmentStatus
    replicas: int
    ready_replicas: int
    bootstrap_servers: str
    security_protocol: str = "SASL_SSL"
    sasl_mechanism: str = "PLAIN"
    client_username: str = "kafkaclient1"
    created_by: str | None = None


class KafkaCredentials(KafkaCluster):
    client_password: str = Field(
        description="Only returned once, when the cluster is created."
    )


class UserInfo(BaseModel):
    sub: str
    login: str
    name: str | None
    email: str | None
    provider: str
    groups: list[str] = []
    is_admin: bool
