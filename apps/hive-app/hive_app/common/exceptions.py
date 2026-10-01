class ConfigurationFileError(Exception):
    pass


class HiveReauthRequired(Exception):
    """Raised when Hive's session can no longer be resumed and a live SMS
    2FA code is needed to recover (the remembered device is no longer
    recognized, tokens can't be refreshed, etc. -- see
    hive_client._REAUTH_REQUIRED_EXCEPTIONS for the apyhiveapi exceptions
    that map to this) -- a genuinely unrecoverable state for a headless
    service (see .agent-docs/research/hive-api-access-approach.md and
    ADR-0018). Kept distinct from apyhiveapi's own identically-named
    exception so callers (startup auth and the heating-poll job, both
    alerting via the shared ReauthAlert) depend only on this repo's own
    exception type, not an apyhiveapi implementation detail. Every other
    failure (network errors, ordinary API errors) is NOT this type, so
    generic job-failure handling treats it as an ordinary transient
    failure."""
