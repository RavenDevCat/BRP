# Route Audit Solver Algorithm

This document defines the supported Route Audit optimization flow. It is the
acceptance contract for Strict Plan and Protected Plan, not a description of
retired experiments.

## Inputs And Hard Constraints

Every optimized route must satisfy all five user inputs:

1. Time window: the complete route, including configured stop dwell, must fit
   inside the supplied service window after direct-provider verification.
2. Time impact: each optimized stop may not move in the adverse direction by
   more than the user limit. Earlier pickup is adverse for To School; later
   drop-off is adverse for From School. Beneficial movement is not rejected.
3. Minimum vehicle saving: total optimized routes must be no greater than the
   current route count minus the requested saving.
4. Stop limit: every optimized route must stay within the requested service-stop
   cap.
5. Comfort: load must stay within the user comfort target as well as physical
   vehicle capacity.

No historical traffic coefficient, sampled multiplier, or market-wide timing
factor changes the solver matrix. Regional OSRM supplies unscaled candidate
travel times. AMap validates CN final routes; Kakao Navi future directions
validates KR final routes.

## Shared Preparation

1. Validate and geocode the workbook, preserve stable node IDs, and build the
   current plan from workbook route order.
2. Build the regional OSRM matrix. Stop dwell is included in solver route time.
3. Validate the current plan with the direct provider and derive each stop's
   current pickup or drop-off timing.
   Repeated address rows are matched one-to-one to distinct service nodes;
   one address key cannot silently overwrite another rider group.
4. Build adverse-only hard time-impact bounds for every solver stop. Missing
   bound coverage makes the scenario invalid rather than best effort.
5. Derive a minimum vehicle lower bound from total demand, comfort-adjusted
   available capacities, and the stop cap.

## Strict Plan

1. Set the maximum allowed vehicle count to current routes minus the requested
   minimum saving.
2. Search vehicle caps from that maximum down to the theoretical lower bound.
3. For each cap, solve with hard vehicle capacities, hard stop count, hard route
   duration, and hard adverse time-impact bounds.
4. Enrich the candidate and validate every complete route with the regional
   direct provider.
5. Rebuild pickup or drop-off times from the final provider-enriched route and
   compare every stop with its current-plan time. Solver-bound coverage alone
   is not acceptance evidence.
6. If provider timing or final stop impact fails, tighten one violated target
   and re-solve. Spare vehicles may split failed routes only within the user's
   maximum vehicle count. No hard input is relaxed.
7. Keep only candidates whose five hard constraints and direct-provider gates
   all pass. Rank accepted candidates by actual route count, direct-provider
   total duration, then modeled duration.

## Protected Plan

1. Inspect every current route for time-window, comfort, stop-cap, and physical
   capacity violations.
2. Freeze the full set of violating current routes and their assigned stops.
   Frozen routes retain their current bus slots and are the only accepted
   exceptions.
3. First try route-preserving reallocation: remove exactly the requested number
   of compliant donor routes and insert their stops into other compliant routes
   without reordering those routes' existing stops.
4. Rank insertions by adverse modeled-time increase before distance increase,
   then assign available comfort-adjusted bus slots globally.
5. Validate the complete combined plan with the direct provider and require
   every compared stop, including frozen-route riders, to stay inside the user
   time-impact limit. Frozen routes are exempt only from pre-existing route
   constraints, not from newly introduced stop-time changes.
6. If no route-preserving candidate passes, fall back to solving the unfrozen
   remainder from scratch with the same Strict Plan hard constraints.
7. Accept the candidate only when the unfrozen remainder has zero violations,
   all service nodes appear exactly once, and the combined plan meets the
   requested minimum vehicle saving.

Protected Plan may carry a failed aggregate provider status because frozen
current routes remain visible. Its adoption gate ignores only those named
frozen routes; every optimized remainder route must pass.

## Recommendation And Failure

- If only one scenario is adoption-ready, recommend it.
- If both are ready, recommend the one with fewer routes. When route counts
  tie, recommend the lower direct-provider total duration. Strict Plan wins only
  the final exact tie.
- If the theoretical minimum vehicles already exceeds the allowed maximum,
  return `provably_infeasible` with both numbers and do not call the solver.
- Otherwise, a failed search reports every attempted cap and the final hard
  constraint reasons. A failed or unavailable provider check is never treated
  as success.

## Runtime Policy

OR-Tools Guided Local Search defaults to 10 seconds per vehicle-cap candidate
through `BRP_SOLVER_TIME_LIMIT_SECONDS`. This bounds candidate improvement work;
it does not change any hard constraint or final provider acceptance rule.

## Validation Evidence

The July 2026 staging validation used direct AMap calls and stayed far below the
50,000-call development ceiling.

| Case | Coverage | Accepted result | Runtime after tuning | Provider calls |
| --- | --- | --- | ---: | ---: |
| Shanghai, 116 stops | 15-minute impact, save 2, stop cap 10, comfort 85% | Strict rejected; Protected 20 routes, 0 over-limit stops, maximum adverse impact 13.07 minutes | 275.11 s | 203 |

This replay used the prepared payload equivalent to the former
`59b54339df97` run. Earlier July rows were removed because the provider-aligned
stop gate showed that their accepted labels were false positives. Real provider
traffic can change route grouping and duration between runs; reproducibility
means the hard acceptance contract and recommendation order remain stable, not
that route hashes match.
