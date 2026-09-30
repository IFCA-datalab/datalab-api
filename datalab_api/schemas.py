from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr


class DeploymentTypeInfo(BaseModel):
    type: str
    label: str
    description: str
    icon: str
    available: bool = Field(
        description="Whether this type can currently be deployed by the API."
    )


class EnvironmentStatus(StrEnum):
    provisioning = "provisioning"
    ready = "ready"
    failed = "failed"
    deleting = "deleting"


class Environment(BaseModel):
    type: str
    namespace: str
    status: EnvironmentStatus
    hub_url: str
    created_by: str | None = None
    error: str | None = None


class ServerStatus(StrEnum):
    running = "running"
    pending = "pending"
    stopped = "stopped"


class JupyterServer(BaseModel):
    username: str
    status: ServerStatus
    url: str


class KafkaCreate(BaseModel):
    replicas: int = Field(default=1, ge=1, le=5)
    client_password: SecretStr | None = Field(
        default=None,
        min_length=12,
        description="Password for the 'kafkaclient1' SASL user. Generated if omitted.",
    )


class KafkaCluster(BaseModel):
    namespace: str
    status: EnvironmentStatus
    replicas: int
    ready_replicas: int
    bootstrap_servers: str
    client_username: str = "kafkaclient1"


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
    is_admin: bool
