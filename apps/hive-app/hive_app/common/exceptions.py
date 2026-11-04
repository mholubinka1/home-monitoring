class ConfigurationFileError(Exception):
    pass


class HiveReauthRequired(Exception):
    """Raised when Hive's session can no longer be resumed and a live SMS
    2FA code is needed to recover (the remembered device is no longer
    recognized, tokens can't be refreshed, etc. -- see
    hive_client._REAUTH_REQUIRED_EXCEPTIONS for the apyhiveapi exceptions
    that map to this) -- a genuinely unrecoverable state for a headless
    service (see .agent-docs/research/hive-api-access-approach.md). Kept
    distinct from apyhiveapi's own identically-named exception so callers
    depend only on this repo's own exception type, not an apyhiveapi
    implementation detail. It is an ordinary poll error: retried and
    recorded as a failed job_run. The only live alert is the Hive data-lost
    alert raised by the watchdog when data stops arriving (ADR-0031). A Hive
    API timeout is a different type (see HiveApiUnavailable)."""


class HiveApiUnavailable(Exception):
    """Raised when Hive's API timed out while a session was starting, so
    apyhiveapi saw no devices and reported a re-authentication requirement
    that is not real. An ordinary transient poll failure: retried and
    recorded."""
