"""FastAPI dependencies that turn request headers into an authenticated ``Principal``.

They run before body *field* validation, so a bad key with a well-formed but invalid
body gets 401/403, not 422. FastAPI decodes the JSON itself before any dependency, so
a body that isn't valid JSON gets 422 even without a key; ``BodySizeLimit`` keeps that
pre-auth parse cheap.
"""

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header

from llm_gateway.api.errors import ApiError
from llm_gateway.auth.keys import Role, parse_key, verify_secret
from llm_gateway.state import AppState, get_state

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    key_id: str
    key_prefix: str
    role: Role


def _invalid_key() -> ApiError:
    # One message for every failure: never tell a caller which part of the key was wrong.
    return ApiError(401, "invalid_api_key", "invalid or missing API key", _UNAUTHORIZED)


async def get_principal(
    state: Annotated[AppState, Depends(get_state)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer":
        raise _invalid_key()
    parsed = parse_key(token)
    if parsed is None:
        raise _invalid_key()
    prefix, secret = parsed
    key = await state.keys.get_by_prefix(prefix)
    if key is None or not verify_secret(key, secret) or not key.is_active:
        raise _invalid_key()
    return Principal(tenant_id=key.tenant_id, key_id=key.id, key_prefix=key.prefix, role=key.role)


def require_role(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the caller's key must have one of ``roles``."""
    allowed = frozenset(roles)

    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in allowed:
            raise ApiError(403, "forbidden", "this key's role cannot do that")
        return principal

    return dependency


async def require_admin_token(
    state: Annotated[AppState, Depends(get_state)],
    x_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    """The deployment-wide token that may create tenants."""
    expected = state.settings.admin_token.get_secret_value().encode()
    # Compare bytes: compare_digest raises on non-ASCII str, which would be a 500.
    given = (x_admin_token or "").encode()
    if not given or not hmac.compare_digest(given, expected):
        raise ApiError(401, "invalid_admin_token", "invalid or missing admin token")
