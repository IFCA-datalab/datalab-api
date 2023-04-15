import os
import uuid
import logging
from typing import Optional

from fastapi import Depends, Request
from fastapi.security import OAuth2AuthorizationCodeBearer
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)

from httpx_oauth.clients.github import GitHubOAuth2
from httpx_oauth.clients.keycloak import KeycloakOauth2
from fastapi_users.db import SQLAlchemyUserDatabase

from app.db import User, get_user_db

SECRET = "SECRET"

import logging

logging.basicConfig(level=logging.INFO)

github_oauth_client = GitHubOAuth2("9a5d19cf055e2397d319", "07815d6bc0555033239abba32fea42fa864e07dc")
#keycloak_oauth_client = KeycloakOauth2("datalab", "P3BTyqXREH1q0xTnkl4ItVLOn6rhEMeK")

class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: User, request: Optional[Request] = None) -> None:
        print(f"User {user.id} has registered.")

    async def on_after_forgot_password(self, user: User, token: str, request: Optional[Request] = None) -> None:
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(self, user: User, token: str, request: Optional[Request] = None) -> None:
        print(f"Verification requested for user {user.id}. Verification token:{token}")

async def get_user_manager(user_db: SQLAlchemyUserDatabase=Depends(get_user_db)):
    yield UserManager(user_db)

bearer_transport = BearerTransport(tokenUrl="/auth/jwt/login")
bearer_transport.scheme = OAuth2AuthorizationCodeBearer(authorizationUrl="https://github.com/login/oauth/authorize", 
                                                        tokenUrl="https://github.com/login/oauth/access_token",
                                                        scopes={"profile": "profile", "openid": "openid"})

#bearer_transport.scheme = OAuth2AuthorizationCodeBearer(authorizationUrl="http://localhost:8080/realms/master/protocol/openid-connect/auth", 
#                                                        tokenUrl="http://localhost:8080/realms/master/protocol/openid-connect/token",
#                                                        scopes={"profile": "profile", "openid": "openid", "email": "email"})


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)

auth_backend = AuthenticationBackend(name="jwt", transport=bearer_transport, get_strategy=get_jwt_strategy,)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)

async def get_current_user ():
    return current_active_user
