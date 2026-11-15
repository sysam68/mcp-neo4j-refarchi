from __future__ import annotations

import time
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from neo4j import AsyncDriver
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from mcp_neo4j_cypher.auth import (
    OIDCSettings,
    RefArchiAuthorizationMiddleware,
    RefArchiTokenVerifier,
    SecurityConfigurationError,
    _JwksResolver,
    build_auth_provider,
    validate_auth_mode,
)
from mcp_neo4j_cypher.server import create_mcp_server


ISSUER = "https://idp.example.test"
RESOURCE = "https://mcp.example.test/refarchi/api/mcp"
MCP_PATH = "/api/mcp"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()
SETTINGS = OIDCSettings(
    issuer=ISSUER,
    discovery_url=f"{ISSUER}/.well-known/openid-configuration",
    audience=RESOURCE,
    resource_url=RESOURCE,
    algorithms=("RS256",),
    allowed_client_ids=("codex-refarchi", "claude-refarchi"),
    write_client_ids=("codex-refarchi",),
    required_groups=("users",),
)
ACCEPT = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def mint(
    scopes: tuple[str, ...] = ("refarchi:read",),
    *,
    client_id: str = "claude-refarchi",
    groups: tuple[str, ...] = ("users",),
    issuer: str = ISSUER,
    audience: str = RESOURCE,
    issued_at: int | None = None,
    expires_at: int | None = None,
    subject: str = "user-123",
    jti: str | None = "token-123",
    key: Any = PRIVATE_KEY,
    algorithm: str = "RS256",
) -> str:
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "client_id": client_id,
        "groups": list(groups),
        "scp": list(scopes),
        "iat": issued_at if issued_at is not None else now,
        "exp": expires_at if expires_at is not None else now + 300,
    }
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": "test-key"})


def verifier(settings: OIDCSettings = SETTINGS) -> RefArchiTokenVerifier:
    return RefArchiTokenVerifier(settings, lambda _token: PUBLIC_KEY)


def rpc(client: TestClient, method: str, params: dict, token: str | None = None):
    headers = {**ACCEPT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        MCP_PATH,
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )


@pytest.fixture
def http_client():
    driver = AsyncMock(spec=AsyncDriver)
    driver.execute_query.return_value = [{"label": "Architecture"}]
    token_verifier = verifier()
    auth_provider = build_auth_provider(SETTINGS, MCP_PATH, token_verifier)
    mcp = create_mcp_server(driver, "neo4j", auth_provider=auth_provider)
    app = mcp.http_app(
        path=MCP_PATH,
        middleware=[
            Middleware(
                RefArchiAuthorizationMiddleware,
                settings=SETTINGS,
                mcp_path=MCP_PATH,
            )
        ],
        json_response=True,
        stateless_http=True,
    )
    with TestClient(app, base_url="https://mcp.example.test") as client:
        yield client, driver


def test_security_configuration_is_fail_closed() -> None:
    environment = {
        "NEO4J_OIDC_ISSUER_URL": ISSUER,
        "NEO4J_OIDC_DISCOVERY_URL": f"{ISSUER}/.well-known/openid-configuration",
        "NEO4J_OIDC_AUDIENCE": RESOURCE,
        "NEO4J_OAUTH_RESOURCE_URL": RESOURCE,
        "NEO4J_OIDC_ALLOWED_CLIENT_IDS": "codex-refarchi,claude-refarchi",
        "NEO4J_OIDC_WRITE_CLIENT_IDS": "codex-refarchi",
        "NEO4J_OIDC_REQUIRED_GROUPS": "users",
    }
    assert validate_auth_mode("oidc_bearer", "streamable-http", "0.0.0.0", environment)
    assert validate_auth_mode("none", "stdio", None, {}) is None
    assert validate_auth_mode("none", "streamable-http", "127.0.0.1", {}) is None
    with pytest.raises(SecurityConfigurationError, match="loopback"):
        validate_auth_mode("none", "streamable-http", "0.0.0.0", {})
    with pytest.raises(SecurityConfigurationError, match="approved clients"):
        validate_auth_mode(
            "oidc_bearer",
            "streamable-http",
            "0.0.0.0",
            environment | {"NEO4J_OIDC_ALLOWED_CLIENT_IDS": ""},
        )
    with pytest.raises(SecurityConfigurationError, match="WRITE_CLIENT_IDS"):
        validate_auth_mode(
            "oidc_bearer",
            "streamable-http",
            "0.0.0.0",
            environment | {"NEO4J_OIDC_WRITE_CLIENT_IDS": ""},
        )
    with pytest.raises(SecurityConfigurationError, match="asymmetric"):
        validate_auth_mode(
            "oidc_bearer",
            "streamable-http",
            "0.0.0.0",
            environment | {"NEO4J_OIDC_ALLOWED_ALGORITHMS": "HS256"},
        )


