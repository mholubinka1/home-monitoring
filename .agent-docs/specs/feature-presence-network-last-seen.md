# Presence comes first from a phone being seen on the network

## Problem Statement

Presence decides Someone Is Home from each Phone's last DNS lookup in Pi-hole, with a 30-minute Away Delay. On a night of spotty internet a Phone sent its traffic over mobile data, Pi-hole saw no lookups, and No One Is Home held the Away Temperature for three hours while the household was home. Separately, 30 minutes is slow to notice that everyone has really left.

## Solution

A Phone counts as home when it was **Last Seen** on the home network within 2 minutes, or made a DNS lookup within 10 minutes (ADR-0042). Last Seen comes from Pi-hole's neighbour table, which it refreshes about every minute whatever route the Phone's own traffic takes. No One Is Home begins only when every Phone fails both tests. Both windows are configurable (`last_seen_minutes`, `dns_minutes`) and replace `away_delay_minutes`, which is refused at startup. Presence Unknown, its fail-safe to home and the Away Override are unchanged.

## User Stories

1. As the household, I want a phone still joined to Wi-Fi to count as home even when its data goes over mobile, so that spotty internet doesn't switch the house to Away.
2. As the household, I want a phone's DNS lookups still to count, so that a phone missing from the neighbour table but active on Wi-Fi isn't missed.
3. As the household, I want Away to begin within about 10 minutes of the last phone leaving, so that the house stops heating sooner when it's empty.
4. As the household, I want both windows in the config, so that they can be tuned without a release.
5. As a maintainer, I want the old `away_delay_minutes` refused at startup with its name, so that an old config never silently means something else.
6. As a maintainer, I want the README, config template, glossary and ADRs to describe the two signals, so that presence can be understood from the docs.

## Implementation Decisions

- **Pi-hole client (hive-app `data/pihole`).** Each address's `lastSeen` (epoch seconds; 0 means never) is parsed. `PiholeDevice` gains `last_seen: datetime | None`, the newest `lastSeen` across the device's addresses, or None when none is set.
- **Presence (hive-app `data/presence`).** `_is_active_phone` passes when the device is a Phone and either `last_seen >= now - last_seen_minutes` or `last_query >= now - dns_minutes`.
- **Config (hive-app `common/config`).** `PresenceSettings` swaps `away_delay_minutes` for `last_seen_minutes` (default 2, > 0) and `dns_minutes` (default 10, > 0). The model already refuses unknown keys, so an old `away_delay_minutes` stops startup with its name.
- **Docs.** Update the README presence paragraph and `deployments/hive-app/config.yml.template`. ADR-0042, ADR-0037's status and the glossary (No One Is Home, Last Seen) are already written.

## Testing Decisions

Use the existing seams:

- **`PresenceRetriever.refresh(now)` with a fake Pi-hole source** (`tests/test_presence_retrieval.py`):
  - A Phone seen 1 minute ago with no lookup for an hour is home.
  - A Phone seen 3 minutes ago with a lookup 5 minutes ago is home.
  - A Phone seen 3 minutes ago with a lookup 11 minutes ago is No One Is Home.
  - A non-Phone seen just now doesn't count.
  - Existing scenarios move to the new settings.
- **`PiholeApiSource.read_devices`** (`tests/test_pihole_api_source.py`): `last_seen` is the newest address `lastSeen`, and None when every address shows 0.
- **Config** (the hive-app config tests): the defaults are 2 and 10, and `away_delay_minutes` is refused.

## Out of Scope

- The internet-health gate, the evening guard and other presence signals.
- Changes to Presence Unknown, the watchdog or the Away Override.

## Further Notes

- A live check after deploy confirms that Last Seen stops for a Phone that leaves: turn the phone's Wi-Fi off for 15 minutes and watch for No One Is Home within about 10 minutes. If Last Seen stays fresh, roll back or tighten the rule.
- The Pi's hive-app config sets none of the three keys.
