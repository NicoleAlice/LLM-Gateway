"""Tenant and API key management."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from llm_gateway.api.errors import ApiError
from llm_gateway.auth.deps import Principal, require_admin_token, require_role
from llm_gateway.auth.keys import ApiKey, Role, Tenant, generate_key
from llm_gateway.auth.repository import DuplicateKeyPrefixError
from llm_gateway.state import AppState, get_state

router = APIRouter(prefix="/v1/admin", tags=["admin"])

MAX_KEY_ATTEMPTS = 3
State = Annotated[AppState, Depends(get_state)]
Admin = Annotated[Principal, Depends(require_role(Role.ADMIN))]


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class TenantCreated(BaseModel):
    tenant_id: str
    admin_key: str


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role


class KeyInfo(BaseModel):
    key_id: str
    prefix: str
    role: Role
    created_at: datetime
    revoked_at: datetime | None

    @classmethod
    def of(cls, key: ApiKey) -> "KeyInfo":
        return cls(
            key_id=key.id,
            prefix=key.prefix,
            role=key.role,
            created_at=key.created_at,
            revoked_at=key.revoked_at,
        )


class KeyCreated(BaseModel):
    key_id: str
    prefix: str
    role: Role
    key: str  # shown once; only its hash is stored


async def _issue_key(state: AppState, tenant_id: str, role: Role) -> tuple[str, ApiKey]:
    # 12-hex prefixes collide rarely but not never; a collision must not overwrite a key.
    for _ in range(MAX_KEY_ATTEMPTS):
        raw, key = generate_key(tenant_id, role, state.clock())
        try:
            await state.keys.add(key)
        except DuplicateKeyPrefixError:
            continue
        return raw, key
    raise RuntimeError(f"no free key prefix after {MAX_KEY_ATTEMPTS} attempts")


@router.post(
    "/tenants",
    status_code=201,
    dependencies=[Depends(require_admin_token)],
)
async def create_tenant(body: TenantCreate, state: State) -> TenantCreated:
    tenant = Tenant(id=uuid.uuid4().hex, name=body.name)
    # Key first: if issuing it fails, no tenant is left behind without an admin key.
    raw, _ = await _issue_key(state, tenant.id, Role.ADMIN)
    await state.tenants.add(tenant)
    return TenantCreated(tenant_id=tenant.id, admin_key=raw)


@router.post("/keys", status_code=201)
async def create_key(body: KeyCreate, state: State, admin: Admin) -> KeyCreated:
    raw, key = await _issue_key(state, admin.tenant_id, body.role)
    return KeyCreated(key_id=key.id, prefix=key.prefix, role=key.role, key=raw)


@router.get("/keys")
async def list_keys(state: State, admin: Admin) -> list[KeyInfo]:
    return [KeyInfo.of(key) for key in await state.keys.list_for_tenant(admin.tenant_id)]


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: str, state: State, admin: Admin) -> Response:
    keys = await state.keys.list_for_tenant(admin.tenant_id)
    target = next((k for k in keys if k.id == key_id), None)
    if target is None:
        raise ApiError(404, "key_not_found", "no such key in this tenant")
    if target.role is Role.ADMIN and target.is_active:
        others = [k for k in keys if k.role is Role.ADMIN and k.is_active and k.id != key_id]
        if not others:
            raise ApiError(409, "last_admin_key", "a tenant must keep one active admin key")
    # No await yields between the check above and this revoke, so in memory it is atomic.
    await state.keys.revoke(admin.tenant_id, key_id, state.clock())
    return Response(status_code=204)
