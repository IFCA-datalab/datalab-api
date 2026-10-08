"""Kubernetes refuses to create a Role granting more than its creator holds."""

from pathlib import Path

import yaml

from datalab_api.services import environments as envs

API_MANIFEST = Path(__file__).parent.parent / "deploy" / "api.yaml"


def _api_permissions() -> set[tuple[str, str, str]]:
    docs = yaml.safe_load_all(API_MANIFEST.read_text())
    role = next(d for d in docs if d and d["kind"] == "ClusterRole")
    return {
        (group, resource, verb)
        for rule in role["rules"]
        for group in rule["apiGroups"]
        for resource in rule["resources"]
        for verb in rule["verbs"]
    }


def test_api_holds_every_permission_it_grants() -> None:
    held = _api_permissions()
    granted = [*envs.build_role().rules, *envs.build_kernel_rbac()[1].rules]
    missing = {
        (group, resource, verb)
        for rule in granted
        for group in rule.api_groups
        for resource in rule.resources
        for verb in rule.verbs
    } - held
    assert not missing, f"add to the ClusterRole in deploy/api.yaml: {missing}"


def test_kernels_use_the_kernel_service_account() -> None:
    from datalab_api.catalog import DeploymentType, hub_configmap_path

    for deployment_type in (DeploymentType.ids, DeploymentType.climate):
        config = hub_configmap_path(deployment_type).read_text()
        assert f"'KERNEL_SERVICE_ACCOUNT_NAME': \"{envs.KERNEL_SERVICE_ACCOUNT}\"" in (
            config
        ), deployment_type
        assert f"{deployment_type}-data-shared" in config.split("KERNEL_VOLUMES")[1]
