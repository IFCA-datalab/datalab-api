"""DataLab JWT issuing/validation and the authenticated-user dependency."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from kubernetes import client
from pydantic import BaseModel

from .config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    sub: str
    login: str
    name: str | None = None
    email: str | None = None
    provider: str
    is_admin: bool = False

    def identities(self) -> set[str]:
        return {value for value in (self.login, self.email) if value}

    def can_act_as(self, username: str) -> bool:
        return self.is_admin or username in self.identities()


def create_access_token(
    settings: Settings,
    *,
    sub: str,
    login: str,
    provider: str,
    name: str | None = None,
    email: str | None = None,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": settings.jwt_issuer,
        "sub": f"{provider}:{sub}",
        "login": login,
        "name": name,
        "email": email,
        "provider": provider,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_ttl_hours),
    }
    return jwt.encode(
        claims,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(settings: Settings, token: str) -> CurrentUser:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = CurrentUser.model_validate(claims)
    user.is_admin = bool(user.identities() & set(settings.admin_users))
    return user


def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(settings, credentials.credentials)


def ensure_can_manage(
    user: CurrentUser, namespace: client.V1Namespace, settings: Settings
) -> None:
    """Only the creator of a namespace (or an admin) may modify or delete it."""
    annotations = namespace.metadata.annotations or {}
    created_by = annotations.get(settings.owner_annotation)
    if user.is_admin or (created_by is not None and created_by == user.sub):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "Only the creator of this resource or an administrator can do this",
    )


UserDep = Annotated[CurrentUser, Depends(get_current_user)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
