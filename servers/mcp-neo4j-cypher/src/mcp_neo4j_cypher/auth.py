"""Fail-closed OAuth resource-server support for RefArchi MCP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from pydantic import AnyHttpUrl
from starlette.authentication import UnauthenticatedUser
from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger("mcp_neo4j_cypher.auth")

READ_SCOPE = "refarchi:read"
WRITE_SCOPE = "refarchi:write"
BUSINESS_SCOPES = (READ_SCOPE, WRITE_SCOPE)
IDENTITY_SCOPES = frozenset({"openid", "profile", "offline_access", "groups"})
ASYMMETRIC_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_AUTH_INSPECTION_BYTES = 1024 * 1024


class SecurityConfigurationError(ValueError):
    """Raised when an unsafe or incomplete security profile is configured."""


class _RejectedToken(Exception):
    """JWT was decoded but failed a bounded semantic validation rule."""


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


def _absolute_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SecurityConfigurationError(f"{name} must be an absolute HTTP(S) URL")


def _metadata_url(resource_url: str) -> str:
    parsed = urlparse(resource_url)
    path = parsed.path.rstrip("/")
    return (
        f"{parsed.scheme}://{parsed.netloc}"
        f"/.well-known/oauth-protected-resource{path}"
    )


def _resource_base_url(resource_url: str, mcp_path: str) -> str:
    """Return the public prefix that FastMCP combines with the internal MCP path."""

    parsed = urlparse(resource_url)
    resource_path = parsed.path.rstrip("/")
    normalized_mcp_path = "/" + mcp_path.strip("/")
    if not resource_path.endswith(normalized_mcp_path):
        raise SecurityConfigurationError(
            "NEO4J_OAUTH_RESOURCE_URL path must end with NEO4J_MCP_SERVER_PATH"
        )
    prefix = resource_path[: -len(normalized_mcp_path)]
    return f"{parsed.scheme}://{parsed.netloc}{prefix}"


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    discovery_url: str
    audience: str
    resource_url: str
    algorithms: tuple[str, ...]
    allowed_client_ids: tuple[str, ...]
    write_client_ids: tuple[str, ...]
    required_groups: tuple[str, ...]
    max_token_lifetime_seconds: int = 3600
    clock_skew_seconds: int = 60
    jwks_cache_ttl_seconds: int = 300

    @classmethod
    def from_env(cls, env: dict[str, str]) -> OIDCSettings:
        settings = cls(
            issuer=env.get("NEO4J_OIDC_ISSUER_URL", "").rstrip("/"),
            discovery_url=env.get("NEO4J_OIDC_DISCOVERY_URL", ""),
            audience=env.get("NEO4J_OIDC_AUDIENCE", ""),
            resource_url=env.get("NEO4J_OAUTH_RESOURCE_URL", ""),
            algorithms=_csv(env.get("NEO4J_OIDC_ALLOWED_ALGORITHMS")) or ("RS256",),
            allowed_client_ids=_csv(env.get("NEO4J_OIDC_ALLOWED_CLIENT_IDS")),
            write_client_ids=_csv(env.get("NEO4J_OIDC_WRITE_CLIENT_IDS")),
            required_groups=_csv(env.get("NEO4J_OIDC_REQUIRED_GROUPS")),
            max_token_lifetime_seconds=int(
                env.get("NEO4J_OIDC_MAX_TOKEN_LIFETIME_SECONDS", "3600")
            ),
            clock_skew_seconds=int(env.get("NEO4J_OIDC_CLOCK_SKEW_SECONDS", "60")),
            jwks_cache_ttl_seconds=int(env.get("NEO4J_OIDC_JWKS_CACHE_TTL", "300")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        for name, value in (
            ("NEO4J_OIDC_ISSUER_URL", self.issuer),
            ("NEO4J_OIDC_DISCOVERY_URL", self.discovery_url),
            ("NEO4J_OIDC_AUDIENCE", self.audience),
            ("NEO4J_OAUTH_RESOURCE_URL", self.resource_url),
        ):
            _absolute_url(name, value)
        if self.audience != self.resource_url:
            raise SecurityConfigurationError(
                "NEO4J_OIDC_AUDIENCE must equal the canonical resource URL"
            )
        if not self.allowed_client_ids:
            raise SecurityConfigurationError(
                "NEO4J_OIDC_ALLOWED_CLIENT_IDS must list approved clients"
            )
        if not self.required_groups:
            raise SecurityConfigurationError(
                "NEO4J_OIDC_REQUIRED_GROUPS must list approved groups"
            )
        if not self.write_client_ids or not set(self.write_client_ids).issubset(
            set(self.allowed_client_ids)
        ):
            raise SecurityConfigurationError(
                "NEO4J_OIDC_WRITE_CLIENT_IDS must be a non-empty subset of approved clients"
            )
        if not self.algorithms or set(self.algorithms) - ASYMMETRIC_ALGORITHMS:
            raise SecurityConfigurationError(
                "NEO4J_OIDC_ALLOWED_ALGORITHMS must contain asymmetric algorithms only"
            )
        if self.max_token_lifetime_seconds <= 0:
            raise SecurityConfigurationError(
                "NEO4J_OIDC_MAX_TOKEN_LIFETIME_SECONDS must be positive"
            )
        if not 0 <= self.clock_skew_seconds <= 300:
            raise SecurityConfigurationError(
                "NEO4J_OIDC_CLOCK_SKEW_SECONDS must be between 0 and 300"
            )
        if self.jwks_cache_ttl_seconds <= 0:
            raise SecurityConfigurationError(
                "NEO4J_OIDC_JWKS_CACHE_TTL must be positive"
            )

    @property
    def metadata_url(self) -> str:
        return _metadata_url(self.resource_url)


def validate_auth_mode(
    auth_mode: str,
    transport: str,
    host: str | None,
    env: dict[str, str],
) -> OIDCSettings | None:
    """Validate the transport/auth pairing before the server opens a socket."""

    normalized = auth_mode.strip().lower()
    if normalized not in {"none", "oidc_bearer"}:
        raise SecurityConfigurationError(
            "NEO4J_AUTH_MODE must be 'none' or 'oidc_bearer'"
        )
    if normalized == "none":
        if transport == "stdio":
            return None
        if host not in LOOPBACK_HOSTS:
            raise SecurityConfigurationError(
                "NEO4J_AUTH_MODE=none is limited to stdio or an explicit loopback host"
            )
        return None
    if transport not in {"http", "streamable-http"}:
        raise SecurityConfigurationError(
            "NEO4J_AUTH_MODE=oidc_bearer requires Streamable HTTP"
        )
    return OIDCSettings.from_env(env)


class _JwksResolver:
    """Resolve signing keys from validated OIDC discovery metadata."""

    def __init__(self, settings: OIDCSettings):
        self.settings = settings
        self._client: jwt.PyJWKClient | None = None

    def __call__(self, token: str) -> Any:
        if self._client is None:
            discovery = (
                httpx.get(self.settings.discovery_url, timeout=10)
                .raise_for_status()
                .json()
            )
            if discovery.get("issuer", "").rstrip("/") != self.settings.issuer:
                raise jwt.InvalidIssuerError("discovery issuer mismatch")
            jwks_uri = discovery.get("jwks_uri")
            if not isinstance(jwks_uri, str):
                raise _RejectedToken("discovery_missing_jwks")
            _absolute_url("discovery jwks_uri", jwks_uri)
            self._client = jwt.PyJWKClient(
                jwks_uri,
                cache_keys=True,
                lifespan=self.settings.jwks_cache_ttl_seconds,
                timeout=10,
            )
        return self._client.get_signing_key_from_jwt(token).key


def _scopes(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("scope", claims.get("scp", []))
    if isinstance(raw, str):
        values = raw.split()
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        values = raw
    else:
        raise _RejectedToken("invalid_scopes")
    return sorted(set(values))


def _client_id(claims: dict[str, Any]) -> str:
    client_id = claims.get("client_id")
    azp = claims.get("azp")
    if client_id is not None and azp is not None and client_id != azp:
        raise _RejectedToken("ambiguous_client")
    value = client_id or azp
    if not isinstance(value, str) or not value:
        raise _RejectedToken("missing_client")
    return value


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class RefArchiTokenVerifier(TokenVerifier):
    """Verify JWT authenticity and retain only the claims needed for authorization."""

    def __init__(
        self,
        settings: OIDCSettings,
        key_resolver: Callable[[str], Any] | None = None,
    ):
        super().__init__(required_scopes=[READ_SCOPE])
        self.settings = settings
        self.key_resolver = key_resolver or _JwksResolver(settings)

    @property
    def scopes_supported(self) -> list[str]:
        return list(BUSINESS_SCOPES)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await asyncio.to_thread(self._verify, token)
        except jwt.ExpiredSignatureError:
            reason = "expired"
        except jwt.InvalidAudienceError:
            reason = "wrong_audience"
        except jwt.InvalidIssuerError:
            reason = "wrong_issuer"
        except jwt.ImmatureSignatureError:
            reason = "not_yet_valid"
        except jwt.MissingRequiredClaimError:
            reason = "missing_claim"
        except jwt.InvalidSignatureError:
            reason = "bad_signature"
        except jwt.InvalidAlgorithmError:
            reason = "algorithm_not_allowed"
        except jwt.PyJWKClientError:
            reason = "signing_key_unavailable"
        except _RejectedToken as exc:
            reason = str(exc)
        except (jwt.InvalidTokenError, httpx.HTTPError, ValueError, TypeError):
            reason = "malformed_or_unavailable"
        except Exception:
            reason = "verification_error"
        logger.info("Bearer token rejected: reason=%s", reason)
        return None

    def _verify(self, token: str) -> AccessToken:
        settings = self.settings
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in settings.algorithms:
            raise jwt.InvalidAlgorithmError("algorithm")
        claims = jwt.decode(
            token,
            self.key_resolver(token),
            algorithms=list(settings.algorithms),
            audience=settings.audience,
            issuer=settings.issuer,
            leeway=settings.clock_skew_seconds,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
        )
        subject = claims.get("sub")
        jti = claims.get("jti")
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if not isinstance(subject, str) or not subject:
            raise _RejectedToken("missing_subject")
        if not isinstance(jti, str) or not jti:
            raise _RejectedToken("missing_jti")
        if not isinstance(issued_at, (int, float)) or isinstance(issued_at, bool):
            raise _RejectedToken("invalid_iat")
        if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
            raise _RejectedToken("invalid_exp")
        if expires_at <= issued_at:
            raise _RejectedToken("invalid_lifetime")
        if expires_at - issued_at > settings.max_token_lifetime_seconds:
            raise _RejectedToken("lifetime_too_long")
        if issued_at > time.time() + settings.clock_skew_seconds:
            raise _RejectedToken("issued_in_future")

        groups = claims.get("groups", [])
        if not isinstance(groups, list) or any(
            not isinstance(group, str) for group in groups
        ):
            raise _RejectedToken("invalid_groups")
        client_id = _client_id(claims)
        scopes = _scopes(claims)
        if client_id not in settings.write_client_ids:
            scopes = [scope for scope in scopes if scope != WRITE_SCOPE]
        return AccessToken(
            token=_token_fingerprint(token),
            client_id=client_id,
            scopes=scopes,
            expires_at=int(expires_at),
            resource=settings.resource_url,
            claims={"subject_verified": True, "groups": sorted(set(groups))},
        )


def build_auth_provider(
    settings: OIDCSettings,
    mcp_path: str,
    verifier: RefArchiTokenVerifier | None = None,
) -> RemoteAuthProvider:
    token_verifier = verifier or RefArchiTokenVerifier(settings)
    resource_base_url = _resource_base_url(settings.resource_url, mcp_path)
    return RemoteAuthProvider(
        token_verifier=token_verifier,
        authorization_servers=[AnyHttpUrl(settings.issuer)],
        base_url=resource_base_url,
        resource_base_url=resource_base_url,
        scopes_supported=list(BUSINESS_SCOPES),
        resource_name="RefArchi MCP",
    )


class RefArchiAuthorizationMiddleware:
    """Enforce client, group and operation scopes before MCP reaches Neo4j."""

    def __init__(
        self,
        app: ASGIApp,
        settings: OIDCSettings,
        mcp_path: str,
    ):
        self.app = app
        self.settings = settings
        self.mcp_path = "/" + mcp_path.strip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != self.mcp_path:
            await self.app(scope, receive, send)
            return

        user = scope.get("user", UnauthenticatedUser())
        access_token = getattr(user, "access_token", None)
        if access_token is None:
            await self.app(scope, receive, send)
            return

        claims = access_token.claims or {}
        groups = claims.get("groups", [])
        allowed_scopes = IDENTITY_SCOPES | set(BUSINESS_SCOPES)
        token_scopes = set(access_token.scopes)
        if (
            access_token.client_id not in self.settings.allowed_client_ids
            or claims.get("subject_verified") is not True
            or not set(self.settings.required_groups).issubset(set(groups))
            or not token_scopes.issubset(allowed_scopes)
        ):
            await self._deny(send, "Authorization required")
            return

        required_scope = READ_SCOPE
        replay_receive = receive
        if scope.get("method") == "POST":
            body = await self._read_small_body(scope, receive)
            if body is not None:
                replay_receive = self._replay(body)
                if self._is_write_call(body):
                    required_scope = WRITE_SCOPE
        if required_scope not in token_scopes:
            await self._deny(send, f"Required scope: {required_scope}")
            return
        if (
            required_scope == WRITE_SCOPE
            and access_token.client_id not in self.settings.write_client_ids
        ):
            await self._deny(send, "Client is not approved for write operations")
            return
        await self.app(scope, replay_receive, send)

    async def _read_small_body(
        self, scope: Scope, receive: Receive
    ) -> bytes | None:
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
        }
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > MAX_AUTH_INSPECTION_BYTES:
                    return None
            except ValueError:
                return None
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return None
            chunk = message.get("body", b"")
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_AUTH_INSPECTION_BYTES:
                return b"".join(chunks)
            if not message.get("more_body", False):
                return b"".join(chunks)

    @staticmethod
    def _replay(body: bytes) -> Receive:
        sent = False

        async def receive() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        return receive

    @staticmethod
    def _is_write_call(body: bytes) -> bool:
        if not body or len(body) > MAX_AUTH_INSPECTION_BYTES:
            return False
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        if not isinstance(payload, dict) or payload.get("method") != "tools/call":
            return False
        params = payload.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        return isinstance(name, str) and name.endswith("write_neo4j_cypher")

    async def _deny(self, send: Send, description: str) -> None:
        body = json.dumps(
            {"error": "insufficient_scope", "error_description": description}
        ).encode()
        challenge = (
            'Bearer error="insufficient_scope", '
            f'error_description="{description}", '
            f'resource_metadata="{self.settings.metadata_url}"'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", challenge.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
