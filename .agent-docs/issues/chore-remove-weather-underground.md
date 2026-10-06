# Issues: chore-remove-weather-underground

## hive-app: remove Weather Underground, use Open-Meteo only — [#616](https://github.com/mholubinka1/home-monitoring/issues/616)

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

Weather Underground is out of scope: it only issues API keys to owners of a
registered personal weather station, and the household has none. Remove the
Weather Underground client, nearest-station discovery and caching, the
`weather_underground` config section and `station_id` handling, and the
`weather_location.station_id` column (never deployed). Collapse the weather
observation path to a single Open-Meteo source. Keep all location derivation
from #615. Amend ADR-0028 and update `context.md`, README and the config
template.

### Acceptance criteria

- [ ] With no weather configuration, both weather jobs register and
      `weather_observation` and `weather_forecast` fill from Open-Meteo at the
      derived Weather Location.
- [ ] An Open-Meteo observation failure raises after exactly one request, is
      retried by the existing job wrapper, and logs no "falling back" message.
- [ ] A config file that still has a `weather_underground` section loads
      without error.
- [ ] No Weather Underground code, config, tests or user-facing docs remain in
      hive-app (historical specs/issues excepted); the `station_id` column is
      gone from the `weather_location` model.
- [ ] Location derivation behaviour is unchanged (postcode, IP fallback and
      upgrade, config override, privacy).
- [ ] ADR-0028 has a dated update; `context.md`, README and
      `deployments/hive-app/config.yml.template` match the code.
