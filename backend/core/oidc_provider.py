"""Environment-configured OpenID Connect provider for Flow.

The provider stays disabled until the required ``FLOW_OIDC_*`` settings are
present.  It deliberately uses only the Python standard library so an on-prem
Flow deployment does not need an extra package installation just to enable
SSO.  Corporate OIDC providers normally publish RS256 keys through JWKS; that
algorithm is verified here together with issuer, audience, expiry and nonce.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from core.auth_providers import AuthIdentity, AuthProvider, register_provider


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _b64url_decode(value: str) -> bytes:
    raw = value.encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _fetch_json(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Corporate SSO server is unavailable") from exc
    if not isinstance(payload, dict):
        raise HTTPException(502, "Invalid response from corporate SSO server")
    return payload


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    discovery_url: str
    redirect_uri: str
    scope: str
    username_claim: str
    department_claim: str
    client_auth_method: str
    auto_provision: bool
    state_secret: str

    @classmethod
    def load(cls) -> "OidcConfig":
        issuer = _env("FLOW_OIDC_ISSUER").rstrip("/")
        discovery = _env("FLOW_OIDC_DISCOVERY_URL")
        if not discovery and issuer:
            discovery = f"{issuer}/.well-known/openid-configuration"
        client_secret = _env("FLOW_OIDC_CLIENT_SECRET")
        return cls(
            issuer=issuer,
            client_id=_env("FLOW_OIDC_CLIENT_ID"),
            client_secret=client_secret,
            discovery_url=discovery,
            redirect_uri=_env("FLOW_OIDC_REDIRECT_URI"),
            scope=_env("FLOW_OIDC_SCOPE", "openid profile email"),
            username_claim=_env("FLOW_OIDC_USERNAME_CLAIM", "preferred_username"),
            department_claim=_env("FLOW_OIDC_DEPARTMENT_CLAIM", "department"),
            client_auth_method=_env("FLOW_OIDC_CLIENT_AUTH_METHOD", "client_secret_basic"),
            auto_provision=_env_bool("FLOW_OIDC_AUTO_PROVISION", False),
            state_secret=_env("FLOW_OIDC_STATE_SECRET") or client_secret,
        )

    @property
    def enabled(self) -> bool:
        return bool(
            self.issuer
            and self.client_id
            and self.client_secret
            and self.discovery_url
            and self.state_secret
        )


_DISCOVERY_CACHE: tuple[str, float, dict[str, Any]] | None = None
_JWKS_CACHE: tuple[str, float, dict[str, Any]] | None = None
_CACHE_SECONDS = 3600


def _cached_document(url: str, *, jwks: bool = False) -> dict[str, Any]:
    global _DISCOVERY_CACHE, _JWKS_CACHE
    cached = _JWKS_CACHE if jwks else _DISCOVERY_CACHE
    now = time.time()
    if cached and cached[0] == url and (now - cached[1]) < _CACHE_SECONDS:
        return cached[2]
    document = _fetch_json(url)
    entry = (url, now, document)
    if jwks:
        _JWKS_CACHE = entry
    else:
        _DISCOVERY_CACHE = entry
    return document


def _discovery(config: OidcConfig) -> dict[str, Any]:
    document = _cached_document(config.discovery_url)
    discovered_issuer = str(document.get("issuer") or "").rstrip("/")
    if discovered_issuer != config.issuer:
        raise HTTPException(502, "Corporate SSO issuer configuration does not match")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not document.get(field):
            raise HTTPException(502, f"Corporate SSO discovery is missing {field}")
    return document


def _rsa_rs256_verify(signing_input: bytes, signature: bytes, jwk: dict[str, Any]) -> None:
    """Verify an RS256 signature from an RSA JWK using PKCS#1 v1.5."""
    try:
        modulus = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
        exponent = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(401, "Invalid corporate SSO signing key") from exc
    size = (modulus.bit_length() + 7) // 8
    if size < 256 or len(signature) != size:
        raise HTTPException(401, "Invalid corporate SSO token signature")
    encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(signing_input).digest()
    padding_length = size - len(digest_info) - 3
    expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
    if padding_length < 8 or not hmac.compare_digest(encoded, expected):
        raise HTTPException(401, "Invalid corporate SSO token signature")


def _validate_claims(claims: dict[str, Any], config: OidcConfig, nonce: str) -> None:
    now = int(time.time())
    skew = 60
    if str(claims.get("iss") or "").rstrip("/") != config.issuer:
        raise HTTPException(401, "Invalid corporate SSO token issuer")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if config.client_id not in audiences:
        raise HTTPException(401, "Invalid corporate SSO token audience")
    if len(audiences) > 1 and claims.get("azp") != config.client_id:
        raise HTTPException(401, "Invalid corporate SSO authorized party")
    try:
        expires_at = int(claims.get("exp"))
    except (TypeError, ValueError):
        raise HTTPException(401, "Corporate SSO token has no expiry") from None
    if expires_at < now - skew:
        raise HTTPException(401, "Corporate SSO token has expired")
    if claims.get("nbf") is not None and int(claims["nbf"]) > now + skew:
        raise HTTPException(401, "Corporate SSO token is not active")
    if claims.get("iat") is not None and int(claims["iat"]) > now + skew:
        raise HTTPException(401, "Corporate SSO token was issued in the future")
    if not nonce or not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise HTTPException(401, "Invalid corporate SSO nonce")
    if not claims.get("sub"):
        raise HTTPException(401, "Corporate SSO token has no subject")


