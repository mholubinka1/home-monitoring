# Issues: bugfix-hive-timeout-not-reauth

## hive-app: classify a Hive API timeout as transient, not re-authentication required — [#600](https://github.com/mholubinka1/home-monitoring/issues/600)

> Work complete — [PR #601](https://github.com/mholubinka1/home-monitoring/pull/601) ready to merge.

**Blocked by**: None

**User stories**: 1, 2, 3, 4

### What to build

When Hive's API times out listing devices, apyhiveapi swallows the timeout and `startSession` raises a bare `HiveReauthRequired` for the empty device list, so hive-app sends a false "re-authentication required" ntfy alert and, after the retry succeeds, an "authentication recovered" notice. Observe the timeout in `HiveApiSource` and raise a new `HiveApiUnavailable` (retried and recorded as a failed `job_run`, never alerted) instead of `HiveReauthRequired` when a timeout occurred in the same session. Genuine re-authentication failures are unchanged. Update ADR-0018 and the `HiveReauthRequired` docstring.

### Acceptance criteria

- [x] Given the API call times out and `startSession` then raises apyhiveapi's `HiveReauthRequired`, when the heating poll runs, then `HiveApiUnavailable` is raised and no ntfy POST is made.
- [x] Given `startSession` raises `HiveReauthRequired` with no timeout in that session, then `HiveReauthRequired` is raised and exactly one ntfy POST is made.
- [x] Given a timeout in one session, a later session without a timeout is classified independently.
- [x] A poll failing with `HiveApiUnavailable` is retried with backoff and recorded as a failed `job_run`.
- [x] ADR-0018 and the `HiveReauthRequired` docstring state that a timeout is not a re-auth trigger.

---
