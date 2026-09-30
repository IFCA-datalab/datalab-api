"""Application settings, loaded from environment variables (or a ``.env`` file)."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Treat `KEY=` as unset so optional integrations stay disabled.
        env_ignore_empty=True,
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    admin_users: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Logins/emails allowed to manage any environment.",
    )

    # --- DataLab JWT -------------------------------------------------------
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_ttl_hours: int = 8
    jwt_issuer: str = "datalab-api"

    # --- Portal (frontend) -------------------------------------------------
    frontend_url: HttpUrl = HttpUrl("http://localhost:5173")
    # Deliver the token in the URL fragment (#token=...) so it never reaches
    # server logs or Referer headers. Set to False for the legacy ?token=...
    portal_token_in_fragment: bool = True
    cookie_secure: bool = True

    # --- GitHub OAuth (optional) -------------------------------------------
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    github_redirect_uri: str | None = None

    # --- Keycloak OIDC (optional) ------------------------------------------
    keycloak_url: str | None = None
    keycloak_realm: str = "datalab"
    keycloak_client_id: str | None = None
    keycloak_client_secret: SecretStr | None = None
    keycloak_callback_url: str | None = None
    # True, False, or a path to a CA bundle.
    keycloak_verify_tls: bool | str = True

    # --- Kubernetes / JupyterHub -------------------------------------------
    base_domain: str = "example.org"
    # Prefix of the labels/annotations set on the namespaces the API manages.
    # Changing it hides the environments created with the previous prefix.
    label_prefix: str = "datalab"
    tls_secret_name: str = "cert-secret"
    hub_api_url_template: str = "https://{name}.{base_domain}/hub/api"
    hub_ready_timeout_seconds: int = 300
    # Token for hubs deployed before per-namespace secrets existed.
    hub_api_token_fallback: SecretStr | None = None
    # JSON object: {"ids": "<secret>", "ipcc": "<secret>"}
    hub_oauth_client_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    hub_dummy_password: SecretStr | None = None
    shared_storage_class: str = "longhorn"
    shared_storage_size: str = "100Gi"

    # --- Kafka ---------------------------------------------------------------
    kafka_namespace: str = "kafka"
    kafka_image: str = "docker.io/confluentinc/cp-kafka:7.6.0"
    kafka_storage_class: str = "cinder-csi"
    kafka_storage_size: str = "10Gi"
    kafka_node_port: int = 30092
    kafka_public_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", "admin_users", "kafka_public_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def github_enabled(self) -> bool:
        return bool(
            self.github_client_id
            and self.github_client_secret
            and self.github_redirect_uri
        )

    @property
    def keycloak_enabled(self) -> bool:
        return bool(
            self.keycloak_url
            and self.keycloak_client_id
            and self.keycloak_client_secret
            and self.keycloak_callback_url
        )

    @property
    def owner_annotation(self) -> str:
        return f"{self.label_prefix}/created-by"

    @property
    def error_annotation(self) -> str:
        return f"{self.label_prefix}/provisioning-error"

    @property
    def type_label(self) -> str:
        return f"{self.label_prefix}/type"

    @property
    def keycloak_realm_url(self) -> str:
        return f"{(self.keycloak_url or '').rstrip('/')}/realms/{self.keycloak_realm}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
