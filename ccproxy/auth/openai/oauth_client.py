"""OpenAI OAuth PKCE client implementation."""

import asyncio
import base64
import contextlib
import hashlib
import secrets
import urllib.parse
import webbrowser
from datetime import UTC, datetime, timedelta

import httpx
import structlog
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse

from plugins.codex.config import CodexSettings

from .credentials import OpenAICredentials, OpenAITokenManager


logger = structlog.get_logger(__name__)


class OpenAIOAuthClient:
    """OpenAI OAuth PKCE flow client."""

    def __init__(
        self, settings: CodexSettings, token_manager: OpenAITokenManager | None = None
    ):
        """Initialize OAuth client.

        Args:
            settings: Codex configuration settings
            token_manager: Token manager for credential storage
        """
        self.settings = settings
        self.token_manager = token_manager or OpenAITokenManager()
        self._server_task: asyncio.Task[None] | None = None
        self._auth_complete = asyncio.Event()
        self._auth_result: OpenAICredentials | None = None
        self._auth_error: str | None = None

    def _generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE code verifier and challenge."""
        # Generate code verifier (43-128 characters)
        code_verifier = (
            base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        )

        # Generate code challenge
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        return code_verifier, code_challenge

    def _build_auth_url(self, code_challenge: str, state: str) -> str:
        """Build OAuth authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self.settings.oauth.client_id,
            "redirect_uri": self.settings.get_redirect_uri(),
            "scope": " ".join(self.settings.oauth.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        query_string = urllib.parse.urlencode(params)
        return f"{self.settings.oauth.base_url}/oauth/authorize?{query_string}"

    async def _exchange_code_for_tokens(
        self, code: str, code_verifier: str
    ) -> OpenAICredentials:
        """Exchange authorization code for tokens."""
        token_url = f"{self.settings.oauth.base_url}/oauth/token"

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.get_redirect_uri(),
            "client_id": self.settings.oauth.client_id,
            "code_verifier": code_verifier,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    token_url, data=data, headers=headers, timeout=30.0
                )
                response.raise_for_status()

                token_data = response.json()

                # Calculate expiration time
                expires_in = token_data.get("expires_in", 3600)  # Default 1 hour
                expires_at = datetime.now(UTC).replace(microsecond=0) + timedelta(
                    seconds=expires_in
                )

                # Create credentials (account_id will be extracted from access_token)
                credentials = OpenAICredentials(
                    access_token=token_data["access_token"],
                    refresh_token=token_data.get("refresh_token", ""),
                    expires_at=expires_at,
                    account_id="",  # Will be auto-extracted by validator
                    active=True,
                )

                return credentials

            except httpx.HTTPStatusError as e:
                error_detail = "Unknown error"
                try:
                    error_data = e.response.json()
                    error_detail = error_data.get(
                        "error_description", error_data.get("error", str(e))
                    )
                except Exception:
                    error_detail = str(e)

                raise ValueError(f"Token exchange failed: {error_detail}") from e
            except Exception as e:
                raise ValueError(f"Token exchange request failed: {e}") from e

    def _create_callback_app(self, code_verifier: str, expected_state: str) -> FastAPI:
        """Create FastAPI app to handle OAuth callback."""
        app = FastAPI(title="OpenAI OAuth Callback")

        @app.get("/auth/callback")
        async def oauth_callback(request: Request) -> Response:
            """Handle OAuth callback."""
            params = dict(request.query_params)

            # Check for error in callback
            if "error" in params:
                error_desc = params.get("error_description", params["error"])
                self._auth_error = f"OAuth error: {error_desc}"
                self._auth_complete.set()
                return HTMLResponse(
                    """
                    <html>
                    <head><title>Authentication Failed</title></head>
                    <body>
                        <h1>Authentication Failed</h1>
                        <p>Error: """
                    + error_desc
                    + """</p>
                        <p>You can close this window.</p>
                        <script>setTimeout(() => window.close(), 3000);</script>
                    </body>
                    </html>
                    """,
                    status_code=400,
                )

            # Verify state parameter
            received_state = params.get("state")
            if received_state != expected_state:
                self._auth_error = "Invalid state parameter"
                self._auth_complete.set()
                return HTMLResponse(
                    """
                    <html>
                    <head><title>Authentication Failed</title></head>
                    <body>
                        <h1>Authentication Failed</h1>
                        <p>Invalid state parameter. Possible CSRF attack.</p>
                        <p>You can close this window.</p>
                        <script>setTimeout(() => window.close(), 3000);</script>
                    </body>
                    </html>
                    """,
                    status_code=400,
                )

            # Get authorization code
            auth_code = params.get("code")
            if not auth_code:
                self._auth_error = "No authorization code received"
                self._auth_complete.set()
                return HTMLResponse(
                    """
                    <html>
                    <head><title>Authentication Failed</title></head>
                    <body>
                        <h1>Authentication Failed</h1>
                        <p>No authorization code received.</p>
                        <p>You can close this window.</p>
                        <script>setTimeout(() => window.close(), 3000);</script>
                    </body>
                    </html>
                    """,
                    status_code=400,
                )

            # Exchange code for tokens
            try:
                credentials = await self._exchange_code_for_tokens(
                    auth_code, code_verifier
                )

                # Save credentials
                success = await self.token_manager.save_credentials(credentials)
                if not success:
                    raise ValueError("Failed to save credentials")

                self._auth_result = credentials
                self._auth_complete.set()

                return HTMLResponse(
                    """
                    <html>
                    <head><title>Authentication Successful</title></head>
                    <body>
                        <h1>Authentication Successful!</h1>
                        <p>You have successfully authenticated with OpenAI.</p>
                        <p>You can close this window and return to the terminal.</p>
                        <script>setTimeout(() => window.close(), 3000);</script>
                    </body>
                    </html>
                    """
                )

            except Exception as e:
                logger.error("Token exchange failed", error=str(e))
                self._auth_error = f"Token exchange failed: {e}"
                self._auth_complete.set()
                return HTMLResponse(
                    f"""
                    <html>
                    <head><title>Authentication Failed</title></head>
                    <body>
                        <h1>Authentication Failed</h1>
                        <p>Token exchange failed: {e}</p>
                        <p>You can close this window.</p>
                        <script>setTimeout(() => window.close(), 3000);</script>
                    </body>
                    </html>
                    """,
                    status_code=500,
                )

        return app

    async def _run_callback_server(self, app: FastAPI) -> None:
        """Run callback server."""
        config = uvicorn.Config(
            app=app,
            host="127.0.0.1",
            port=self.settings.callback_port,
            log_level="warning",  # Reduce noise
            access_log=False,
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def authenticate(self, open_browser: bool = True) -> OpenAICredentials:
        """Perform OAuth PKCE flow.

        Args:
            open_browser: Whether to automatically open browser

        Returns:
            OpenAI credentials

        Raises:
            ValueError: If authentication fails
        """
        # Reset state
        self._auth_complete.clear()
        self._auth_result = None
        self._auth_error = None

        # Generate PKCE parameters
        code_verifier, code_challenge = self._generate_pkce_pair()
        state = secrets.token_urlsafe(32)

        # Create callback app
        app = self._create_callback_app(code_verifier, state)

        # Start callback server
        self._server_task = asyncio.create_task(self._run_callback_server(app))

        # Give server time to start
        await asyncio.sleep(1)

        # Build authorization URL
        auth_url = self._build_auth_url(code_challenge, state)

        logger.info("Starting OpenAI OAuth flow")
        print("\nPlease visit this URL to authenticate with OpenAI:")
        print(f"{auth_url}\n")

        if open_browser:
            try:
                webbrowser.open(auth_url)
                print("Opening browser...")
            except Exception as e:
                logger.warning("Failed to open browser automatically", error=str(e))
                print("Please copy and paste the URL above into your browser.")

        print("Waiting for authentication to complete...")

        try:
            # Wait for authentication to complete (with timeout)
            await asyncio.wait_for(self._auth_complete.wait(), timeout=300)  # 5 minutes

            if self._auth_error:
                raise ValueError(self._auth_error)

            if not self._auth_result:
                raise ValueError("Authentication completed but no credentials received")

            logger.info("OpenAI authentication successful")  # type: ignore[unreachable]
            return self._auth_result

        except TimeoutError as e:
            raise ValueError("Authentication timed out (5 minutes)") from e
        finally:
            # Clean up server
            if self._server_task and not self._server_task.done():
                self._server_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._server_task
