import logging
from typing import Union
from urllib.parse import urlencode
from fastapi import FastAPI, APIRouter,HTTPException, Depends
from fastapi.responses import RedirectResponse
import httpx
import os
import jwt
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any


#from fastapi_keycloak import FastAPIKeycloak, OIDCUser
from .routers import deployments, users, jupyters
from .app.db import User, create_db_and_tables
from .app.schemas import UserCreate, UserRead, UserUpdate
from .app.users import (
    SECRET, 
    auth_backend, 
    current_active_user, 
    fastapi_users,
    #keycloak_oauth_client
    #github_oauth_client
)

from jose import JWTError, jwt

import oauthlib.oauth2

import requests.auth
from requests_oauth2client import OAuth2Client, AuthorizationRequest

from starlette.requests import Request

from httpx_oauth.oauth2 import OAuth2
from httpx_oauth.oauth2 import BaseOAuth2
from httpx_oauth.integrations.fastapi import OAuth2AuthorizeCallback

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy

from dotenv import load_dotenv
load_dotenv()

import os

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI")
FRONTEND_URL = os.getenv("FRONTEND_URL")
JWT_SECRET = os.getenv("JWT_SECRET")

KEYCLOAK_URL="https://keycloak..es"
KEYCLOAK_REALM="datalab"
KEYCLOAK_CLIENT_ID="datalab-api"
KEYCLOAK_CLIENT_SECRET="XXXXXXXXXXXXXXXXXXXXXX6"
KEYCLOAK_CALLBACK_URL="http://localhost:8000/auth/keycloak/callback"
PORTAL_URL="http://localhost:5173"
OAUTH_STATE_SECRET="v_CyKv2QrZXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXdWw"

## GIT OAUTH" APP
app = FastAPI(title="datalab-api", debug=True)
                       #swagger_ui_init_oauth={"clientId": "DATALAB CLIENT",
                       #                       "clientSecret": "DATALAB SECRET",
                       #                       "appName": "datalab-api",
                       #                       "scopes": "openid, read:users",
                       #                       "useBasicAuthenticationWithAccessCodeGrant": True,
                       #                       },)

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
REDIRECT_URL = "http://vm243.pub.cloud.ifca.es:30001/callback"

# app.include_router(
#     fastapi_users.get_oauth_router(
#         keycloak_oauth_client, 
#         auth_backend, 
#         SECRET, ## PASSWORD TO CHANGE
#         associate_by_email=True,
#         redirect_url=f"{REDIRECT_URL}"),
#     prefix="",
#     tags=["auth"],
# )
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/auth/github/login")
async def github_login():
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={GITHUB_REDIRECT_URI}"
        "&scope=read:user user:email"
    )
    return RedirectResponse(url)

@app.get("/auth/github/callback")
async def github_callback(code: str):
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
        )

        token_data = token_resp.json()

        access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(
                status_code=400,
                detail="GitHub token exchange failed",
            )

        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )

        user_resp.raise_for_status()
        user_data = user_resp.json()

    username = user_data.get("login", "")

    portal_token = create_portal_token(
        user_id=str(user_data["id"]),
        username=username,
        name=user_data.get("name"),
    )

    return redirect_to_portal(
        portal_token=portal_token,
        username=username,
        avatar=user_data.get("avatar_url"),
    )

class InsecureOAuth2(BaseOAuth2[dict[str, Any]]):

    def get_httpx_client(self):
        return httpx.AsyncClient(verify=False)

    

keycloak_oauth_client = InsecureOAuth2(
    client_id=KEYCLOAK_CLIENT_ID,
    client_secret=KEYCLOAK_CLIENT_SECRET,
    authorize_endpoint=(
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
        "/protocol/openid-connect/auth"
    ),
    access_token_endpoint=(
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
        "/protocol/openid-connect/token"
    ),
    refresh_token_endpoint=(
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
        "/protocol/openid-connect/token"
    ),
    name="keycloak",
    base_scopes=["openid", "profile", "email"],
    token_endpoint_auth_method="client_secret_basic",
)


PORTAL_URL = os.getenv(
    "PORTAL_URL",
    "http://localhost:5174",
)


def create_portal_token(
    user_id: str,
    username: str,
    name: str | None = None,
):
    return jwt.encode(
        {
            "sub": str(user_id),
            "login": username,
            "name": name,
            "exp": datetime.now(timezone.utc) + timedelta(hours=8),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def redirect_to_portal(
    portal_token: str,
    username: str,
    avatar: str | None = None,
):
    params = {
        "token": portal_token,
        "user": username,
    }

    if avatar:
        params["avatar"] = avatar

    return RedirectResponse(
        f"{FRONTEND_URL}/?{urlencode(params)}"
    )


@app.get("/auth/keycloak/login")
async def keycloak_login():

    state = secrets.token_urlsafe(32)

    authorization_url = (
        await keycloak_oauth_client.get_authorization_url(
            redirect_uri=KEYCLOAK_CALLBACK_URL,
            state=state,
            scope=["openid", "profile", "email"],
        )
    )

    response = RedirectResponse(
        authorization_url,
        status_code=302,
    )

    response.set_cookie(
        key="keycloak_oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        secure=False,  # localhost en desarrollo
        max_age=600,
    )

    return response


@app.get("/auth/keycloak/callback")
async def keycloak_callback(
    request: Request,
    code: str,
    state: str,
):

    # -----------------------------
    # 1. Comprobar state
    # -----------------------------

    expected_state = request.cookies.get(
        "keycloak_oauth_state"
    )

    if (
        not expected_state
        or not secrets.compare_digest(
            expected_state,
            state,
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state",
        )

    # -----------------------------
    # 2. Cambiar code por token
    # -----------------------------

    token_data = (
        await keycloak_oauth_client.get_access_token(
            code=code,
            redirect_uri=KEYCLOAK_CALLBACK_URL,
        )
    )

    access_token = token_data.get("access_token")

    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="Keycloak token exchange failed",
        )

    # -----------------------------
    # 3. Obtener usuario Keycloak
    # -----------------------------

    userinfo_url = (
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
        "/protocol/openid-connect/userinfo"
    )

    async with httpx.AsyncClient(
        verify=False
    ) as client:

        user_resp = await client.get(
            userinfo_url,
            headers={
                "Authorization":
                    f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

        user_resp.raise_for_status()

        user_data = user_resp.json()

    # -----------------------------
    # 4. Identificar usuario
    # -----------------------------

    user_id = user_data.get("sub")

    username = (
        user_data.get("preferred_username")
        or user_data.get("email")
        or user_id
    )

    if not user_id or not username:
        raise HTTPException(
            status_code=400,
            detail="Keycloak user information incomplete",
        )

    # -----------------------------
    # 5. Crear JWT DataLab
    # -----------------------------

    portal_token = create_portal_token(
        user_id=str(user_id),
        username=str(username),
        name=user_data.get("name"),
    )

    # -----------------------------
    # 6. Volver al portal
    # -----------------------------

    response = redirect_to_portal(
        portal_token=portal_token,
        username=str(username),
    )

    response.delete_cookie(
        "keycloak_oauth_state"
    )

    return response