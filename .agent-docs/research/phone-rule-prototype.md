# Phone Rule prototype

The throwaway prototype behind the Phone Rule in [the presence spec](../specs/feature-presence-autonomous-heating.md), kept here so #801 has something to port from. On 2026-11-17, against 43 real Pi-hole devices, it picked out exactly the household's three phones. Every name and address below is made up or a generic product name; never add real household ones (the repo is public).

## What the production rule adds

The prototype covers the MAC gate and the hostname rules only. The spec adds:

- `ignored_hostnames` and `phone_hostnames` from config, checked first, in that order;
- a hardware address that is not six colon-separated hex octets (FTL's `ip-<address>`, blank) fails the MAC gate rather than raising. The prototype's `int(mac.split(":")[0], 16)` raises on `ip-192.0.2.5`;
- a device with several hostnames is a Phone if any of them is.

## Rule order

1. MAC gate: only a randomised MAC passes. Randomised means locally administered and unicast: bit `0x02` of the first octet set, bit `0x01` clear. Otherwise **not a Phone** (`factory-mac`).
2. The label is the hostname before the first dot, trimmed. Blank, `*` or `localhost` gives **unknown** (`no-hostname`).
3. Words: the label lowercased and split into `[a-z]+` and `\d+` runs, so `Alex-s-iPhone15.lan` gives `alex, s, iphone, 15`.
4. Exclusion groups in order; the first group with a matching word gives **not a Phone** (`not-phone:<group>`).
5. Legacy Android default, `^android-[0-9a-f]{8,16}$` (case-insensitive): **Phone**.
6. Samsung phone model code, `(?:^|-)sm-[asgfnme]\d{3}` (case-insensitive): **Phone**. `SM-T`, `X` and `P` are tablets, `SM-R` watches.
7. Any phone word: **Phone**.
8. Otherwise **unknown** (`no-rule-matched`).

## Word lists

Exclusion groups, in order:

| group | words |
| --- | --- |
| tablet | tab, tablet, ipad, pad, matepad, mediapad, kindle, fire, lenovotab, galaxytab |
| wearable | watch, band, fit, gear, wear |
| computer | macbook, imac, mac, mbp, mba, laptop, desktop, pc, chromebook, pixelbook, book, surface, thinkpad, windows, ubuntu, linux, raspberrypi |
| media | tv, chromecast, roku, firetv, appletv, shield, xbox, playstation, ps4, ps5, nintendo, switch, echo, sonos |

Phone words:

- generic: iphone, phone, smartphone, mobile, android
- brands and product lines: pixel, galaxy, samsung, xiaomi, redmi, poco, oneplus, nord, motorola, moto, razr, xperia, nokia, hmd, realme, oppo, vivo, iqoo, honor, huawei, fairphone, zenfone, rog, asus, tcl, alcatel, zte, blade, nubia, redmagic, infinix, tecno, itel, doogee, ulefone, oukitel, blackview, cat, lg, htc, meizu, lenovo, sharp, aquos, kyocera

## Test cases (62)

MAC gate (randomised?):

| MAC | randomised |
| --- | --- |
| `02:00:00:00:00:00` | yes |
| `06:00:00:00:00:00` | yes |
| `8a:00:00:00:00:00` | yes |
| `de:00:00:00:00:00` | yes |
| `00:00:00:00:00:00` | no |
| `dc:a6:32:00:00:00` | no |
| `03:00:00:00:00:00` | no (multicast) |

Phones (31): `iPhone`, `Alexs-iPhone`, `Alex-s-iPhone.lan`, `iPhone-15-Pro`, `Pixel-8a`, `Pixel-9-Pro-XL`, `Galaxy-S24-Ultra`, `Galaxy-Z-Flip6`, `Galaxy-A55-5G`, `SM-S921B`, `Redmi-Note-13-Pro`, `POCO-F6`, `Xiaomi-14T`, `OnePlus-12`, `Nord-CE4`, `moto-g85-5G`, `motorola-razr-50`, `Xperia-1-VI`, `Nokia-G42-5G`, `realme-GT-6`, `OPPO-Reno12`, `vivo-X100-Pro`, `HONOR-Magic6-Pro`, `HUAWEI-P60`, `Nothing-Phone-2a`, `Fairphone-5`, `Zenfone-11-Ultra`, `ROG-Phone-8`, `android-1a2b3c4d5e6f7a8b`, `Alexs-Phone`, `Alex-Mobile`.

Never phones (15), with the deciding group:

| hostname | group |
| --- | --- |
| `Galaxy-Tab-S9` | tablet |
| `Alex-s-Tab-S8` | tablet |
| `iPad` | tablet |
| `Pixel-Tablet` | tablet |
| `Xiaomi-Pad-6` | tablet |
| `Lenovo-Tab-P12` | tablet |
| `HUAWEI-MatePad-11` | tablet |
| `OnePlus-Pad` | tablet |
| `SM-X710` | none (unknown: a tablet model code, no rule matches) |
| `Pixel-Watch-3` | wearable |
| `Galaxy-Watch6` | wearable |
| `Alexs-MacBook-Air` | computer |
| `Galaxy-Book4` | computer |
| `DESKTOP-ABC1234` | computer |
| `Samsung-TV` | media |

Unknown (6): no hostname, `""`, `*`, `localhost`, `QZXW123ABC`, `Kitchen`.

Whole device (3):

- factory MAC `c0:00:00:00:00:01` with `Pixel-8`: not a Phone (`factory-mac`);
- randomised MAC `c2:00:00:00:00:01` with `Pixel-8.lan`: Phone;
- the same input classified 100 times gives one answer.

## Name formats the lists rest on

Since Android 11, a phone sends its Settings → About phone → Device name, by default the marketing name. Android converts it to ASCII, replaces every character outside `[A-Za-z0-9-]` with `-`, collapses repeats and caps it at 63 characters, so "Galaxy S24 Ultra" arrives as `Galaxy-S24-Ultra`. iPhones send their device name (`iPhone`, `Alex-s-iPhone`).
