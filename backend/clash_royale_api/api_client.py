import asyncio
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import httpx

from api_key_store import KeyStore, LEASE_CLEANUP_MARGIN_S

CLASH_BASE_URL = "https://api.clashroyale.com/v1"
ALPHABET = set("0289PYLQGRJCUV")  # Supercell tag alphabet


class ClashRoyaleMaintenanceError(Exception):
    """Raised when the Clash Royale API is in maintenance mode."""

    def __init__(
        self,
        detail: str = "Clash Royale API is in maintenance. Try again later.",
        code: int = 503,
    ):
        super().__init__(detail)
        self.detail = detail
        self.code = code


class ClashRoyalePlayerCheckError(Exception):
    """Give the add route a specific failure without exposing raw httpx errors."""

    default_detail = "Clash Royale API request failed"

    def __init__(self):
        self.detail = self.default_detail
        super().__init__(self.detail)


class ClashRoyaleInvalidTagError(ClashRoyalePlayerCheckError):
    default_detail = "Invalid player tag"


class ClashRoyalePlayerNotFoundError(ClashRoyalePlayerCheckError):
    default_detail = "Player not found"


class ClashRoyaleAuthError(ClashRoyalePlayerCheckError):
    default_detail = "Clash Royale rejected the API token or IP address"


class ClashRoyaleConnectionError(ClashRoyalePlayerCheckError):
    default_detail = "Clash Royale API could not be reached"


class ClashRoyaleInvalidResponseError(ClashRoyalePlayerCheckError):
    default_detail = "Clash Royale API returned no player name"