def _decode_and_verify_id_token(
    token: str,
    *,
    config: OidcConfig,
    discovery: dict[str, Any],
    nonce: str,
) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise HTTPException(401, "Invalid corporate SSO ID token")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
        signature = _b64url_decode(parts[2])
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(401, "Invalid corporate SSO ID token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise HTTPException(401, "Invalid corporate SSO ID token")
    if header.get("alg") != "RS256":
        raise HTTPException(401, "Corporate SSO must sign ID tokens with RS256")
    kid = str(header.get("kid") or "")
    keys = _cached_document(str(discovery["jwks_uri"]), jwks=True).get("keys") or []
    jwk = next(
        (
            key
            for key in keys
            if isinstance(key, dict)
            and key.get("kty") == "RSA"
            and (not kid or str(key.get("kid") or "") == kid)
            and key.get("use", "sig") == "sig"
        ),
        None,
    )
    if not jwk:
        # A key rotation can happen inside the normal one-hour cache window.
        global _JWKS_CACHE
        _JWKS_CACHE = None
        keys = _cached_document(str(discovery["jwks_uri"]), jwks=True).get("keys") or []
        jwk = next(
            (key for key in keys if isinstance(key, dict) and key.get("kty") == "RSA" and str(key.get("kid") or "") == kid),
            None,
        )
    if not jwk:
        raise HTTPException(401, "Corporate SSO signing key was not found")
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    _rsa_rs256_verify(signing_input, signature, jwk)
    _validate_claims(claims, config, nonce)
    return claims


def seal_login_state(payload: dict[str, Any], secret: str) -> str:
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64url_encode(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def open_login_state(value: str, secret: str, *, max_age: int = 600) -> dict[str, Any]:
    try:
        body, supplied_signature = value.split(".", 1)
        expected_signature = _b64url_encode(
            hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature")
        payload = json.loads(_b64url_decode(body))
        issued_at = int(payload["issued_at"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(401, "Invalid or missing corporate SSO login state") from exc
    if abs(int(time.time()) - issued_at) > max_age:
        raise HTTPException(401, "Corporate SSO login state has expired")
    return payload


class OidcAuthProvider(AuthProvider):
    name = "oidc"
    kind = "sso"
    label = "SSO"

    def config(self) -> OidcConfig:
        return OidcConfig.load()

    def enabled(self) -> bool:
        return self.config().enabled

    def start_url(self) -> str:
        return "/api/auth/sso/oidc/start"

    def begin(self, redirect_uri: str) -> tuple[str, dict[str, Any]]:
        config = self.config()
        if not config.enabled:
            raise HTTPException(503, "Corporate SSO is not configured")
        discovery = _discovery(config)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
        context = {
            "state": state,
            "nonce": nonce,
            "code_verifier": verifier,
            "redirect_uri": config.redirect_uri or redirect_uri,
            "issued_at": int(time.time()),
        }
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": config.client_id,
                "redirect_uri": context["redirect_uri"],
                "scope": config.scope,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{discovery['authorization_endpoint']}?{query}", context

    def authenticate(self, credential: Any) -> AuthIdentity:
        config = self.config()
        code = str((credential or {}).get("code") or "")
        nonce = str((credential or {}).get("nonce") or "")
        redirect_uri = str((credential or {}).get("redirect_uri") or "")
        verifier = str((credential or {}).get("code_verifier") or "")
        if not code or not nonce or not redirect_uri or not verifier:
            raise HTTPException(401, "Incomplete corporate SSO callback")
        discovery = _discovery(config)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": config.client_id,
            "code_verifier": verifier,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        if config.client_auth_method == "client_secret_post":
            form["client_secret"] = config.client_secret
        elif config.client_auth_method == "client_secret_basic":
            credentials = base64.b64encode(f"{config.client_id}:{config.client_secret}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {credentials}"
        else:
            raise HTTPException(500, "Unsupported FLOW_OIDC_CLIENT_AUTH_METHOD")
        token_response = _fetch_json(
            str(discovery["token_endpoint"]),
            data=urllib.parse.urlencode(form).encode("utf-8"),
            headers=headers,
        )
        if token_response.get("error"):
            raise HTTPException(401, "Corporate SSO rejected the login code")
        claims = _decode_and_verify_id_token(
            str(token_response.get("id_token") or ""),
            config=config,
            discovery=discovery,
            nonce=nonce,
        )
        username = str(
            claims.get(config.username_claim)
            or claims.get("preferred_username")
            or claims.get("email")
            or ""
        ).strip()
        safe_claims: dict[str, Any] = {"sub": str(claims.get("sub") or "")}
        department = claims.get(config.department_claim)
        if department not in (None, "", []):
            safe_claims["department"] = department
        for key in ("departments", "dept", "department_name", "org", "org_name", "groups"):
            if key in claims and key not in safe_claims:
                safe_claims[key] = claims[key]
        return self.resolve_identity(
            username=username,
            claims=safe_claims,
            auto_provision=config.auto_provision,
            name=str(claims.get("name") or ""),
            email=str(claims.get("email") or ""),
        )


OIDC_PROVIDER = register_provider(OidcAuthProvider())


__all__ = (
    "OIDC_PROVIDER",
    "OidcAuthProvider",
    "OidcConfig",
    "open_login_state",
    "seal_login_state",
)
