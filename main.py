import logging
import uvicorn
from typing import Union
from fastapi import Security, FastAPI, HTTPException, Depends
from fastapi.security import OAuth2AuthorizationCodeBearer
#from fastapi_keycloak import FastAPIKeycloak, OIDCUser
import fastapi_users
from routers import deployments, users, jupyters
from app.db import User, create_db_and_tables
from app.schemas import UserCreate, UserRead, UserUpdate
from app.users import (
    SECRET, 
    auth_backend, 
    current_active_user, 
    fastapi_users,
    keycloak_oauth_client
    #github_oauth_client
)

from pydantic import BaseModel
from jose import JWTError, jwt

import oauthlib.oauth2
import requests.auth
from requests_oauth2client import OAuth2Client, AuthorizationRequest

from starlette.requests import Request

from httpx_oauth.oauth2 import OAuth2
from httpx_oauth.integrations.fastapi import OAuth2AuthorizeCallback

## GIT OAUTH" APP
app = FastAPI(title="datalab-api", debug=True,
                       swagger_ui_init_oauth={"clientId": "DATALAB CLIENT",
                                              "clientSecret": "DATALAB SECRET",
                                              "appName": "datalab-api",
                                              "scopes": "openid, read:users",
                                              "useBasicAuthenticationWithAccessCodeGrant": True,
                                              },)


app.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"]
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
       prefix="/users",
       tags=["users"],
   )
REDIRECT_URL = "http://node.example.org:30001/callback"

app.include_router(
    fastapi_users.get_oauth_router(
        keycloak_oauth_client, 
        auth_backend, 
        SECRET, ## PASSWORD TO CHANGE
        associate_by_email=True,
        redirect_url=f"{REDIRECT_URL}"),
    prefix="",
    tags=["auth"],
)
app.include_router(deployments.router,
                   tags=["deployments"])
app.include_router(users.router,
                   tags=["users"])
app.include_router(jupyters.router,
                   tags=["jupyters"])


@app.get("/authenticated-route")
async def authenticated_route(user: User = Depends(current_active_user)):
    return {"message": f"hello {user.email}!"}

@app.on_event("startup")
async def on_startup():
    await create_db_and_tables()

