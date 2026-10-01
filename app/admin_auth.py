"""Who may use /admin: a valid Cloudflare Access token (T-0049).

Cloudflare Access puts a signed JWT in the Cf-Access-Jwt-Assertion header of every
request it lets through. Checking it here (signature, audience, issuer, expiry) means
the admin pages stay shut for anything that reaches the container some other way.
Cloudflare's docs: "Validate JWTs" under Cloudflare Access.
"""
from __future__ import annotations

import jwt
from starlette.concurrency import run_in_threadpool

HEADER = "Cf-Access-Jwt-Assertion"


class AccessDenied(Exception):
    pass


class AccessVerifier:
    """`signing_key` maps a token to its public key; by default Cloudflare's published
    keys for the team, fetched and cached by PyJWT. Tests pass their own."""

    def __init__(self, team_domain: str, audience: str, allowed_emails: frozenset[str] = frozenset(),
                 signing_key=None):
        self.issuer = f"https://{team_domain}"
        self.audience = audience
        self.allowed_emails = allowed_emails
        if signing_key is None:
            client = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
            signing_key = lambda token: client.get_signing_key_from_jwt(token).key  # noqa: E731
        self._signing_key = signing_key

    def _verify(self, token: str) -> str:
        try:
            key = self._signing_key(token)
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience,
                                issuer=self.issuer, options={"require": ["exp", "iat", "aud", "iss"]})
        except Exception as exc:                      # bad signature, expired, wrong aud, key fetch failed
            raise AccessDenied(f"invalid Access token: {type(exc).__name__}") from None
        email = str(claims.get("email", "")).lower()
        if not email:
            raise AccessDenied("Access token has no email")
        if self.allowed_emails and email not in self.allowed_emails:
            raise AccessDenied(f"{email} is not an admin")
        return email

    async def __call__(self, headers) -> str:
        """The admin's email, or AccessDenied."""
        token = headers.get(HEADER)
        if not token:
            raise AccessDenied("no Access token")
        # The first call may fetch Cloudflare's keys over the network: keep it off the event loop.
        return await run_in_threadpool(self._verify, token)