def test_discovery_metadata_selects_the_validated_jwks_uri(monkeypatch) -> None:
    class DiscoveryResponse:
        def raise_for_status(self):
            return self

        def json(self):
            return {
                "issuer": ISSUER,
                "jwks_uri": f"{ISSUER}/jwks.json",
            }

    observed: dict[str, Any] = {}

    class SigningKey:
        key = PUBLIC_KEY

    class JwksClient:
        def __init__(self, uri: str, **kwargs: Any):
            observed["uri"] = uri
            observed["options"] = kwargs

        def get_signing_key_from_jwt(self, token: str):
            observed["token"] = token
            return SigningKey()

    monkeypatch.setattr(
        "mcp_neo4j_cypher.auth.httpx.get",
        lambda url, timeout: (
            observed.update(discovery_url=url, discovery_timeout=timeout)
            or DiscoveryResponse()
        ),
    )
    monkeypatch.setattr("mcp_neo4j_cypher.auth.jwt.PyJWKClient", JwksClient)

    assert _JwksResolver(SETTINGS)("signed-token") is PUBLIC_KEY
    assert observed["discovery_url"] == SETTINGS.discovery_url
    assert observed["uri"] == f"{ISSUER}/jwks.json"
    assert observed["token"] == "signed-token"
    assert observed["options"]["lifespan"] == SETTINGS.jwks_cache_ttl_seconds


def test_discovery_issuer_mismatch_is_rejected(monkeypatch) -> None:
    class DiscoveryResponse:
        def raise_for_status(self):
            return self

        def json(self):
            return {
                "issuer": "https://wrong-issuer.example.test",
                "jwks_uri": f"{ISSUER}/jwks.json",
            }

    monkeypatch.setattr(
        "mcp_neo4j_cypher.auth.httpx.get",
        lambda *_args, **_kwargs: DiscoveryResponse(),
    )

    with pytest.raises(jwt.InvalidIssuerError, match="discovery issuer mismatch"):
        _JwksResolver(SETTINGS)("signed-token")


@pytest.mark.asyncio
async def test_valid_token_is_sanitized_after_verification() -> None:
    raw_token = mint()
    access_token = await verifier().verify_token(raw_token)
    assert access_token is not None
    assert access_token.client_id == "claude-refarchi"
    assert access_token.scopes == ["refarchi:read"]
    assert access_token.token != raw_token and len(access_token.token) == 16
    assert access_token.claims == {"subject_verified": True, "groups": ["users"]}
    assert "user-123" not in repr(access_token)
    assert "token-123" not in repr(access_token)


