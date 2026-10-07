# Issues: feature-weather-data-foundations

## FND-1 · Weather observations cannot be duplicated — [#TBD]

**Blocked by**: None

**User stories**: 1, 2, 6

### What to build

Make weather writes safe to repeat. `weather_observation` gets a unique key on `(source, observed_at)` (created additively by Schema Sync) and a keyed upsert that updates the existing row when that pair already exists; the live observation job writes through it. The deploy note includes the read-only duplicate check to run before the key is created.

### Acceptance criteria

- [ ] Given an observation for an hour already stored for the same source, when it is written again, then exactly one row remains and holds the latest values.
- [ ] Given the same hour from two different sources, then both rows exist.
- [ ] Given an existing table without the key, when Schema Sync runs, then the key is created (verified against the real MariaDB fixture).
- [ ] The live observation job persists through the keyed upsert.
- [ ] The deploy note has the read-only query that proves there are no duplicate hours, and the cleanup to run first if there are.

---

## FND-2 · Solar and forecast mean are collected — [#TBD]

**Blocked by**: FND-1

**User stories**: 3, 4

### What to build

The live jobs also collect and store shortwave radiation, cloud cover and sunshine duration on each observation (nullable columns), and the forecast job stores each day's mean temperature (a new `weather_forecast` column). A variable Open-Meteo does not offer for the endpoint in use is stored as null and never fails the whole write.

### Acceptance criteria

- [ ] Given a response with the new variables, then they are stored on the observation with the correct units.
- [ ] Given a response missing one of them, then the observation is still stored with null for it.
- [ ] Given a non-finite new value, then it is rejected like the existing variables.
- [ ] Given a forecast response, then each day's mean temperature is stored alongside the maximum.
- [ ] Schema Sync adds the new columns to existing tables; the Weather Observation glossary entry is updated.

---

## FND-3 · hive-app stores the Hive working flag — [#TBD]

**Blocked by**: None

**User stories**: 5

### What to build

`heating_status` gains a nullable boolean column for the thermostat's own "heating is working" report (the library's current-operation value). hive-app records it on every poll. A missing or unexpected value is stored as null and logged once. Its meaning is unverified; a short on/off test after deploy decides whether it reflects the boiler.

### Acceptance criteria

- [ ] Given a poll where the thermostat reports working, then the row stores true; not working, false.
- [ ] Given a poll with no value or an unexpected type, then the row stores null and one log line says so.
- [ ] Schema Sync adds the column to the existing table and existing rows are unaffected.
- [ ] The existing heating persistence and retrieval tests still pass.
- [ ] A note records that the semantics are to be confirmed by an on/off test after deploy.
