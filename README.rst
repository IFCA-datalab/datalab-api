====================
DataLab Platform API
====================

.. image:: https://img.shields.io/pypi/v/ifca-datalab-api.svg
        :target: https://pypi.org/project/ifca-datalab-api/
        :alt: PyPI version

.. image:: https://img.shields.io/pypi/pyversions/ifca-datalab-api.svg
        :target: https://pypi.org/project/ifca-datalab-api/
        :alt: Python versions

.. image:: https://github.com/IFCA-datalab/datalab-api/actions/workflows/ci.yml/badge.svg
        :target: https://github.com/IFCA-datalab/datalab-api/actions/workflows/ci.yml
        :alt: CI

.. image:: https://github.com/IFCA-datalab/datalab-api/actions/workflows/main.yml/badge.svg
        :target: https://github.com/IFCA-datalab/datalab-api/actions/workflows/main.yml
        :alt: Docker image
        
.. image:: https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json
        :target: https://github.com/astral-sh/ruff
        :alt: Ruff


.. image:: https://www.mypy-lang.org/static/mypy_badge.svg
        :target: https://mypy-lang.org/
        :alt: Checked with mypy


The DataLab is a platform for users whose main goal is the analysis of data in a
ready-to-use environment. The API provisions, on demand and inside a Kubernetes
cluster, the resources each group needs: a JupyterHub per environment type, the
users' Jupyter servers and, optionally, a Kafka cluster.

* Free software: Apache Software License 2.0

Features
--------

- Login with **Keycloak** (OIDC) or **GitHub** OAuth. Both issue the same DataLab
  JWT, sent as ``Authorization: Bearer <token>`` on every other endpoint.
- Provision a complete **JupyterHub** per environment type (``ids``, ``ipcc``,
  ``dummy``) in its own ``jupyterhub-<type>`` namespace: RBAC, secrets, config,
  storage, proxy, hub and ingress.
- Start and inspect each user's **Jupyter server** through the JupyterHub REST API.
- Deploy a **Kafka** cluster (SASL/PLAIN) with a configurable number of brokers.

Requirements
------------

- Python 3.12+
- `uv <https://docs.astral.sh/uv/>`_
- Access to a Kubernetes cluster (``~/.kube/config`` locally, or the in-cluster
  service account). Without it the API still starts, but cluster endpoints
  answer ``503``.
- A Keycloak realm and/or a GitHub OAuth app for login.

Quickstart
----------

.. code-block:: console

    $ cp .env.example .env      # then fill in the values (see below)
    $ uv sync
    $ uv run uvicorn --factory datalab_api.main:app --reload

Open http://localhost:8000/docs for the interactive documentation.

``datalab_api.main:app`` is an application *factory*, so use ``uvicorn --factory``;
``fastapi dev`` cannot load it.

Configuration
-------------

All settings are read from environment variables or from a ``.env`` file.
``.env.example`` lists every option. The most relevant ones:

=============================  ==============================================================
Variable                       Description
=============================  ==============================================================
``JWT_SECRET``                 **Required.** At least 32 characters. Generate one with
                               ``python -c "import secrets; print(secrets.token_urlsafe(48))"``
``BASE_DOMAIN``                Domain under which each hub is exposed
                               (``<type>.<BASE_DOMAIN>``)
``FRONTEND_URL``               Portal that receives the token after login
``CORS_ORIGINS``               Comma-separated list of allowed origins
``ADMIN_USERS``                Comma-separated logins/emails that can manage everything
``COOKIE_SECURE``              Set to ``false`` only for local development over plain http
``KEYCLOAK_*``                 Keycloak URL, realm, client and callback (empty = disabled)
``GITHUB_*``                   GitHub OAuth app (empty = disabled)
``HUB_OAUTH_CLIENT_SECRETS``   JSON object with the OAuth client secret of each hub type
``HUB_DUMMY_PASSWORD``         Password for the ``dummy`` hub (required to deploy it)
``LABEL_PREFIX``               Prefix of the labels/annotations on managed namespaces
                               (default ``datalab``)
``KAFKA_PUBLIC_HOSTS``         Comma-separated public hostnames of the Kafka brokers
=============================  ==============================================================

Never commit the real ``.env``: keep secrets in the environment or in a
Kubernetes ``Secret``.

API
---

======  ================================================  =====================================
Method  Path                                              Description
======  ================================================  =====================================
GET     ``/auth/{github,keycloak}/login``                 Start the OAuth flow
GET     ``/users/me``                                     Identity carried by the token
GET     ``/deployments/types``                            Catalog of environment types (public)
GET     ``/deployments``                                  Environments and their status
GET     ``/deployments/running``                          Names of existing environments
POST    ``/deployments/{type}/jupyterhub``                Create a hub (``202``, asynchronous)
GET     ``/deployments/{type}/jupyterhub``                Status: provisioning / ready / failed
POST    ``/deployments/{type}/jupyterhub/retry``          Resume a failed provisioning
DELETE  ``/deployments/{type}/jupyterhub``                Delete (creator or admin)
GET     ``/deployments/{type}/jupyters/{username}``       Jupyter server status
POST    ``/deployments/{type}/jupyters/{username}``       Start a Jupyter server
DELETE  ``/deployments/{type}/jupyters/{username}``       Stop a Jupyter server
POST    ``/deployments/kafka``                            Create Kafka (password returned once)
GET     ``/deployments/kafka``                            Kafka status and bootstrap servers
DELETE  ``/deployments/kafka``                            Delete Kafka (creator or admin)
======  ================================================  =====================================

Users can only manage their own Jupyter server; ``ADMIN_USERS`` can manage everything.

Deployment in Kubernetes
------------------------

.. code-block:: console

    $ kubectl create secret generic datalab-api-env --from-env-file=.env
    $ kubectl apply -f datalab_api/manifests/api-deployment.yaml

The container image is built and published to the GitHub Container Registry by
the workflow in ``.github/workflows``.

Development
-----------

.. code-block:: console

    $ uv run pytest
    $ uv run ruff format && uv run ruff check --fix
    $ uv run mypy

Credits
-------

Developed at the Instituto de Física de Cantabria (IFCA). See ``AUTHORS.rst``.
