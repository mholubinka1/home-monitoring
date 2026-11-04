# Issues: feature/merge-hypervolt-agile-fuel-threshold-panels-into-electricity-dashboard

## Show the effective charging limit and fuel price on the electricity dashboard

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

Start from the current live electricity dashboard export. Replace the Agile Prices panel with the hypervolt-agile version, which adds the dashed "Effective charging limit" line, and add the "Fuel price (latest)" gauge and the "Fuel price vs effective charging limit" chart in a new row at the bottom. The dashboard imports into Grafana with no id, variable or input clashes, and every existing panel is still present. A structure test on the dashboard JSON covers this.

### Acceptance criteria

- [ ] Given the merged dashboard, when panel ids are listed, then they are unique and the Agile Prices panel is still id 3
- [ ] Given the Agile Prices panel, when its queries are read, then query B reads `fuel_price_threshold` and an override names it "Effective charging limit"
- [ ] Given the dashboard, when panels 20 and 21 are read, then they are the fuel gauge and the fuel-vs-limit chart reading `fuel_price_threshold`
- [ ] Given the layout, when `gridPos` is read, then no panels overlap and none exceed 24 columns
- [ ] Given the base export, when the merge is done, then every other panel title is still present and `gauge` is in `__requires`
- [ ] The existing timezone dashboard tests still pass

---

## Document the new panels in the reference queries

**Blocked by**: #719

**User stories**: 6

### What to build

Add entries for the two fuel panels and the new Agile Prices limit query to the reference queries file, in its existing per-panel format. A doc check on the same test seam asserts the new panels are documented.

### Acceptance criteria

- [ ] Given the reference queries file, when searched, then it documents "Fuel price (latest)" and "Fuel price vs effective charging limit"
- [ ] Given the Agile Prices entry, when read, then it describes the effective charging limit query
- [ ] The doc check test passes

---
