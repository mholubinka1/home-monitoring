# Issues: feature-held-boost-no-upper-limit

## A Held Boost has a lower limit only

**GitHub issue**: #843

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

The Held Boost room check becomes "at least the Comfort Temperature minus `HELD_MARGIN`" with no upper limit, so a boost while the house holds 17.0 raises it whether the room reads 16.5, 17.0 or 17.2; 16.4 still does not. Docstrings, specs, glossary, ADR-0040 and the explainer say so.

### Acceptance criteria

- [x] Given 17.0 held and a boost with the room at 17.0, when the job runs, then today is raised at that boost.
- [x] Given 17.0 held and a boost with the room at 17.2, when the job runs, then today is raised at that boost.
- [x] Given 17.0 held and a boost with the room at 16.4, when the job runs, then it is not held and today is not raised.
- [x] Given the specs, glossary, ADR-0040 and explainer, when read, then they state a 0.5 °C lower limit and no upper limit.

---
