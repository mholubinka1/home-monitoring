# Issues: feature-presence-network-last-seen

## Presence comes first from a phone being seen on the network

**GitHub issue**: #845

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 6

### What to build

hive-app parses each Pi-hole address's `lastSeen` into `PiholeDevice.last_seen`, the newest across the device's addresses. A Phone counts as home when last seen within `last_seen_minutes` (default 2) or when it made a DNS lookup within `dns_minutes` (default 10). These replace `away_delay_minutes`, which is refused at startup. The README and config template describe the two signals.

### Acceptance criteria

- [ ] Given a Phone last seen 1 minute ago with no DNS lookup for an hour, when presence refreshes, then Someone Is Home.
- [ ] Given a Phone last seen 3 minutes ago with a DNS lookup 5 minutes ago, when presence refreshes, then Someone Is Home.
- [ ] Given a Phone last seen 3 minutes ago with a DNS lookup 11 minutes ago, when presence refreshes, then No One Is Home.
- [ ] Given a device that is not a Phone, last seen just now, when presence refreshes, then it does not count.
- [ ] Given Pi-hole addresses with lastSeen times, when devices are read, then `last_seen` is the newest of them, and None when every address shows 0.
- [ ] Given a config with no presence windows, when settings load, then they are 2 and 10 minutes; given `away_delay_minutes`, startup is refused naming it.
- [ ] Given the README and config template, when read, then they describe Last Seen first and DNS as the fallback.

---
