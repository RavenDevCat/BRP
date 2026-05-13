# Route Reallocation Design

## Goal

Add an audit-oriented recommendation layer that does more than compare scores.

The system should be able to say:

- which current route looks weak
- which stop or small stop cluster could move to another route
- why the move is better
- whether the move creates a realistic path to remove one route and reduce cost

This is meant to support supplier review and negotiation, not only full re-optimization.

## Product principle

The recommendation engine should prefer:

- small, explainable changes before large restructuring
- operational language before optimization jargon
- route-to-route suggestions before full network replacement
- runtime that stays practical for internal use

Target runtime for the recommendation layer:

- acceptable: within 1 to 2 minutes for realistic audit cases

## Why this approach

Two extremes are not ideal on their own:

1. Single-stop-only logic
- fast and easy to explain
- but may miss route-removal opportunities

2. Full route-group merge search
- more powerful
- but much heavier
- harder to explain
- easier to distrust in supplier conversations

The preferred design is a staged approach:

1. identify weak routes
2. test limited stop or small-cluster reallocations to nearby routes
3. only then check whether a route can be merged away

This gives a better balance of:

- interpretability
- business usefulness
- runtime

## Scope of the first version

Version 1 should focus on:

- current-plan audit only
- original stop set only
- no subway / nearby alternative scenarios required
- route-to-route recommendations based on limited reallocation

The first version should not attempt:

- full global route redesign in the recommendation layer
- arbitrary large cluster search
- unlimited combinatorial route merge exploration

## Inputs required

Current workbook support should be treated as the source of truth:

- `current_plan_assignments`
- `current_plan_fleet`

From these, the system already knows:

- current routes
- current stop allocation
- current route order
- current bus type per route
- actual seat counts for current-plan vehicles
- canonical stop list for baseline generation

The recommendation layer should also use:

- prepared original points
- OSRM travel-time / travel-distance matrix
- current-plan assessment metrics

## Output shape

Recommendations should be action-oriented.

Example structure:

- recommendation type
  - `move_stop`
  - `move_stop_cluster`
  - `merge_route_candidate`
- from route
- to route
- affected stops
- estimated operational benefit
  - average route distance change
  - average route duration change
  - load-factor change
  - route count reduction potential
- confidence / rationale
- plain-language explanation

Example user-facing wording:

- Move stops `S041` and `S042` from `Route 07` to `Route 05`.
- Estimated effect:
  - lowers average route duration by 7.8%
  - improves load balance across both routes
  - makes `Route 07` a candidate for removal if one more nearby stop is reassigned

## Staged algorithm

### Stage 1: Build current-plan audit features

For each current route, compute:

- passenger count
- seat count
- load factor
- route distance
- route duration
- stop count
- geographic centroid
- overlap score against neighboring routes

Add route flags:

- low-load
- overlong
- oversized vehicle
- geographically overlapping with nearby route

This stage identifies weak routes and candidate receiving routes.

### Stage 2: Select candidate route pairs

Do not compare every route pair blindly.

Limit candidate pairs using:

- geographic proximity between route centroids
- closest-stop distance between routes
- similar district / area
- overlap in travel direction toward depot

Example filter:

- for each weak route, keep only the 2 to 4 nearest alternative routes

This keeps runtime under control.

### Stage 3: Generate stop-level move candidates

Within a candidate route pair:

- score stops by how transferable they look
- prefer edge stops or geographically isolated stops
- avoid depot
- avoid breaking route logic too much

Candidate move types for version 1:

- single stop move
- 2-stop adjacent cluster move
- 3-stop adjacent cluster move only when clearly localized

Do not search arbitrary subsets.

## Stage 4: Fast feasibility test

For each candidate move:

1. apply the move virtually
2. rebuild only the two affected routes
3. re-optimize stop order inside those affected routes
4. recompute:
   - route duration
   - route distance
   - load factor
   - capacity feasibility

Reject any move that:

- exceeds capacity
- breaks route duration policy badly
- creates obviously worse route geometry

This is where we stay efficient:

- only recompute affected routes
- do not rerun full-network OR-Tools for every candidate

## Stage 5: Benefit scoring

Each feasible move should get a business-oriented score.

Suggested components:

- operational benefit
  - distance reduction
  - duration reduction
- fleet benefit
  - better load balance
  - improved utilization of small vs large buses
- simplification benefit
  - route elimination potential

Penalty components:

- increased imbalance
- route-duration blow-up
- too much extra complexity in receiving route

The top-ranked moves become recommendations.

## Stage 6: Route-removal check

Only after stop transfer suggestions are scored:

- examine whether one weak route can become empty or near-empty
- if yes, test a merge / route-elimination recommendation

This check should be narrow:

- only for routes already flagged as weak
- only after candidate stop transfers indicate realistic absorption by nearby routes

This avoids expensive full merge exploration.

## Runtime strategy

To keep runtime in the 1 to 2 minute range:

- use the already built current-plan assessment matrix
- avoid full recomputation of the whole network
- test only weak routes first
- test only nearby receiving routes
- test only small transfer units
- optimize only affected route pairs

Suggested cap for version 1:

- weak routes evaluated: top 5 to 8
- receiving routes per weak route: top 2 to 4
- candidate moves per route pair: bounded small set

This keeps the search controlled.

## Recommendation categories

The UI should separate recommendation types:

1. Route-level diagnosis
- route is too long
- route is underfilled
- route uses oversized vehicle

2. Route-to-route move recommendations
- move one stop
- move a small adjacent stop cluster

3. Route elimination opportunities
- if transfers are accepted, one route could be removed

This presentation is easier to trust than a single opaque optimization result.

## Relationship with existing baselines

The recommendation engine should sit beside the existing layers:

- Current Plan
- Like-for-Like Baseline
- Free Optimization Baseline

Interpretation:

- Like-for-Like Baseline answers:
  - how much can be improved without changing route structure

- Route Reallocation Recommendations answer:
  - what small structural changes are worth considering

- Free Optimization Baseline answers:
  - what the theoretical upper-bound improvement might look like

That makes the full audit story much clearer.

## First implementation milestone

Build a first version that does the following:

- identify weak current routes
- test single-stop and 2-stop-cluster moves to nearby routes
- recompute only affected route pairs
- output the top 3 to 10 actionable move recommendations

The first release does not need route elimination yet, but it should calculate:

- whether a route becomes close to removable after a move

This gives strong user value without overbuilding too early.

## Second implementation milestone

After the first version is stable:

- add route-removal candidate detection
- add richer cost language
- add management-style summary wording

Example:

- If the top two recommended transfers are accepted, Route 12 may no longer require a dedicated vehicle.

## Summary

The right design is:

- not just per-route scoring
- not full global re-optimization first
- but a staged route-to-route reallocation engine

This is the best fit for:

- supplier scheme audit
- explainable optimization advice
- practical runtime
- user trust
