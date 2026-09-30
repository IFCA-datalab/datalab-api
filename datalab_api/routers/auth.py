"""OAuth2 login with GitHub or Keycloak. Both issue the same DataLab JWT."""

import logging
import secrets
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from ..config import Settings
from ..security import SettingsDep, create_access_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "datalab_oauth_state"
STATE_MAX_AGE = 600


def _http(request: Request) -> httpx.AsyncClient:
    http: httpx.AsyncClient = request.app.state.http
    return http


def _redirect_with_state(settings: Settings, url: str, state: str) -> RedirectResponse:
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=STATE_MAX_AGE,
    )
    return response


def _check_state(expected: str | None, received: str) -> None:
    if not expected or not secrets.compare_digest(expected, received):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")


def _redirect_to_portal(
    settings: Settings, token: str, login: str, avatar: str | None
) -> RedirectResponse:
    params = {"token": token, "user": login}
    if avatar:
        params["avatar"] = avatar
    separator = "#" if settings.portal_token_in_fragment else "?"
    base = str(settings.frontend_url).rstrip("/")
    response = RedirectResponse(f"{base}/{separator}{urlencode(params)}")
    response.delete_cookie(STATE_COOKIE)
    return response


def _require(enabled: bool, provider: str) -> None:
    if not enabled:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{provider} login is not configured"
        )


# --- GitHub ------------------------------------------------------------------


@router.get("/github/login", summary="Start GitHub login")
async def github_login(settings: SettingsDep) -> RedirectResponse:
    _require(settings.github_enabled, "GitHub")
    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return _redirect_with_state(
        settings, f"https://github.com/login/oauth/authorize?{query}", state
    )


@router.get(
    "/github/callback", summary="GitHub OAuth callback", include_in_schema=False
)
async def github_callback(
    request: Request,
    settings: SettingsDep,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    expected_state: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
) -> RedirectResponse:
    _require(settings.github_enabled, "GitHub")
    _check_state(expected_state, state)
    http = _http(request)
    assert settings.github_client_secret is not None

    token_resp = await http.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret.get_secret_value(),
            "code": code,
            "redirect_uri": settings.github_redirect_uri,
        },
    )
    access_token = (
        token_resp.json().get("access_token") if token_resp.is_success else None
    )
    if not access_token:
        log.warning("GitHub token exchange failed: %s", token_resp.status_code)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "GitHub token exchange failed")

    user_resp = await http.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    if not user_resp.is_success:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not fetch GitHub user")
    user: dict[str, Any] = user_resp.json()

    token = create_access_token(
        settings,
        sub=str(user["id"]),
        login=user["login"],
        name=user.get("name"),
        email=user.get("email"),
        provider="github",
    )
    return _redirect_to_portal(settings, token, user["login"], user.get("avatar_url"))


# --- Keycloak ----------------------------------------------------------------


@router.get("/keycloak/login", summary="Start Keycloak login")
async def keycloak_login(settings: SettingsDep) -> RedirectResponse:
    _require(settings.keycloak_enabled, "Keycloak")
    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "client_id": settings.keycloak_client_id,
            "redirect_uri": settings.keycloak_callback_url,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
        }
    )
    return _redirect_with_state(
        settings,
        f"{settings.keycloak_realm_url}/protocol/openid-connect/auth?{query}",
        state,
    )


@router.get(
    "/keycloak/callback", summary="Keycloak OAuth callback", include_in_schema=False
)
async def keycloak_callback(
    request: Request,
    settings: SettingsDep,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    expected_state: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
) -> RedirectResponse:
    _require(settings.keycloak_enabled, "Keycloak")
    _check_state(expected_state, state)
    http: httpx.AsyncClient = request.app.state.keycloak_http
    assert settings.keycloak_client_secret is not None
    oidc = f"{settings.keycloak_realm_url}/protocol/openid-connect"

    token_resp = await http.post(
        f"{oidc}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.keycloak_callback_url,
        },
        auth=(
            settings.keycloak_client_id or "",
            settings.keycloak_client_secret.get_secret_value(),
        ),
    )
    access_token = (
        token_resp.json().get("access_token") if token_resp.is_success else None
    )
    if not access_token:
        log.warning("Keycloak token exchange failed: %s", token_resp.status_code)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Keycloak token exchange failed"
        )

    user_resp = await http.get(
        f"{oidc}/userinfo", headers={"Authorization": f"Bearer {access_token}"}
    )
    if not user_resp.is_success:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Could not fetch Keycloak user"
        )
    user: dict[str, Any] = user_resp.json()

    sub = user.get("sub")
    login = user.get("preferred_username") or user.get("email") or sub
    if not sub or not login:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Keycloak user information incomplete"
        )

    token = create_access_token(
        settings,
        sub=str(sub),
        login=str(login),
        name=user.get("name"),
        email=user.get("email"),
        provider="keycloak",
    )
    return _redirect_to_portal(settings, token, str(login), None)
