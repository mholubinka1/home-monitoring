---
status: accepted
---

# Presence comes first from a phone being seen on the network, with DNS as the fallback

ADR-0037 decided presence from each Phone's last DNS lookup in Pi-hole, with a 30-minute Away Delay. On a night of spotty internet a Phone sent its traffic over mobile data, so Pi-hole saw no lookups from it and No One Is Home held the Away Temperature for three hours while the household was home. Pi-hole's device table also records when each device's address was **last seen** on the home network (its neighbour table, refreshed about every minute), whatever route the device's own traffic takes. We decided a Phone counts as home when it was last seen on the network within 2 minutes or made a DNS lookup within 10 minutes, both configurable (`last_seen_minutes`, `dns_minutes`), replacing `away_delay_minutes`. No One Is Home begins only when every Phone fails both. Presence Unknown and its fail-safe to home are unchanged.

## Considered Options

- **A longer Away Delay.** It's a config change only, but the heating keeps running longer after the household really leaves, and it doesn't address the cause.
- **An internet-health gate** (treat Phones going quiet as connection trouble while Pi-hole's upstream lookups fail). It targets spotty internet directly, but needs parsing of Pi-hole's query outcomes and leaves the DNS signal as the only presence evidence.
- **An evening guard** (a longer delay inside the Comfort Window). It narrows the harm without fixing the signal.

## Consequences

- A Phone that stays joined to Wi-Fi while its data goes over mobile counts as home. A Phone that drops off Wi-Fi entirely still looks like leaving.
- Away now begins within about 10 minutes of the last Phone leaving, not 30.
- Untested at the time of deciding: whether a Phone that has left can linger in the neighbour table, keeping last seen fresh. A live check after deploy (Wi-Fi off for 15 minutes) confirms it before the rule is trusted.
- The old `away_delay_minutes` key is refused at startup; the Pi's config didn't set it.
