# Implementation Roadmap

## Current phase

The platform has already moved beyond pure route generation and now supports:

- current-plan import
- current-plan fleet facts
- current-plan audit
- like-for-like baseline
- free-optimization baseline
- first-pass route-to-route reallocation suggestions
- profile-based traffic assumptions for time-only adjustment, including city-aware defaults for supported cities

## Immediate next steps

1. Strengthen route removal / consolidation judgment
   - classify suggestions more clearly as:
     - local improvement
     - consolidation path
     - removal path
     - route removable now
   - improve prioritization so the most decision-relevant actions surface first

2. Continue strengthening the constrained-improvement baseline
   - let route-level action signals guide move selection
   - next, allow small coherent move packages when multiple compatible moves support the same route-level direction
   - keep the network structure mostly intact
   - provide a fairer middle benchmark between like-for-like and free optimization

3. Improve decision-oriented presentation
   - make audit conclusions easier to scan
   - express suggestions in operational language
   - better highlight savings, route count impact, and vehicle implications

4. Continue performance control
   - keep recommendation search limited and explainable
   - avoid global combinatorial redesign in the audit workflow
   - maintain acceptable runtime for realistic workbooks

## Guiding principle

The product should behave like an operations audit and optimization-advice tool:

- replay the current scheme
- benchmark it fairly
- identify high-value adjustments
- explain why those adjustments matter
