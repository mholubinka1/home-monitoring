# Issues: chore-gas-dashboard-panel-cleanup

## Remove unused panels and the sunshine series (#712)

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

In `data/grafana/gas-heating-dashboard.json` and `data/grafana/mariadb/gas-heating-queries.md`, remove panels 7 to 13, the sunshine and solar radiation series from the outdoor temperature band, and the variables only those panels used; re-lay out the six remaining panels with no gaps, the billing period progress gauge at full width.

### Acceptance criteria

- [ ] The seven panels are gone from the JSON and the query reference
- [ ] The outdoor band shows min, mean and max only (no sunshine or radiation)
- [ ] `model_summary`, `all_weeks` and `yoy_summary` are removed; every remaining variable is used
- [ ] Panels tile the 24-column grid with no gaps or overlaps
- [ ] Dashboard tests updated and passing