class ClashRoyaleAPI:
    """
    Async Clash Royale API client backed by a shared key store.

    Reuses one httpx.AsyncClient for connection pooling. A lease is acquired
    for each outbound request, so concurrent callers do not reserve a token
    while they are still validating input, reading the cache, or writing data.
    """

    def __init__(
        self,
        key_store: KeyStore,
        base_url: str = CLASH_BASE_URL,
        timeout_s: float = 5.0,
    ):
        self.key_store = key_store
        self._base_url = base_url.rstrip("/")  # Always remove trailing "/" of base url
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=timeout_s, read=timeout_s, write=timeout_s, pool=timeout_s
            ),
            headers={"Accept": "application/json", "User-Agent": "royale-analytics"},
        )

    # --- helpers -------------------------------------------------------------
    @staticmethod
    def check_tag_syntax(player_tag: str) -> bool:
        """
        Check for one leading '#', 4-12 tag characters, and the Supercell alphabet.

        Args:
            player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

        Returns:
            bool: True if valid, False otherwise
        """

        # Strip the tag
        tag = player_tag.strip()

        if not tag.startswith("#") or tag.count("#") != 1:
            return False

        core = tag[1:]  # Part without the leading '#'

        # NOTE Match the frontend's current 4-12 limit. Update both validators if Clash
        # Royale starts (or already is) issuing shorter or longer tags.
        if len(core) < 4 or len(core) > 12:
            return False

        # Check if the tag without the '#' is only numbers and upper letters
        if not all(ch in ALPHABET for ch in core):
            return False

        # Valid if all checks passed
        return True

    @staticmethod
    def _url_encode_player_tag(player_tag: str):
        """
        URL encodes a Clash Royale player tag for use in API requests.

        Replaces the '#' character with '%23' to make the player tag URL-safe
        for use in HTTP requests to the Clash Royale API.

        Args:
            player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

        Returns:
            str: URL-encoded player tag (e.g., "%23YYRJQY28")
        """
        return player_tag.replace("#", "%23")

    @staticmethod
    def _check_maintenance(response):
        """
        Checks if the Clash Royale API is in maintenance mode.

        Args:
            response (Any): Parsed API response.

        Raises:
            ClashRoyaleMaintenanceError: If the API is in maintenance mode.
        """
        reason = response[0] if isinstance(response, list) and response else response
        if isinstance(reason, dict) and reason.get("reason") == "inMaintenance":
            raise ClashRoyaleMaintenanceError(
                "Clash Royale API is in maintenance mode. Try again later."
            )

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        """Read Retry-After seconds or an HTTP date; the store applies its cap."""
        value = response.headers.get("Retry-After")
        if not value:
            return 0.0
        try:
            return max(0.0, float(value))
        except ValueError:
            # HTTP dates commonly use IMF-fixdate. Accept the two older HTTP
            # date forms as well.
            formats = (
                "%a, %d %b %Y %H:%M:%S GMT",
                "%A, %d-%b-%y %H:%M:%S GMT",
                "%a %b %d %H:%M:%S %Y",
            )
            for date_format in formats:
                try:
                    date = datetime.strptime(value, date_format).replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    continue
                return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            return 0.0

    async def probe_key(self, key: str) -> str:
        """Test one key directly for startup validation or maintenance recovery.

        The store calls this only under its distributed validator/probe lock.
        A 401/403 is a key or IP authorization failure. A 429, network error,
        or maintenance response is temporary and must not invalidate the key.
        """
        try:
            response = await self._client.get(
                "/cards", headers={"Authorization": f"Bearer {key}"}
            )
            try:
                self._check_maintenance(response.json())
            except ValueError:
                pass
            if response.status_code in (401, 403):
                return "invalid"
            if response.status_code == 429:
                return "rate_limited"
            return "usable" if response.is_success else "temporary"
        except ClashRoyaleMaintenanceError:
            return "maintenance"
        except httpx.RequestError:
            return "temporary"

    async def _request(self, endpoint: str):
        """
        Makes an authenticated HTTP GET request to the Clash Royale API.

        Sends a GET request to the specified endpoint with the API key in the
        Authorization header. Automatically raises an exception for HTTP error
        status codes and returns the JSON response.

        Args:
            endpoint (str): The API endpoint path (e.g., "/players/{tag}")
            already with the player tag url encoded, if needed in the endpoint

        Returns:
            dict: JSON response data from the API

        Raises:
            RuntimeError: If the HTTP client is closed
            HTTPStatusError: If the API request fails (4xx/5xx status codes)
            RequestException: If there are network connectivity issues
        """

        if self._client is None:
            raise RuntimeError(
                "HTTP client is closed. Create a new instance or call within an async context."
            )

        # A 429 or rejected token can move this request to another eligible
        # key. Every attempt shares one deadline, so retries cannot multiply
        # the configured maximum waiting time.
        deadline = (
            asyncio.get_running_loop().time() + self.key_store.config.request_budget_s
        )
        for attempt in range(3):
            lease = await self.key_store.acquire(deadline)
            outcome = "other"
            retry_after = 0.0
            try:
                # The Redis lease has an expiry to recover from dead workers.
                # Finish this HTTP attempt before that expiry, including when
                # a caller configured a longer overall request budget.
                request_time_left = max(
                    0.01,
                    min(
                        deadline - asyncio.get_running_loop().time(),
                        self.key_store.config.lease_s - LEASE_CLEANUP_MARGIN_S,
                    ),
                )
                try:
                    async with asyncio.timeout(request_time_left):
                        resp = await self._client.get(
                            endpoint, headers={"Authorization": f"Bearer {lease.key}"}
                        )
                except TimeoutError as exc:
                    raise httpx.ReadTimeout(
                        "Clash Royale request deadline exceeded"
                    ) from exc
                try:
                    payload = resp.json()
                    # Maintenance can be reported in an error body. Detect it
                    # before raise_for_status turns it into a generic 5xx.
                    self._check_maintenance(payload)
                except ValueError:
                    payload = None
                if resp.status_code == 429:
                    outcome = "rate_limited"
                    retry_after = self._retry_after(resp)
                elif resp.status_code in (401, 403):
                    outcome = "auth_failed"
                elif resp.is_success:
                    outcome = "success"
                if resp.status_code in (401, 403, 429) and attempt < 2:
                    # The finally block still reports the outcome and frees
                    # the lease before the next key is requested.
                    continue
                resp.raise_for_status()
                return payload if payload is not None else resp.json()
            except ClashRoyaleMaintenanceError:
                outcome = "maintenance"
                raise
            finally:
                # Cancellation and network failures also release the key.
                # Shielding lets the Redis release finish if this task stops.
                await asyncio.shield(
                    self.key_store.release(lease, outcome, retry_after)
                )

    async def check_connection(self):
        """Initialize the shared inventory and return configured/usable counts."""
        return await self.key_store.initialize(self.probe_key)

    async def check_existing_player(self, player_tag: str):
        """
        Validates the tag syntax and verifies the a player with that tag exists
        by fetching the player profile from the Clash Royale API.

        The full profile is returned, so the caller can store it as the first
        profile snapshot without a second request.

        Args:
            player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

        Returns:
            dict: The player's profile (with a non-empty "name") when the API
                confirms the player exists.

        Raises:
            ClashRoyalePlayerCheckError: If the tag or API response is invalid,
                or the Clash Royale API rejects or cannot complete the request.
            ClashRoyaleMaintenanceError: If Clash Royale is in maintenance.
        """

        player_tag = player_tag.strip()
        if not self.check_tag_syntax(player_tag):
            raise ClashRoyaleInvalidTagError()

        try:
            player_info = await self.get_player_info(player_tag)
        except httpx.HTTPStatusError as e:
            # Only 404 proves the player is missing. Auth and API failures need
            # their own errors so the add route does not call them "not found".
            status = e.response.status_code
            if status == 404:
                raise ClashRoyalePlayerNotFoundError() from e
            if status in (401, 403):
                raise ClashRoyaleAuthError() from e
            raise ClashRoyalePlayerCheckError() from e
        except httpx.RequestError as e:
            raise ClashRoyaleConnectionError() from e

        # A successful request without a name does not prove the tag is missing.
        # This should not happen if the CR API is working as expected.
        if not isinstance(player_info, dict) or not player_info.get("name"):
            raise ClashRoyaleInvalidResponseError()

        return player_info

    async def get_player_battle_logs(self, player_tag: str):
        """
        Fetches battle logs for a specific player from the Clash Royale API.

        Makes an HTTP GET request to the Clash Royale API to retrieve the battle
        history for the specified player. The response contains a list of recent
        battle logs with detailed information about each match.

        Args:
            player_tag (str): The player tag (e.g., "#YYRJQY28")

        Returns:
            list: List of battle log dictionaries from the API response

        Raises:
            HTTPError: If the API request fails (4xx/5xx status codes)
            RequestException: If there are network connectivity issues
        """

        if not self.check_tag_syntax(player_tag):
            raise ValueError(f"Invalid player tag syntax: {player_tag!r}")

        tag = self._url_encode_player_tag(player_tag)
        return await self._request(f"/players/{tag}/battlelog")

    async def get_player_info(self, player_tag: str):
        """
        Fetches player information for a specific player from the Clash Royale API.

        Makes an HTTP GET request to the Clash Royale API to retrieve the profile
        information for the specified player.

        Args:
            player_tag (str): The player tag (e.g., "#YYRJQY28")

        Returns:
            list: All of the player's stats and information

        Raises:
            HTTPError: If the API request fails (4xx/5xx status codes)
            RequestException: If there are network connectivity issues
        """

        if not self.check_tag_syntax(player_tag):
            raise ValueError(f"Invalid player tag syntax: {player_tag!r}")

        tag = self._url_encode_player_tag(player_tag)
        return await self._request(f"/players/{tag}")

    async def get_cards(self):
        """
        Fetches every card's information from the Clash Royale API.

        Makes an HTTP GET request to the Clash Royale API to retrieve all game cards.

        Returns:
            list: All cards in the game as items

        Raises:
            HTTPError: If the API request fails (4xx/5xx status codes)
            RequestException: If there are network connectivity issues
        """

        return await self._request("/cards")

    async def get_path_of_legend_season_rankings(
        self, season_id: str, limit: int, after: str | None = None
    ):
        """
        Fetches one page of a finished season's global Path of Legend leaderboard.

        Path of Legend is the ranked mode, with a leaderboard that resets
        every season; the trophy road has its own, separate rankings.

        Args:
            season_id (str): Season id as "YYYY-MM" (e.g., "2026-09")
            limit (int): Players in the page
            after (str | None): paging.cursors.after of the previous page,
                None for the first page

        Returns:
            dict: {"items": [{"tag", "name", "rank", ...}], "paging":
                {"cursors": {"after"?}}}, without "after" on the last page

        Raises:
            HTTPError: If the API request fails (4xx/5xx status codes), e.g.
                404 for a season without a leaderboard
            RequestException: If there are network connectivity issues
        """

        params = {"limit": limit}
        if after is not None:
            params["after"] = after
        # The cursor is opaque and may contain characters a URL reserves
        return await self._request(
            f"/locations/global/pathoflegend/{quote(season_id, safe='')}"
            f"/rankings/players?{urlencode(params)}"
        )

    async def close(self):
        """
        Closes the HTTP client and releases all resources.

        Should be called when the API client is no longer needed to properly
        clean up connections and avoid resource leaks.
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None
