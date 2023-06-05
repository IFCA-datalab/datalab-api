import glob
    import os
    import re
    import sys
    from binascii import a2b_hex

    from jupyterhub.utils import url_path_join
    from kubernetes_asyncio import client
    from tornado.httpclient import AsyncHTTPClient

    c.JupyterHub.spawner_class = "kubespawner.KubeSpawner"

    # Connect to a proxy running in a different pod. Note that *_SERVICE_*
    # environment variables are set by Kubernetes for Services
    c.ConfigurableHTTPProxy.api_url = 'http:/{}:{}'.format(os.environ['PROXY_API_SERVICE_HOST'],
    int(os.environ['PROXY_API_SERVICE_PORT']))
    c.ConfigurableHTTPProxy.should_start = False

    # Do not shut down user pods when hub is restarted
    c.JupyterHub.cleanup_servers = False

    # Check that the proxy has routes appropriately setup
    c.JupyterHub.last_activity_interval = 60


    # configure the hub db connection
    #db_type = get_config("hub.db.type")
    #if db_type == "sqlite-pvc":
    #    c.JupyterHub.db_url = "sqlite:///jupyterhub.sqlite"
    #elif db_type == "sqlite-memory":
    #    c.JupyterHub.db_url = "sqlite://"
    #else:
    #    set_config_if_not_none(c.JupyterHub, "db_url", "hub.db.url")

    # hub_bind_url configures what the JupyterHub process within the hub pod's
    # container should listen to.
    hub_container_port = 8081
    #c.JupyterHub.hub_bind_url = f"http://:{hub_container_port}"

    c.JupyterHub.ip = os.environ['PROXY_PUBLIC_SERVICE_HOST']
    c.JupyterHub.port = int(os.environ['PROXY_PUBLIC_SERVICE_PORT'])

    # # the hub should listen on all interfaces, so the proxy can access it
    c.JupyterHub.hub_ip = '0.0.0.0'

    c.KubeSpawner.image = "jupyter/pyspark-notebook:latest"

    c.KubeSpawner.service_account = "hub"

    # Mount volume for storage
    pvc_name_template = 'claim-{username}'
    c.KubeSpawner.pvc_name_template = pvc_name_template
    volume_name_template = 'volume-{username}'

    c.KubeSpawner.storage_pvc_ensure = True
    c.KubeSpawner.storage_class = 'cinser-csi'
    c.KubeSpawner.storage_access_modes = ['ReadWriteOnce']
    c.KubeSpawner.storage_capacity = '200Mi'

    # Add volumes to singleuser pods
    c.KubeSpawner.volumes = [
        {
            'name': volume_name_template,
            'persistentVolumeClaim': {
                'claimName': pvc_name_template
            }
        }
    ]
    c.KubeSpawner.volume_mounts = [
        {
            'mountPath': '/home/jovyan',
            'name': volume_name_template
        }
    ]

    # # Gives spawned containers access to the API of the hub
    c.JupyterHub.hub_connect_ip = os.environ['HUB_SERVICE_HOST']
    c.JupyterHub.hub_connect_port = int(os.environ['HUB_SERVICE_PORT'])

    c.JupyterHub.authenticator_class = 'jupyterhub.auth.DummyAuthenticator'
    c.DummyAuthenticator.password = "some_password"