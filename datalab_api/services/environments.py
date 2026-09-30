"""Provisioning of per-type JupyterHub environments in Kubernetes."""

import base64
import copy
import logging
import secrets
from functools import cache
from typing import Any

import yaml
from kubernetes import client
from kubernetes.client.rest import ApiException

from ..catalog import (
    CATALOG,
    MANIFESTS_DIR,
    NAMESPACE_PREFIX,
    DeploymentType,
    hub_configmap_path,
    namespace_for,
)
from ..config import Settings
from ..k8s import (
    MANAGED_BY_LABEL,
    MANAGED_BY_VALUE,
    KubeClient,
    create_if_absent,
)
from ..schemas import Environment, EnvironmentStatus

log = logging.getLogger(__name__)

HUB_SECRET_NAME = "hub-secret"


class EnvironmentExistsError(Exception):
    pass


@cache
def _load_manifest(relative_path: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((MANIFESTS_DIR / relative_path).read_text())
    return data


def manifest(relative_path: str) -> dict[str, Any]:
    """Return a fresh, mutable copy of a bundled manifest (never written back)."""
    return copy.deepcopy(_load_manifest(relative_path))


def hub_url(settings: Settings, deployment_type: DeploymentType | str) -> str:
    return f"https://{deployment_type}.{settings.base_domain}"


# --- Builders (pure functions, easy to test) ------------------------------


def build_hub_secret(
    settings: Settings, deployment_type: DeploymentType
) -> dict[str, Any]:
    data = {
        "proxy-token": secrets.token_hex(32),
        "cookie-secret": secrets.token_hex(32),
        "api-token": secrets.token_hex(32),
    }
    oauth_secret = settings.hub_oauth_client_secrets.get(deployment_type.value)
    if oauth_secret and oauth_secret.get_secret_value():
        data["oauth-client-secret"] = oauth_secret.get_secret_value()
    if settings.hub_dummy_password:
        data["dummy-password"] = settings.hub_dummy_password.get_secret_value()
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": HUB_SECRET_NAME},
        "type": "Opaque",
        "stringData": data,
    }


def build_role() -> client.V1Role:
    return client.V1Role(
        metadata=client.V1ObjectMeta(name="hub", labels={"component": "jupyter"}),
        rules=[
            client.V1PolicyRule(
                api_groups=[""],
                resources=["pods", "persistentvolumeclaims"],
                verbs=["get", "watch", "list", "create", "delete"],
            ),
            client.V1PolicyRule(
                api_groups=[""], resources=["events"], verbs=["get", "watch", "list"]
            ),
        ],
    )


def build_role_binding() -> client.V1RoleBinding:
    return client.V1RoleBinding(
        metadata=client.V1ObjectMeta(name="hub", labels={"component": "jupyter"}),
        subjects=[client.RbacV1Subject(kind="ServiceAccount", name="hub")],
        role_ref=client.V1RoleRef(
            api_group="rbac.authorization.k8s.io", kind="Role", name="hub"
        ),
    )


def build_shared_pvc(
    settings: Settings, deployment_type: DeploymentType
) -> client.V1PersistentVolumeClaim:
    return client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(name=f"{deployment_type}-data-shared"),
        spec=client.V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteMany"],
            resources=client.V1VolumeResourceRequirements(
                requests={"storage": settings.shared_storage_size}
            ),
            storage_class_name=settings.shared_storage_class,
            volume_mode="Filesystem",
        ),
    )


def build_ingress(
    settings: Settings, deployment_type: DeploymentType
) -> dict[str, Any]:
    body = manifest("hub/ingress.yaml")
    host = f"{deployment_type}.{settings.base_domain}"
    rule = body["spec"]["rules"][0]
    rule["host"] = host
    backend = rule["http"]["paths"][0]["backend"]["service"]
    backend["name"] = "proxy-public"
    backend["port"]["number"] = 80
    body["spec"]["tls"] = [{"hosts": [host], "secretName": settings.tls_secret_name}]
    return body


def build_hub_deployment(settings: Settings) -> dict[str, Any]:
    """Hub deployment with the site-specific values the hub config reads."""
    body = manifest("hub/hub-deployment.yaml")
    container = body["spec"]["template"]["spec"]["containers"][0]
    container["env"] += [
        {"name": "HUB_BASE_DOMAIN", "value": settings.base_domain},
        {"name": "KEYCLOAK_REALM_URL", "value": settings.keycloak_realm_url},
    ]
    return body


# --- Operations -------------------------------------------------------------


def reserve_namespace(
    kube: KubeClient, settings: Settings, deployment_type: DeploymentType, owner: str
) -> str:
    """Create the namespace synchronously so concurrent requests get a 409."""
    name = namespace_for(deployment_type)
    body = client.V1Namespace(
        metadata=client.V1ObjectMeta(
            name=name,
            labels={
                MANAGED_BY_LABEL: MANAGED_BY_VALUE,
                settings.type_label: deployment_type.value,
            },
            annotations={settings.owner_annotation: owner},
        )
    )
    try:
        kube.core.create_namespace(body=body)
    except ApiException as exc:
        if exc.status == 409:
            raise EnvironmentExistsError(name) from exc
        raise
    return name