@pytest.mark.asyncio
async def test_write_scope_is_retained_only_for_approved_clients() -> None:
    claude = await verifier().verify_token(
        mint(scopes=("refarchi:read", "refarchi:write"))
    )
    codex = await verifier().verify_token(
        mint(
            scopes=("refarchi:read", "refarchi:write"),
            client_id="codex-refarchi",
        )
    )

    assert claude is not None and claude.scopes == ["refarchi:read"]
    assert codex is not None and codex.scopes == ["refarchi:read", "refarchi:write"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        mint(audience="https://other.example.test/mcp"),
        mint(issuer="https://evil.example.test"),
        mint(expires_at=1_000_000),
        mint(jti=None),
        mint(issued_at=int(time.time()) - 7200, expires_at=int(time.time()) + 60),
        mint(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        mint(key="shared-secret-used-only-for-test", algorithm="HS256"),
    ],
    ids=[
        "audience",
        "issuer",
        "expired",
        "jti",
        "lifetime",
        "signature",
        "algorithm",
    ],
)
async def test_invalid_tokens_are_rejected(token: str) -> None:
    assert await verifier().verify_token(token) is None


@pytest.mark.asyncio
async def test_client_and_group_are_authorized_after_cryptographic_verification() -> None:
    wrong_client = await verifier().verify_token(mint(client_id="other-client"))
    wrong_group = await verifier().verify_token(mint(groups=("guests",)))
    assert wrong_client is not None and wrong_client.client_id == "other-client"
    assert wrong_group is not None and wrong_group.claims["groups"] == ["guests"]


def test_anonymous_challenge_and_protected_resource_metadata_are_exact(http_client) -> None:
    client, _ = http_client
    response = rpc(client, "tools/list", {})
    assert response.status_code == 401
    assert f'resource_metadata="{SETTINGS.metadata_url}"' in response.headers[
        "www-authenticate"
    ]

    metadata = client.get("/.well-known/oauth-protected-resource/refarchi/api/mcp")
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == RESOURCE
    assert [value.rstrip("/") for value in metadata.json()["authorization_servers"]] == [
        ISSUER
    ]
    assert metadata.json()["scopes_supported"] == [
        "refarchi:read",
        "refarchi:write",
    ]


@pytest.mark.parametrize(
    "token",
    [
        mint(client_id="other-client"),
        mint(groups=("guests",)),
        mint(scopes=("refarchi:write",)),
        mint(scopes=("refarchi:read", "refarchi:unknown")),
    ],
    ids=["client", "group", "missing-read", "unknown-scope"],
)
def test_client_group_and_scope_fail_with_403_before_neo4j(http_client, token) -> None:
    client, driver = http_client
    response = rpc(client, "tools/list", {}, token)
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"
    driver.execute_query.assert_not_awaited()


def test_valid_user_lists_tools_and_completes_one_bounded_read(http_client) -> None:
    client, driver = http_client
    listed = rpc(client, "tools/list", {}, mint())
    assert listed.status_code == 200
    tool_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "get_all_labels" in tool_names
    assert "write_neo4j_cypher" not in tool_names

    called = rpc(
        client,
        "tools/call",
        {"name": "get_all_labels", "arguments": {}},
        mint(),
    )
    assert called.status_code == 200
    assert called.json()["result"]["isError"] is False
    driver.execute_query.assert_awaited_once()


def test_read_only_token_cannot_call_write_and_identity_does_not_leak(http_client) -> None:
    client, driver = http_client
    allowed = rpc(client, "tools/list", {}, mint())
    assert allowed.status_code == 200

    denied_write = rpc(
        client,
        "tools/call",
        {
            "name": "write_neo4j_cypher",
            "arguments": {"query": "CREATE (:ShouldNotExist)", "params": {}},
        },
        mint(),
    )
    assert denied_write.status_code == 403
    denied_next_session = rpc(
        client,
        "tools/list",
        {},
        mint(client_id="other-client", subject="another-user", jti="another-token"),
    )
    assert denied_next_session.status_code == 403
    driver.execute_query.assert_not_awaited()


def test_resource_base_requires_the_internal_path_suffix() -> None:
    with pytest.raises(SecurityConfigurationError, match="must end"):
        build_auth_provider(replace(SETTINGS, resource_url=f"{RESOURCE}/other"), MCP_PATH)
