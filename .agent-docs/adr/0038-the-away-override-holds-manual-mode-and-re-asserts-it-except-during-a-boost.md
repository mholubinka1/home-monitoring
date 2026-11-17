---
status: accepted
---

# The Away Override holds MANUAL mode and re-asserts it, except during a boost

While No One Is Home the thermostat must hold the Away Temperature (7 °C by default) until someone returns. We decided to switch the thermostat to MANUAL mode at the Away Temperature, rather than rewrite the Hive schedule, and to switch back to SCHEDULE on Someone Is Home. Hive keeps the schedule throughout, so nothing has to be copied and restored, and if hive-app stops while the house is empty, one tap in the Hive app recovers. While No One Is Home, every heating poll re-asserts MANUAL at the Away Temperature if the thermostat differs, so a hand change is reverted within one poll; the only exception is a running boost, which is left alone and falls back to the override when it ends.

## Considered Options

- **Rewriting the weekly schedule to the Away Temperature.** It needs the schedule write path, which `apyhiveapi` 1.0.10 does not support, plus a stored copy of the household's schedule to put back, and a crash while away would leave the household's schedule lost from Hive.
- **Hive OFF mode.** Simplest, but the temperature Hive holds is its own frost setting and cannot be configured from this app.
- **Acting only at transitions and respecting hand changes.** The household chose that the app always wins while the house is empty. A remote early warm-up is done with a boost, which is permitted.

## Consequences

- Hive writes happen only while No One Is Home (when the thermostat differs from the override) and once on Someone Is Home, so a steady state writes nothing.
- A boost started while away runs in full; its Boost Start still counts as Comfort Temperature evidence.
- Once the Comfort Schedule exists, returning home also rewrites today's and tomorrow's schedule from the latest Comfort Plan.
