---
status: partly superseded by ADR-0042 (presence now comes first from network last-seen, DNS as the fallback)
---

# Presence comes from phones' DNS activity in Pi-hole and fails safe to home

Presence-Based Heating Control needs to know whether anyone is home. The glossary had assumed Hive's own geofencing, but `apyhiveapi` does not expose it, and the Hive account has no motion or door sensors. Pi-hole is the home network's DHCP and DNS server, so it already records every device's MAC address, the hostname it sent and the time of its last DNS lookup. Phones look things up at least every 10 to 45 minutes while home, even asleep. We decided that hive-app reads Pi-hole's device list every minute, calls a device a Phone by a fixed rule (a randomised MAC address, then hostname words; plus configured extra and ignored hostnames), and treats the house as No One Is Home once no Phone has made a lookup for the Away Delay (30 minutes by default). When Pi-hole cannot be read, presence is Unknown. For its first 10 minutes control keeps the last known state, so a Pi-hole restart for an update doesn't cost two Hive writes and a burst of heat. After that control treats it as Someone Is Home, with an ntfy alert at 30 minutes.

## Considered Options

- **Hive geofencing or holiday mode.** Not exposed by the library we use; holiday mode only works when someone remembers to set it.
- **Electricity use above baseload.** Clear for whole days away, but the meter data arrives about a day late, so it cannot drive the thermostat.
- **Pinging phones or reading the ARP table.** Sleeping iPhones ignore pings and stop advertising on the network; a single missed reply would read as away.
- **Tracking phones by MAC address.** Phones may rotate their private MAC, and the household did not want to look up and maintain addresses. Hostnames, which the phone sends from its device name, survive a MAC change; the randomised-MAC test narrows candidates to personal devices first.
- **Freezing the last state indefinitely, or turning heating down, when Pi-hole is unreadable.** A failed signal would look exactly like an empty house and could leave someone cold at home. Heating an empty house on its normal schedule is the cheaper mistake. The 10-minute grace is bounded: at worst a return home during an outage waits 10 minutes longer.
- **Failing safe from the first failed read.** Pi-hole runs on its own box and restarts for updates; each blip during an absence would restore SCHEDULE and re-apply the override a minute later.

## Consequences

- Guests' phones count as someone home. A phone left at home keeps the heating on.
- A Phone that sends no hostname and is not configured is never seen. The 03:05 warning (No One Is Home overnight) is how a missed phone is noticed.
- Boosts never change presence, since a boost can be started from outside the house.
- Pi-hole's password lives only in the Pi's `config.yml`; real hostnames and MAC addresses never appear in this public repo.
