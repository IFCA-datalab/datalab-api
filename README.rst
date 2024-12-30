
# DataLab API



.. image:: https://img.shields.io/pypi/v/datalab_api.svg
        :target: https://pypi.python.org/pypi/datalab_api

.. image:: https://img.shields.io/travis/aidaph/datalab_api.svg
        :target: https://travis-ci.com/aidaph/datalab_api

.. image:: https://readthedocs.org/projects/datalab-api/badge/?version=latest
        :target: https://datalab-api.readthedocs.io/en/latest/?version=latest
        :alt: Documentation Status



The DATALAB is a platform for users whose main goal is the analysis of the data in a ready-to-use environment.
The datalab get the data for inmediately use in a Jupyter platform where the backend resourses are provisioned dinamically on demand in a Kubernetes cluster.

* Free software: Apache Software License 2.0
* Documentation: https://datalab-api.readthedocs.io.

Features
--------

- Map the user to a namespace/group inside the datalab.
- Get the data for the analysis.
- Authenticate users via OpenID using the datalaboauthenticator package.
- Create the whole environment for the user inside the kubernetes cluster. From the namespace until the jupyter environment connected to Jupyter Enterprise Gateway.
- Create kafka cluster with 3 nodes: datalab[1-3].ifca.es