def provision_environment(
    kube: KubeClient, settings: Settings, deployment_type: DeploymentType
) -> None:
    """Create every resource of a JupyterHub environment. Idempotent.

    Runs as a background task; failures are recorded as a namespace
    annotation so ``GET`` can report them.
    """
    ns = namespace_for(deployment_type)
    core, apps = kube.core, kube.apps
    steps: list[tuple[str, Any, dict[str, Any]]] = [
        (
            "secret",
            core.create_namespaced_secret,
            {"body": build_hub_secret(settings, deployment_type)},
        ),
        (
            "service account",
            core.create_namespaced_service_account,
            {
                "body": client.V1ServiceAccount(
                    metadata=client.V1ObjectMeta(
                        name="hub", labels={"component": "jupyter"}
                    )
                )
            },
        ),
        ("role", kube.rbac.create_namespaced_role, {"body": build_role()}),
        (
            "role binding",
            kube.rbac.create_namespaced_role_binding,
            {"body": build_role_binding()},
        ),
        (
            "configmap",
            core.create_namespaced_config_map,
            {"body": yaml.safe_load(hub_configmap_path(deployment_type).read_text())},
        ),
        (
            "hub pvc",
            core.create_namespaced_persistent_volume_claim,
            {"body": manifest("hub/pvc.yaml")},
        ),
        (
            "proxy-api service",
            core.create_namespaced_service,
            {"body": manifest("proxy/service-api.yaml")},
        ),
        (
            "proxy-public service",
            core.create_namespaced_service,
            {"body": manifest("proxy/service.yaml")},
        ),
        (
            "hub service",
            core.create_namespaced_service,
            {"body": manifest("hub/hub-service.yaml")},
        ),
        (
            "proxy deployment",
            apps.create_namespaced_deployment,
            {"body": manifest("proxy/proxy-deployment.yaml")},
        ),
        (
            "hub deployment",
            apps.create_namespaced_deployment,
            {"body": build_hub_deployment(settings)},
        ),
        (
            "ingress",
            kube.networking.create_namespaced_ingress,
            {"body": build_ingress(settings, deployment_type)},
        ),
    ]
    if CATALOG[deployment_type].shared_storage:
        steps.insert(
            6,
            (
                "shared pvc",
                core.create_namespaced_persistent_volume_claim,
                {"body": build_shared_pvc(settings, deployment_type)},
            ),
        )

    step = "starting"
    try:
        for step, create, kwargs in steps:
            created = create_if_absent(create, namespace=ns, **kwargs)
            log.info("[%s] %s %s", ns, step, "created" if created else "already exists")
        kube.annotate_namespace(ns, {settings.error_annotation: None})
    except Exception as exc:
        log.exception("[%s] provisioning failed at step %r", ns, step)
        reason = exc.reason if isinstance(exc, ApiException) else type(exc).__name__
        try:
            kube.annotate_namespace(
                ns, {settings.error_annotation: f"{step}: {reason}"}
            )
        except ApiException:
            log.exception("[%s] could not record provisioning error", ns)


def _deployment_ready(kube: KubeClient, namespace: str, name: str) -> bool:
    try:
        dep = kube.apps.read_namespaced_deployment_status(name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return False
        raise
    return bool(dep.status and (dep.status.ready_replicas or 0) >= 1)


def describe_environment(
    kube: KubeClient, settings: Settings, namespace: client.V1Namespace
) -> Environment:
    name: str = namespace.metadata.name
    deployment_type = name.removeprefix(NAMESPACE_PREFIX)
    annotations = namespace.metadata.annotations or {}
    error = annotations.get(settings.error_annotation)

    if namespace.status and namespace.status.phase == "Terminating":
        status = EnvironmentStatus.deleting
    elif error:
        status = EnvironmentStatus.failed
    elif _deployment_ready(kube, name, "hub") and _deployment_ready(
        kube, name, "proxy"
    ):
        status = EnvironmentStatus.ready
    else:
        status = EnvironmentStatus.provisioning

    return Environment(
        type=deployment_type,
        namespace=name,
        status=status,
        hub_url=hub_url(settings, deployment_type),
        created_by=annotations.get(settings.owner_annotation),
        error=error,
    )


def list_environment_namespaces(kube: KubeClient) -> list[client.V1Namespace]:
    # Prefix match (not only the label) so hubs created before labels existed show up.
    return [
        ns
        for ns in kube.core.list_namespace().items
        if ns.metadata.name.startswith(NAMESPACE_PREFIX)
    ]


def get_hub_api_token(
    kube: KubeClient, settings: Settings, namespace: str
) -> str | None:
    try:
        secret = kube.core.read_namespaced_secret(HUB_SECRET_NAME, namespace)
        encoded = (secret.data or {}).get("api-token")
        if encoded:
            return base64.b64decode(encoded).decode()
    except ApiException as exc:
        if exc.status != 404:
            raise
    if settings.hub_api_token_fallback:
        return settings.hub_api_token_fallback.get_secret_value()
    return None
