"""OAuth sign-in against Google and GitHub, hand-rolled on httpx.

Only the authorization-code flow is needed, so the exchange is two POSTs and a
profile fetch; a framework would add a session middleware this app deliberately
does not have. The one non-obvious rule lives in ``fetch_verified_email``: an
email is only accepted when the provider itself vouches it is verified, because
possession of a verified inbox is this app's entire login credential.
"""

from dataclasses import dataclass

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)


class OAuthError(Exception):
    """The provider exchange failed or returned no usable, verified email."""


@dataclass(frozen=True, slots=True)
class OAuthProvider:
    """The endpoints and quirks of one OAuth provider."""

    name: str
    auth_url: str
    token_url: str
    userinfo_url: str
    scopes: str
    uses_pkce: bool


PROVIDERS: dict[str, OAuthProvider] = {
    "google": OAuthProvider(
        name="google",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes="openid email",
        uses_pkce=True,
    ),
    "github": OAuthProvider(
        name="github",
        auth_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user/emails",
        scopes="user:email",
        uses_pkce=False,
    ),
}


def credentials(provider: OAuthProvider) -> tuple[str, str] | None:
    """The configured (client_id, client_secret), or None when the provider is off."""
    if provider.name == "google":
        client_id, secret = settings.google_client_id, settings.google_client_secret
    else:
        client_id, secret = settings.github_client_id, settings.github_client_secret
    if not client_id or not secret:
        return None
    return client_id, secret


async def exchange_code(
    client: httpx.AsyncClient,
    provider: OAuthProvider,
    *,
    code: str,
    redirect_uri: str,
    pkce_verifier: str,
) -> str:
    """Trade the callback's authorization code for an access token.

    Raises:
        OAuthError: the provider refused the exchange or was unreachable.
    """
    creds = credentials(provider)
    if creds is None:
        raise OAuthError(f"{provider.name} OAuth is not configured.")
    client_id, client_secret = creds
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if provider.uses_pkce:
        data["code_verifier"] = pkce_verifier
    try:
        response = await client.post(
            provider.token_url, data=data, headers={"Accept": "application/json"}
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("oauth.exchange_unreachable", provider=provider.name, error=str(exc))
        raise OAuthError("Could not reach the sign-in provider.") from exc
    token = body.get("access_token") if isinstance(body, dict) else None
    if response.status_code >= 400 or not isinstance(token, str) or not token:
        # The provider's error body can name our own config problems; log it for
        # the operator, never echo it to the browser.
        log.warning(
            "oauth.exchange_rejected",
            provider=provider.name,
            status=response.status_code,
            body=response.text,
        )
        raise OAuthError("The sign-in provider rejected the login.")
    return token


async def fetch_verified_email(
    client: httpx.AsyncClient, provider: OAuthProvider, access_token: str
) -> str:
    """Return the account's verified email, the only claim this app needs.

    Raises:
        OAuthError: the profile was unreachable or carries no verified email.
    """
    try:
        response = await client.get(
            provider.userinfo_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("oauth.userinfo_unreachable", provider=provider.name, error=str(exc))
        raise OAuthError("Could not read the sign-in provider profile.") from exc
    if response.status_code >= 400:
        log.warning("oauth.userinfo_rejected", provider=provider.name, status=response.status_code)
        raise OAuthError("The sign-in provider rejected the login.")
    email = _extract_verified_email(provider, body)
    if email is None:
        raise OAuthError("The account has no verified email to sign in with.")
    return email


def _extract_verified_email(provider: OAuthProvider, body: object) -> str | None:
    if provider.name == "google":
        # OIDC userinfo: one object; email_verified must be affirmatively true.
        if isinstance(body, dict) and body.get("email_verified") is True:
            email = body.get("email")
            if isinstance(email, str) and email:
                return email
        return None
    # GitHub /user/emails: prefer the primary verified address, fall back to any
    # verified one (a hidden primary still lists here thanks to the user:email scope).
    if not isinstance(body, list):
        return None
    verified = [
        item
        for item in body
        if isinstance(item, dict)
        and item.get("verified") is True
        and isinstance(item.get("email"), str)
    ]
    for item in verified:
        if item.get("primary") is True:
            return str(item["email"])
    if verified:
        return str(verified[0]["email"])
    return None
