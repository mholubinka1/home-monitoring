---
status: accepted
---

# Comfort Windows are set per weekday and may run past midnight

The Comfort Window was configured as one window for weekdays and one for weekends, each ending by midnight. The household goes to bed later on the nights before a weekend day and earlier on the night before a workday, so the end of a window depends on the next day, not the day it opens. We decided the Comfort Windows are configured as seven named days (`monday` … `sunday`), each with a start and an end, where an end at or before the start is the next morning. The defaults are 07:30 to 23:00 Monday to Thursday, 07:30 to 00:30 on Friday, 08:30 to 00:30 on Saturday and 08:30 to 23:00 on Sunday.

A window's whole span, including the part after midnight, is the evidence of the day it opened on. A boost at 00:15 on Saturday morning can be Friday's Held Boost, and held time after midnight counts towards Friday's Quiet Day. So a day settles on the first hourly run after its window closes (about 01:04 on Saturday and Sunday), not after midnight. Minutes below comfort and the day's own count of Boost Starts stay on calendar days.

hive-app splits a slot that crosses midnight when it builds Hive's week, because a Hive day cannot hold an entry at 24:00. The day the slot opens gets the target at the pre-heat start and the Away Temperature at 23:59; the next day gets the target at 00:00 and the Away Temperature at the close. octopus-app keeps yesterday's plan slot stored through today, so the next day's tail entries stay in place all day.

## Considered Options

- **A start keyed by the day's kind and an end keyed by the next day's kind.** It was simpler to configure, but the household chose seven named days, the most flexible.
- **Reusing the `weekday` and `weekend` keys, with `end` meaning "the evening before".** `weekend.end` would silently apply to Fridays.
- **The tail counting for nothing, or counting for the next calendar day.** Counting for nothing would lose evidence, and counting for the next day would make a Saturday window that starts at 00:00. The household chose the day the window opened.
- **Storing the tail as a separate slot on the next day.** The schedule table would show Friday ending at midnight plus an extra 00:00–00:30 row, and the rule that keeps a passed slot's start assumed one slot a day.
- **No 23:59 close, so Hive holds the target into the next day until its 00:30 entry.** That avoids a one-minute dip, but if the next day were ever not written (an outage), Hive would hold the target all night.

## Consequences

- The old `weekday` and `weekend` keys are refused at startup.
- On Friday and Saturday nights Hive drops to the Away Temperature for one minute at 23:59. The room does not notice.
- The UK clock changes at 01:00 or 02:00, so a 00:30 close is never moved by a clock change.
