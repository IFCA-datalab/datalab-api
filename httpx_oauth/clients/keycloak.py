from typing import Any, Dict, List, Optional, Tuple, cast

import httpx

from httpx_oauth.errors import GetIdEmailError
from httpx_oauth.oauth2 import BaseOAuth2
from httpx_oauth.typing import TypedDict

AUTHORIZE_ENDPOINT = "https://sso.example.org/auth/realms/datalab/protocol/openid-connect/auth"
ACCESS_TOKEN_ENDPOINT = "https://sso.example.org/auth/realms/datalab/protocol/openid-connect/token"
BASE_SCOPES = ["profile","openid", "email"]
# Userinfo endpoint?
PROFILE_ENDPOINT = "https://sso.example.org/auth/realms/datalab/protocol/openid-connect/userinfo"
# EMAILS_ENDPOINT = "https://api.github.com/user/emails"


class KeycloakOauth2(BaseOAuth2[Dict[str, Any]]):
    display_name ="Keycloak"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scopes: Optional[List[str]] = BASE_SCOPES,
        name: str = "keycloak",
    ):
        print(client_id)
        super().__init__(
            client_id,
            client_secret,
            AUTHORIZE_ENDPOINT,
            ACCESS_TOKEN_ENDPOINT,
            name=name,
            base_scopes=scopes,
        )

    async def get_id_email(self, token: str) -> Tuple[str, Optional[str]]:
        async with self.get_httpx_client() as client:
            response = await client.get(
                PROFILE_ENDPOINT,
                params={"personFields": "emailAddresses"},
                headers={**self.request_headers, "Authorization": f"Bearer {token}"},
            )
            if response.status_code >= 400:
                raise GetIdEmailError(response.json())

            data = cast(Dict[str, Any], response.json())
            print(data)
            return data.get("sub"), data.get("email")
