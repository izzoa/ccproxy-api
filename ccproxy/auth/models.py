"""Data models for authentication."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, SecretStr, field_validator


class OAuthToken(BaseModel):
    """OAuth token information from Claude credentials."""

    access_token: SecretStr = Field(..., alias="accessToken")
    refresh_token: SecretStr = Field(..., alias="refreshToken")
    expires_at: int | None = Field(None, alias="expiresAt")
    scopes: list[str] = Field(default_factory=list)
    subscription_type: str | None = Field(None, alias="subscriptionType")
    token_type: str = Field(default="Bearer", alias="tokenType")

    @field_validator("access_token", "refresh_token", mode="before")
    @classmethod
    def validate_tokens(cls, v: str | SecretStr | None) -> SecretStr | None:
        """Convert string values to SecretStr."""
        if v is None:
            return None
        if isinstance(v, str):
            return SecretStr(v)
        return v

    def __repr__(self) -> str:
        """Safe string representation that masks sensitive tokens."""
        access_token_str = self.access_token.get_secret_value()
        refresh_token_str = self.refresh_token.get_secret_value()

        access_preview = (
            f"{access_token_str[:8]}...{access_token_str[-8:]}"
            if len(access_token_str) > 16
            else "***"
        )
        refresh_preview = (
            f"{refresh_token_str[:8]}...{refresh_token_str[-8:]}"
            if len(refresh_token_str) > 16
            else "***"
        )

        expires_at = (
            datetime.fromtimestamp(self.expires_at / 1000, tz=UTC).isoformat()
            if self.expires_at is not None
            else "None"
        )
        return (
            f"OAuthToken(access_token='{access_preview}', "
            f"refresh_token='{refresh_preview}', "
            f"expires_at={expires_at}, "
            f"scopes={self.scopes}, "
            f"subscription_type='{self.subscription_type}', "
            f"token_type='{self.token_type}')"
        )

    @property
    def is_expired(self) -> bool:
        """Check if the token is expired."""
        if self.expires_at is None:
            # If no expiration info, assume not expired for backward compatibility
            return False
        now = datetime.now(UTC).timestamp() * 1000  # Convert to milliseconds
        return now >= self.expires_at

    @property
    def expires_at_datetime(self) -> datetime:
        """Get expiration as datetime object."""
        if self.expires_at is None:
            # Return a far future date if no expiration info
            return datetime.fromtimestamp(2147483647, tz=UTC)  # Year 2038
        return datetime.fromtimestamp(self.expires_at / 1000, tz=UTC)


class OrganizationInfo(BaseModel):
    """Organization information from OAuth API."""

    uuid: str
    name: str
    organization_type: str | None = None
    billing_type: str | None = None
    rate_limit_tier: str | None = None


class AccountInfo(BaseModel):
    """Account information from OAuth API."""

    uuid: str
    email: str
    full_name: str | None = None
    display_name: str | None = None
    has_claude_max: bool | None = None
    has_claude_pro: bool | None = None

    @property
    def email_address(self) -> str:
        """Compatibility property for email_address."""
        return self.email


class UserProfile(BaseModel):
    """User profile information from Anthropic OAuth API."""

    organization: OrganizationInfo | None = None
    account: AccountInfo | None = None


class ClaudeCredentials(BaseModel):
    """Claude credentials from the credentials file."""

    claude_ai_oauth: OAuthToken = Field(..., alias="claudeAiOauth")

    def __repr__(self) -> str:
        """Safe string representation that masks sensitive tokens."""
        return f"ClaudeCredentials(claude_ai_oauth={repr(self.claude_ai_oauth)})"


class ValidationResult(BaseModel):
    """Result of credentials validation."""

    valid: bool
    expired: bool | None = None
    credentials: ClaudeCredentials | None = None
    path: str | None = None


# Backwards compatibility - provide common aliases
User = UserProfile
Credentials = ClaudeCredentials
Profile = UserProfile
