import asyncio
import json
import logging.config
import os
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from logging import Logger, getLogger
from pathlib import Path
from typing import Any

from aiohttp import ClientSession
from apyhiveapi import Hive
from apyhiveapi.helper import hive_exceptions

from hive_app.common.config import HiveSettings
from hive_app.common.exceptions import HiveApiUnavailable, HiveReauthRequired
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

_RESUME_REQUIRES_RELOGIN_MESSAGE = (
    "Hive's persisted session can no longer be resumed (e.g. the remembered "
    "device is no longer recognized by Cognito); a live SMS 2FA code is "
    "needed to recover."
)
_NO_REMEMBERED_DEVICE_MESSAGE = (
    "Hive login completed but did not yield a refresh token and remembered "
    "device (Cognito offered no device to remember), so the next restart "
    "would need another SMS code."
)
_LOGIN_REQUIRES_SMS_MESSAGE = (
    "Hive login requires a live SMS 2FA code; a headless service cannot supply one."
)

# Every apyhiveapi exception meaning "a live human must redo SMS 2FA login",
# whichever path surfaces it. HiveInvalidUsername/HiveInvalidPassword are
# deliberately absent: config-time credential errors, not this scenario.
_REAUTH_REQUIRED_EXCEPTIONS = (
    hive_exceptions.HiveReauthRequired,
    hive_exceptions.HiveInvalidDeviceAuthentication,
    hive_exceptions.HiveAuthError,
    hive_exceptions.HiveFailedToRefreshTokens,
    hive_exceptions.HiveInvalid2FACode,
    hive_exceptions.HiveUnknownConfiguration,
)

_API_TIMED_OUT_ATTR = "_hive_app_api_timed_out"
_API_TIMED_OUT_MESSAGE = (
    "Hive's API timed out while starting a session, so the re-authentication "
    "requirement apyhiveapi reported is not real."
)


