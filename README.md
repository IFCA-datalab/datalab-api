# datalab-api

The DATALAB is a platform for users whose main goal is the analysis of the data in a ready-to-use environment.
The datalab get the data for inmediately use in a Jupyter platform where the backend resourses are provisioned dinamically on demand in a Kubernetes cluster.

In the first version of this API:

- Authenticate users via OpenID using the library fastapi_users. Only github configured.
  TODO: Keycloak. 
- Create the whole environment for the user inside the kubernetes cluster. From the namespace until the jupyter environment connected to Jupyter Enterprise Gateway.

TODO:
- Map the user to a namespace/gruup inside the datalab.
- Get the data for the analysis.
