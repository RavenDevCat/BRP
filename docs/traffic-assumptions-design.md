# Traffic Assumptions Design

## Goal

Add a realistic but lightweight traffic layer to the audit workflow without turning the product into a full real-time routing system.

## Version 1

Version 1 uses profile-based travel-time multipliers:

- `Off-Peak` = `1.00`
- `AM Peak` = `1.20`
- `PM Peak` = `1.30`

These multipliers:

- affect **travel time only**
- do **not** change distance
- apply consistently to:
  - current-plan audit
  - like-for-like baseline
  - constrained-improvement baseline
  - free-optimization baseline
  - overlong-route judgment

## Version 1.1

Version 1.1 keeps the same three user-facing profiles, but allows city-aware defaults when the workbook clearly belongs to a supported city.

Current location-aware defaults:

- `China` country fallback
  - `Off-Peak` = `1.00`
  - `AM Peak` = `1.38`
  - `PM Peak` = `1.68`
- `Shanghai`
  - `Off-Peak` = `1.00`
  - `AM Peak` = `1.45`
  - `PM Peak` = `1.75`
- `Beijing`
  - `Off-Peak` = `1.00`
  - `AM Peak` = `1.42`
  - `PM Peak` = `1.72`
- `Suzhou`
  - `Off-Peak` = `1.00`
  - `AM Peak` = `1.32`
  - `PM Peak` = `1.58`
- `Xian`
  - `Off-Peak` = `1.00`
  - `AM Peak` = `1.30`
  - `PM Peak` = `1.56`
- `Seoul`
  - `Off-Peak` = `1.00`
  - `AM Peak` = `1.20`
  - `PM Peak` = `1.32`

If the exact city is not recognized, the system first falls back to the country default when available, then to the global profile defaults.

## Why this approach

This is a better fit for the current business problem than trying to model traffic lights directly:

- it is explainable to non-technical users
- it is stable enough for audit and negotiation use
- it avoids dependency on commercial real-time traffic APIs
- it creates a clean upgrade path for future refinement

## Current implementation notes

- Traffic is expressed as a named profile, not as an arbitrary free-form user number.
- The legacy routing layer applies the multiplier to road-travel duration before stop dwell is added.
- Map summaries explicitly state which traffic assumption was used.

## Planned future refinements

1. Add direction-aware school operations profiles
   - `AM To School`
   - `PM From School`

2. Add zone-aware adjustments
   - central districts vs suburban districts

3. Calibrate with historical operating data
   - compare actual trip times against OSRM baseline times
   - derive better multipliers by city, direction, and time window

4. Evaluate commercial ETA APIs only if needed
   - use them as calibration references, not as the first implementation path
