"""Teacher sign-in on the AWS URL: a Cognito ID token on every API call (deployment-aws.md §6.5).

The check is off when COGNITO_USER_POOL_ID is unset (the laptop, the tests), so the local build runs
exactly as before. Learner note links (GET /n/{token}) and the health checks never need a teacher
token: a note link's own HMAC token is its credential (outputs-and-delivery.md §6.2).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import jwt

REGION = "af-south-1"
OPEN_PATHS = ("/health", "/health/live")
OPEN_PREFIXES = ("/n/", "/c/")  # note and crop links carry their own signed tokens


JWKS_TIMEOUT_S = 5


class AuthUnavailableError(Exception):
    """Cognito's signing keys could not be fetched: the token cannot be judged (503, not 401)."""


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Mapping[str, Any]:
        """The token's claims, or `jwt.InvalidTokenError` when it is not a valid teacher token."""
        ...


class CognitoVerifier:
    """Verify an RS256 Cognito ID token: signature, issuer, audience, expiry and token_use."""

    def __init__(
        self,
        pool_id: str,
        client_id: str,
        *,
        region: str = REGION,
        signing_key: Callable[[str], Any] | None = None,
    ) -> None:
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
        self.client_id = client_id
        if signing_key is None:
            jwks = jwt.PyJWKClient(
                f"{self.issuer}/.well-known/jwks.json", cache_keys=True, timeout=JWKS_TIMEOUT_S
            )

            def from_jwks(token: str) -> Any:
                try:
                    return jwks.get_signing_key_from_jwt(token).key
                except jwt.PyJWKClientConnectionError as error:
                    raise AuthUnavailableError(str(error)) from error
                except jwt.PyJWKClientError as error:  # e.g. a kid this pool never issued
                    raise jwt.InvalidTokenError(str(error)) from error

            signing_key = from_jwks
        self._signing_key = signing_key

    def verify(self, token: str) -> Mapping[str, Any]:
        claims: dict[str, Any] = jwt.decode(
            token,
            self._signing_key(token),
            algorithms=["RS256"],
            audience=self.client_id,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "token_use"]},
        )
        if claims.get("token_use") != "id":
            raise jwt.InvalidTokenError("not an ID token")
        return claims


def verifier_from_environment() -> TokenVerifier | None:
    """A CognitoVerifier from COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID, or None (auth off)."""
    pool = os.environ.get("COGNITO_USER_POOL_ID")
    if not pool:
        return None
    client = os.environ.get("COGNITO_CLIENT_ID")
    if not client:
        raise ValueError("COGNITO_USER_POOL_ID is set but COGNITO_CLIENT_ID is not")
    return CognitoVerifier(pool, client)


def needs_token(method: str, path: str) -> bool:
    """CORS preflights, the health checks and learner note links are open; all else is not."""
    if method == "OPTIONS" or path in OPEN_PATHS:
        return False
    return not path.startswith(OPEN_PREFIXES)