class HiveApiSource:
    """The real HiveSource implementation: a thin synchronous wrapper
    around apyhiveapi's async Cognito-SRP-authenticated client. Consumers
    (HeatingRetriever, HiveAuthenticator) see only the HiveSource Protocol's
    simple verbs -- none of apyhiveapi's Cognito/SRP/asyncio/device
    internals leak through this boundary.

    apyhiveapi is fully asyncio-based; this codebase is fully synchronous
    (schedule + threading.Thread workers). There's no existing
    async-bridging precedent elsewhere in this repo, so this wraps each
    call in its own asyncio.run() -- a fresh event loop (and fresh Hive/
    aiohttp.ClientSession) per call is unnecessary complexity to avoid at
    this cadence (120s heating polls) and is what aiohttp's ClientSession
    requires anyway, since it's bound to the loop that created it and can't
    be reused across separate asyncio.run() calls.

    Per apyhiveapi's own Testing Decisions (see the spec): this class is not
    unit-tested against a live or mocked Cognito flow -- nothing in this
    repo does that today, and it would mean re-implementing SRP math in
    tests. It's exercised only by construction/wiring; HeatingRetriever and
    HiveAuthenticator are tested against a fake HiveSource instead. This
    extends to this class's small non-auth helpers too (_climate_device,
    the boost/schedule field mapping in _fetch_heating_status) -- they're
    private implementation details of the one HiveSource verb this repo's
    own conventions test only through its public interface (mirroring
    PricingRetriever/test_pricing_retrieval.py's seam shape), not because
    they individually need Cognito to exercise.
    """

    def __init__(self, settings: HiveSettings, mariadb: MariaDBClient) -> None:
        self._settings = settings
        self._mariadb = mariadb
        self._auth_state_path = Path(settings.auth_state_path)

    # -- HiveSource: auth --

    def read_auth_state(self) -> HiveAuthState | None:
        if not self._auth_state_path.exists():
            return None
        try:
            raw = json.loads(self._auth_state_path.read_text(encoding="utf-8"))
            state = HiveAuthState(
                refresh_token=self._require_str(raw, "refresh_token"),
                device_group_key=self._require_str(raw, "device_group_key"),
                device_key=self._require_str(raw, "device_key"),
                device_password=self._require_str(raw, "device_password"),
                updated_at=datetime.fromisoformat(raw["updated_at"]),
            )
            return state
        except (OSError, KeyError, ValueError, TypeError) as e:
            logger.warning(
                f"Hive auth state file at {self._auth_state_path} is unreadable "
                f"or malformed -- treating as no prior successful login: {e}"
            )
            return None

    @staticmethod
    def _require_str(raw: dict[str, Any], key: str) -> str:
        # A plain isinstance check, not just key presence -- a hand-edited
        # or truncated auth state file can contain valid JSON with the
        # right keys but the wrong value types (e.g. a list or number where
        # a token string belongs), which would otherwise construct a
        # HiveAuthState that later fails Cognito's SRP flow unpredictably
        # instead of taking the clean corrupt-file fallback below.
        value = raw[key]
        if not isinstance(value, str):
            raise TypeError(
                f"expected '{key}' to be a string, got {type(value).__name__}"
            )
        return value

    def login(self) -> HiveAuthState:
        return asyncio.run(self._login())

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        return asyncio.run(self._resume(state))

    def interactive_login(self, code_provider: Callable[[], str]) -> HiveAuthState:
        return asyncio.run(self._interactive_login(code_provider))

    def persist_auth_state(self, state: HiveAuthState) -> None:
        # Written to a temp file in the same directory (so the rename below
        # is atomic, not cross-filesystem) then renamed into place, rather
        # than written directly to auth_state_path -- this avoids both a
        # crash-mid-write leaving a truncated/corrupt file, and a window
        # where the file briefly exists at the umask-default (potentially
        # world-readable) mode before permissions are tightened. mkstemp
        # creates the temp file already restricted to the owner (0o600 on
        # POSIX), so the target inherits that mode across the rename with
        # no separate chmod step needed.
        payload = asdict(state)
        payload["updated_at"] = state.updated_at.isoformat()
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=self._auth_state_path.parent,
            prefix=f".{self._auth_state_path.name}.",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as file:
                file.write(json.dumps(payload))
            os.replace(tmp_path, self._auth_state_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    async def _login(self) -> HiveAuthState:
        async with self._hive_session() as hive:
            await self._establish_session(hive, None)
            return self._auth_state_from_session(hive)

    async def _interactive_login(
        self, code_provider: Callable[[], str]
    ) -> HiveAuthState:
        async with self._hive_session() as hive:
            login_result = await hive.login()
            if login_result.get("ChallengeName") == hive.auth.SMS_MFA_CHALLENGE:
                await hive.sms2fa(code_provider(), login_result["Session"])
                if not hive.auth.device_key:
                    raise RuntimeError(_NO_REMEMBERED_DEVICE_MESSAGE)
                await hive.auth.device_registration()
            elif "AuthenticationResult" not in login_result:
                raise RuntimeError(
                    "hive.login() returned neither an AuthenticationResult nor "
                    f"an SMS_MFA challenge (ChallengeName="
                    f"{login_result.get('ChallengeName')!r})."
                )
            elif hive.auth.device_key and not hive.auth.device_password:
                # login() stored the new device's group/device keys from
                # NewDeviceMetadata but only device_registration() generates
                # and confirms the device password a restart's resume needs.
                await hive.auth.device_registration()
            await self._start_session(
                hive, session_config={}, reauth_message=_LOGIN_REQUIRES_SMS_MESSAGE
            )
            state = self._auth_state_from_session(hive)
            # Whichever path got here, every field a restart's resume needs
            # must be set -- a direct login that returns no NewDeviceMetadata
            # registers nothing, and a registration that yields no password
            # would leave a state that cannot resume.
            if not all(
                (
                    state.refresh_token,
                    state.device_group_key,
                    state.device_key,
                    state.device_password,
                )
            ):
                raise RuntimeError(_NO_REMEMBERED_DEVICE_MESSAGE)
            return state

    async def _resume(self, state: HiveAuthState) -> HiveAuthState:
        async with self._hive_session() as hive:
            await self._establish_session(hive, state)
            return self._auth_state_from_session(hive)

    @staticmethod
    async def _establish_session(hive: Hive, state: HiveAuthState | None) -> None:
        """Starts hive's Cognito session: a fresh interactive login if no
        auth state is available, otherwise a resume via token/device
        refresh. Shared by _login/_resume (HiveAuthenticator's startup path)
        and _fetch_heating_status's own self-healing fallback (poll path)
        so this state-is-None branching lives in exactly one place."""
        if state is None:
            logger.info("No persisted Hive auth state -- starting interactive login.")
            await HiveApiSource._fresh_login(hive)
        else:
            logger.info(
                "Persisted Hive auth state found -- resuming via token/device refresh."
            )
            await HiveApiSource._start_session(
                hive,
                session_config=HiveApiSource._resume_config(state),
                reauth_message=_RESUME_REQUIRES_RELOGIN_MESSAGE,
            )

    @staticmethod
    async def _fresh_login(hive: Hive) -> None:
        """Performs a genuine fresh interactive Hive login via hive.login()
        (HiveSession.login) -- apyhiveapi's actual dedicated entry point for
        this, distinct from hive.startSession() (used only to resume/refresh
        an already-authenticated session). login() drives a real Cognito
        USER_SRP_AUTH handshake and, on success, populates
        hive.tokens.tokenData itself via its own internal updateTokens()
        call -- but it does not call getDevices(), so hive.deviceList is
        still empty afterward; the follow-up _start_session call below (with
        an explicitly empty config -- see its own docstring for why) is what
        actually populates it.

        login()'s three outcomes, per its own "Business Rules" docstring in
        apyhiveapi: (1) full success -- "AuthenticationResult" present, also
        covers a device-registration challenge apyhiveapi handled
        transparently, indistinguishable from plain success here; (2)
        SMS_MFA challenge with no AuthenticationResult -- the account needs
        a live SMS 2FA code and (fresh login, no remembered device) has no
        device fallback; login() doesn't raise for this, it just returns
        the raw challenge dict, so this is the only place that can turn it
        into the reauth-required signal callers already handle; (3)
        anything else -- an exception login() itself raises: any member of
        _REAUTH_REQUIRED_EXCEPTIONS is translated to HiveReauthRequired
        here, while others (HiveInvalidUsername/HiveInvalidPassword/
        HiveApiError) propagate on their own. A *returned* dict matching
        neither outcome above needs an explicit raise here, or this method
        would silently return None -- the caller would then persist a
        HiveAuthState with an empty refresh_token as though login had
        succeeded, deferring the failure to a later, harder-to-diagnose
        resume attempt."""
        try:
            login_result = await hive.login()
        except _REAUTH_REQUIRED_EXCEPTIONS as e:
            raise HiveReauthRequired(_LOGIN_REQUIRES_SMS_MESSAGE) from e
        if "AuthenticationResult" in login_result:
            await HiveApiSource._start_session(
                hive, session_config={}, reauth_message=_LOGIN_REQUIRES_SMS_MESSAGE
            )
        elif login_result.get("ChallengeName") == hive.auth.SMS_MFA_CHALLENGE:
            raise HiveReauthRequired(_LOGIN_REQUIRES_SMS_MESSAGE)
        else:
            raise RuntimeError(
                "hive.login() returned a result with no AuthenticationResult "
                "and an unrecognised ChallengeName "
                f"({login_result.get('ChallengeName')!r}) -- apyhiveapi's "
                "documented login() outcomes are AuthenticationResult, a "
                "transparently-handled device-registration challenge (both "
                "covered above), or SMS_MFA; this result is none of those."
            )

    @staticmethod
    async def _start_session(
        hive: Hive, session_config: dict[str, Any], reauth_message: str
    ) -> None:
        """Runs hive.startSession(session_config), translating every
        apyhiveapi exception in _REAUTH_REQUIRED_EXCEPTIONS into this repo's
        own HiveReauthRequired type (see
        HiveReauthRequired's docstring) so every caller in this class
        raises/handles one consistent exception rather than duplicating this
        try/except at each call site. The one exception to that: apyhiveapi's
        own HiveReauthRequired raised after a Hive API timeout in this
        session is the empty-device-list artefact, not a real re-auth
        requirement, so it becomes HiveApiUnavailable (see
        _record_api_timeouts).

        session_config is a real dict, never None -- but it CAN be an
        explicitly empty {}, passed by _fresh_login right after a successful
        hive.login(). That's intentional, not the original no-real-login
        bug this class was fixed for (which called bare hive.startSession()
        with no prior hive.login() at all, so no real tokens existed yet for
        the token-refresh path an empty config falls through to): here,
        login() already populated real tokens on hive.tokens.tokenData, so
        an empty config correctly skips startSession()'s token/username/
        password/device_data-processing block (nothing to set) and falls
        straight through to that method's own getDevices() + createDevices()
        sequence, which is what actually populates hive.deviceList
        (createDevices() alone builds it; getDevices() alone does not).
        Passing _resume_config's blank-placeholder token shape here instead
        would overwrite those real tokens with blanks via updateTokens()'s
        "elif 'token' in tokens" branch, with tokenCreated too fresh for the
        90%-expiry refresh check to repair it -- hence the empty {} instead.
        Reusing this already-tested startSession() path rather than
        re-implementing getDevices()+createDevices() ourselves keeps this in
        sync with the real library's own sequence by construction."""
        try:
            await hive.startSession(session_config)
        except _REAUTH_REQUIRED_EXCEPTIONS as e:
            if isinstance(e, hive_exceptions.HiveReauthRequired) and getattr(
                hive, _API_TIMED_OUT_ATTR, False
            ):
                raise HiveApiUnavailable(_API_TIMED_OUT_MESSAGE) from e
            raise HiveReauthRequired(reauth_message) from e

    @asynccontextmanager
    async def _hive_session(self) -> AsyncIterator[Hive]:
        """Constructs a Hive bound to an explicitly-owned aiohttp
        ClientSession and closes that session on exit. Hive/HiveAsyncApi
        create their own ClientSession internally when none is passed
        (apyhiveapi/api/hive_async_api.py) with no way to close it
        afterward -- constructing our own and passing it in as `websession`
        is the only way to actually release it, since a fresh session is
        needed every call anyway (see this class's docstring on why one
        asyncio.run() per call, not a long-lived Hive instance)."""
        session = ClientSession()
        try:
            hive = Hive(
                username=self._settings.username,
                password=self._settings.password,
                websession=session,
            )
            self._record_api_timeouts(hive)
            yield hive
        finally:
            await session.close()

    @staticmethod
    def _record_api_timeouts(hive: Hive) -> None:
        """Flags hive when its getAll() times out. apyhiveapi's getDevices
        swallows that TimeoutError, so startSession later fails with a bare
        HiveReauthRequired indistinguishable from a genuine one; the flag is
        the only way _start_session can tell them apart."""
        get_all = hive.api.getAll

        async def get_all_recording_timeouts(*args: Any, **kwargs: Any) -> Any:
            try:
                return await get_all(*args, **kwargs)
            except TimeoutError:
                setattr(hive, _API_TIMED_OUT_ATTR, True)
                raise

        hive.api.getAll = get_all_recording_timeouts

    @staticmethod
    def _resume_config(state: HiveAuthState) -> dict[str, Any]:
        # With a "tokens" key present (even with empty id/access tokens --
        # only the refresh token matters here, see
        # HiveSession.startSession/updateTokens) startSession() goes
        # straight to getDevices(), refreshing via
        # REFRESH_TOKEN_AUTH/DEVICE_SRP_AUTH as needed. It never performs a
        # fresh interactive login regardless of whether "tokens" is present
        # or absent -- that's hive.login()'s job, not startSession()'s (see
        # _establish_session's fresh-login branch, which calls hive.login()
        # directly instead).
        return {
            "tokens": {
                "token": "",  # nosec B105 -- placeholder, not a credential; see comment above
                "refreshToken": state.refresh_token,
                "accessToken": "",  # nosec B105 -- same as "token" above
            },
            "device_data": (
                state.device_group_key,
                state.device_key,
                state.device_password,
            ),
        }

    @staticmethod
    def _auth_state_from_session(hive: Hive) -> HiveAuthState:
        return HiveAuthState(
            refresh_token=hive.tokens.tokenData.get("refreshToken", ""),
            device_group_key=hive.auth.device_group_key or "",
            device_key=hive.auth.device_key or "",
            device_password=hive.auth.device_password or "",
            updated_at=datetime.now(UTC),
        )

    # -- HiveSource: heating status --

    def fetch_heating_status(self) -> HeatingStatus:
        return asyncio.run(self._fetch_heating_status())

    async def _fetch_heating_status(self) -> HeatingStatus:
        # Re-establishing the session on every poll (rather than reusing a
        # long-lived Hive instance) can itself rotate the refresh token/
        # device keys via Cognito's REFRESH_TOKEN_AUTH -- so the resulting
        # state is always re-persisted below, not just at startup/resume,
        # or a later restart could resume with tokens Cognito has already
        # superseded.
        state = self.read_auth_state()
        # If state is None, no prior HiveAuthenticator.authenticate() run
        # ever succeeded (e.g. it failed at startup). _establish_session
        # falling back to a fresh login in that case -- rather than this
        # method raising -- means each of this job's retry-with-backoff
        # attempts is itself a recovery attempt, instead of a permanent
        # failure loop until the process is restarted.
        async with self._hive_session() as hive:
            await self._establish_session(hive, state)
            # Persisted immediately after the session starts, before the
            # heating.get*() calls below -- if one of those triggers
            # apyhiveapi's own internal 90%-lifetime token auto-refresh
            # mid-poll, that rotation wouldn't be captured until the row is
            # next re-read on the following poll. Negligible in practice
            # (tokens were just minted moments earlier in this same call)
            # and self-heals within one 120-second cycle either way.
            self.persist_auth_state(self._auth_state_from_session(hive))

            device = self._climate_device(hive)
            current_temp = await hive.heating.getCurrentTemperature(device)
            target_temp = await hive.heating.getTargetTemperature(device)
            mode = await hive.heating.getMode(device)
            heating_state = await hive.heating.getState(device)
            boost_status = await hive.heating.getBoostStatus(device)
            schedule = await hive.heating.getScheduleNowNextLater(device) or {}
            working = self._as_working_flag(
                await hive.heating.getCurrentOperation(device)
            )

        return HeatingStatus(
            polled_at=datetime.now(UTC),
            current_temp=current_temp,
            target_temp=target_temp,
            mode=mode,
            state=heating_state,
            boost_active=boost_status == "ON",
            # getBoostTime() returns the raw device "boost" state value
            # (minutes remaining vs. an absolute timestamp is unconfirmed
            # against a real payload -- see
            # .agent-docs/research/hive-api-access-approach.md, and issue
            # #513's identical "confirm against real data, don't guess"
            # stance for heating-active semantics), so it isn't converted
            # into an absolute end-timestamp here.
            boost_ends_at=None,
            schedule=schedule,
            working=working,
        )

    @staticmethod
    def _as_working_flag(reported: object) -> bool | None:
        """The thermostat's own "heating is working" report, or None (logged)
        when it is missing or not a boolean. Only the type name is logged."""
        if isinstance(reported, bool):
            return reported
        logger.warning(
            "Hive thermostat 'working' report is missing or unexpected "
            "(type %s); storing null.",
            type(reported).__name__,
        )
        return None

    @staticmethod
    def _climate_device(hive: Hive) -> dict[str, Any]:
        climate_devices = hive.deviceList.get("climate", [])
        if not climate_devices:
            raise RuntimeError(
                "No Hive climate (heating) device found on this account."
            )
        return climate_devices[0]

    def persist_heating_status(self, status: HeatingStatus) -> None:
        self._mariadb.write_heating_status(status)
