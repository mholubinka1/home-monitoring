# Hive schedule write spike

Spike #807 for [the presence spec](../specs/feature-presence-autonomous-heating.md): can hive-app write the weekly schedule with pyhive-integration 1.0.10, and what does Hive accept? Run on 2026-11-17 against the live account. The repo is public, so this note holds only made-up probe slots (05:40 to 06:25 at 19 °C, 7 °C otherwise) and never the household's real schedule.

## Method

A one-off script, kept out of the repo, ran inside the hive-app container with its own code (`HiveApiSource` session, so the same auth path as the poll):

1. Resume the session from the persisted auth state and read the raw product (`GET /nodes/all`), saving the real week to a backup file on the Pi.
2. For each probe, build a full week (all seven days, the real week with one weekday replaced by the probe's slots) and `POST /nodes/heating/{id}` with `{"schedule": {...}}` through 1.0.10's authenticated `api.request`, sending the JSON body as is.
3. Read the product back from `GET /nodes/all` and compare the probe day's `start` and `target` pairs with what was sent, at 0, 2, 5 and 10 seconds, stopping at the first exact match.
4. Always restore the original week, and compare it with the backup.

The probe day was a Thursday (not the day of the run). The whole run took about a minute.

## Findings on the live account

| Probe | HTTP | Stored |
| --- | --- | --- |
| Day of three slots: 00:00 at 7 °C, 05:40 at 19 °C, 06:25 at 7 °C | 200 | exactly as sent, at the first read-back (0 s) |
| Start at 05:41 (one-minute grain) | 200 | exact |
| Start at 05:42 and end at 06:28 | 200 | exact |
| Target 19.3 °C (not on a half degree) | 200 | exact: 19.3 stored |
| First slot at 01:00, none at 00:00 | 200 | exact |
| 6, 8, 12, 16, 24 and 48 slots in one day | 200 | exact each time |

- **Accepted.** The raw POST of `{"schedule": {...}}` with all seven days works through 1.0.10's `api.request`. The body keys are lowercase weekday names; each slot is `{"start": <minutes past midnight>, "value": {"target": <°C>}}`, which is exactly what Hive returns.
- **Start-time grain.** One minute. Hive stored 05:41 and 05:42 unchanged, so the Hive app's coarser steps are an app limitation, not the API's. The Comfort Schedule's rounding "to the grain the spike proves" therefore needs no rounding beyond whole minutes (the Comfort Plan's two-minute marks are already whole minutes).
- **Slot limit.** Not reached: 48 slots in a day were accepted and stored. The real limit is above 48, far beyond anything the Comfort Schedule needs (at most three entries a day).
- **Half degrees and a first slot after 00:00.** Hive accepted and stored both. Neither is a Hive rule, so any such check is hive-app's own choice (see "Spec follow-up").
- **200 but unstored.** Not seen in any probe: every 200 was followed by an exact read-back. Nothing proves a 200 can never be ignored (the 2.0 beta's source says Hive "answers 200 and echoes the payload even when it ignores it"), so the read-back stays.
- **Read-back timing.** Every probe matched at the first read (0 s after the POST). The final restore of the whole original week read back different 2 seconds after the POST, and an identical comparison a couple of minutes later. Cause unknown: possibly propagation lag, possibly an unrelated change. Read-back verification should therefore treat a mismatch as `not_verified` (which the spec already backs off for an hour) rather than as a failed write.
- **Restore.** The original week was restored and, on the second comparison, is byte-for-byte equal to the backup.
- **Auth collision, once.** A second script resume a few seconds after the first, between hive-app's own 120-second polls, was refused: HTTP 403 on `/nodes/all`, then "device not registered, user must complete SMS 2FA". hive-app's next poll, 90 seconds later, resumed normally with no error, and a third script run succeeded. So a session started beside another one can fail transiently; it did not damage the persisted auth state. This supports the spec's "failed write retried at the next poll" and its own-session design, and argues against any schedule write that loops on failure within one poll.

## pyhive-integration 2.0.0b5: `set_schedule`

Read from the 2.0.0b5 wheel's source (`apyhiveapi/devices/schedule.py`; the latest 2.0 pre-release on PyPI on 2026-11-17).

Validation (`_normalise_schedule` and `_normalise_slot`), raised as `ValueError` before anything is sent:

- the schedule is a dict with exactly the seven lowercase weekday names (missing or unknown names are refused);
- each day is a non-empty list of slots;
- each slot is a dict whose `start` is an `int` (a `bool` is refused) from 0 to 1439;
- each slot's `value` is a dict that contains the product's key (`target` for heating and TRVs, `status` for hot water);
- slots are normalised: extra keys are dropped, and slots are sorted by `start`;
- duplicate starts in a day are refused.

After validation it sends one POST of the whole normalised week, then re-reads the product and returns `True` only if the stored schedule equals what it sent; a mismatch is logged as "Hive accepted the request … but the stored schedule does not match". `set_schedule` returns `False` when the product doesn't support schedules.

The beta checks neither "the first slot is at 00:00" nor "targets are on half degrees", and neither does the live account (see the findings above). It also has no per-day slot limit.

## Spec follow-up

The spec's Comfort Schedule section says hive-app copies the beta's rules, including "the first at 0" and "targets are on half degrees". The beta doesn't have those two. They stay in hive-app as its own checks (the Comfort Schedule always starts each day at 00:00 and sends half-degree temperatures), and the spec now says so.
