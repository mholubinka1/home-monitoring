---
status: accepted
---

# The Away Override holds MANUAL mode and re-asserts it, except during a boost

While No One Is Home the thermostat must hold the Away Temperature (10 °C by default since #826; it was 7 °C, which the Hive app shows as "Off") until someone returns. We decided to switch the thermostat to MANUAL mode at the Away Temperature, rather than rewrite the Hive schedule, and to switch back to SCHEDULE on Someone Is Home. Hive keeps the schedule throughout, so nothing has to be copied and restored, and if hive-app stops while the house is empty, one tap in the Hive app recovers. While No One Is Home, every heating poll re-asserts MANUAL at the Away Temperature if the thermostat differs, so a hand change is reverted within one poll. Two exceptions: a running boost is left alone and falls back to the override when it ends, and a thermostat that is OFF is left OFF, since OFF already heats nothing and restoring SCHEDULE afterwards would turn a summer's heating back on. On Someone Is Home the restore waits for any running boost to end, so a warm-up started on the way home runs in full. The Away Override can only be switched `on` on top of the Comfort Schedule: startup refuses it otherwise.

## Considered Options

- **Rewriting the weekly schedule to the Away Temperature.** Once the Comfort Schedule ships the write path exists, so absence could be one more input to it: today written as Away Temperature all day, rebuilt from the plan on return. Rejected because the override would then depend on the newer, less proven schedule write. Each departure and return would rewrite the whole week, and a hive-app crash while away would leave today at the Away Temperature until someone fixed it by hand. MANUAL mode recovers with one tap.
- **Hive OFF mode.** Simplest, but the temperature Hive holds is its own frost setting and cannot be configured from this app.
- **Acting only at transitions and respecting hand changes.** The household chose that the app always wins while the house is empty. A remote early warm-up is done with a boost, which is permitted.

## Consequences

- Hive writes happen only while No One Is Home (when the thermostat differs from the override) and once on Someone Is Home, so a steady state writes nothing.
- A boost started while away runs in full; its Boost Start still counts as Comfort Temperature evidence.
- Returning home also rewrites today's and tomorrow's schedule from the latest Comfort Plan before SCHEDULE is restored.
- If control is switched from `on` while the override is in force, hive-app restores SCHEDULE once, so a config change never strands the house at the Away Temperature.
