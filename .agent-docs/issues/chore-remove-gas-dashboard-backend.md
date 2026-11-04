# Issues: chore-remove-gas-dashboard-backend

> Work complete — PR ready to merge.

## Remove day verdicts, weeks and the noise model (#730)

**Blocked by**: None

**User stories**: 1, 2

### What to build

The Heating Model job stops computing day verdicts, weekly rows and the noise model. The modules, the job steps, the client methods that store and read verdicts and weeks, the two tables' models and records, the three noise columns on the model table, and their tests are removed. The refit and the 7-day forecast behave exactly as before.

### Acceptance criteria

- [x] Given a fitted model, when the job refreshes, then the forecast rows are written and no verdict or week data is written or readable
- [x] A usable fit is stored without noise values; the forecast is the same as before for the same inputs
- [x] The verdict, week and noise modules, their persistence tests and the verdict and week assertions in the job tests are gone
- [x] Pre-commit and the octopus-app tests pass

---

## Remove the bootstrapped upper threshold (#731)

**Blocked by**: #730

**User stories**: 1, 2

### What to build

The job stops bootstrapping an upper threshold. The bootstrap module, its job step, the upper threshold on the stored model and its column are removed, with their tests.

### Acceptance criteria

- [x] A refit stores a usable model without an upper threshold; the forecast is unchanged
- [x] The bootstrap module and its tests are gone
- [x] Pre-commit and the octopus-app tests pass

---

## Remove the thermostat cross-check, the model label and the heating readings (#732)

**Blocked by**: #731

**User stories**: 1, 2, 3

### What to build

The job stops cross-checking the fitted threshold against the thermostat. The thermostat module, the heating-readings reader and its type and constants, and the label, thermostat threshold, heating-day and non-heating-day columns on the model table are removed, with their tests. A database whose old `label` column has been made nullable accepts new fits.

### Acceptance criteria

- [x] A refit stores a model with no label or thermostat fields; the forecast is unchanged
- [x] With the old `label` column present and nullable, writing a fit works
- [x] The thermostat module, the readings reader and the cross-app readings test are gone
- [x] Schema Sync no longer declares the removed tables or columns
- [x] Pre-commit and the octopus-app tests pass

---

## Update the docs and document the two-phase database migration (#733)

**Blocked by**: #732

**User stories**: 3, 4

### What to build

ADR-0030 gets a dated amendment superseding the verdict, week, bootstrap, noise and thermostat parts. The heating-model explainer sections 7 to 10b and 12 and the glossary describe only the fit and the forecast. The README documents the two-phase migration for the Pi: before the new image runs, make `heating_model.label` nullable; after it runs, drop the two tables and the removed columns.

### Acceptance criteria

- [x] ADR-0030 records what is superseded and why
- [x] The explainer and glossary no longer describe verdicts, weeks, the bootstrap, noise or the thermostat label
- [x] The README gives the exact SQL for both phases and says to run phase 1 before the new image and phase 2 after
- [x] Links in the edited docs resolve

---

## Remove code left without a caller (#734)

**Blocked by**: #733

**User stories**: 5

### What to build

Find every function, type, constant, import, fixture and config value in octopus-app that no longer has a caller after the removals, and delete it.

### Acceptance criteria

- [x] No unused function, type, constant, import or test helper remains from the removed features
- [x] Pre-commit and the octopus-app tests pass

---
