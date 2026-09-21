# Edge Operating Model

## Prime directive

The fund is not the tuition payment for the research system. Capital preservation and dataset integrity outrank model novelty.

## Three books

1. **Core** — capital-preservation and understood exposures. Experimental agents cannot increase Core risk.
2. **Edge Book** — validated, bounded-risk edges with documented mechanism and failure criteria.
3. **Laboratory** — research, shadow and deliberately small-capital experiments. Failure here is expected and must not threaten the fund.

No strategy can move directly from research to scaled capital.

## Required edge record

Every candidate receives a stable `edge_id` before evaluation. The record must state:

- edge class: information, structural, behavioral, liquidity, volatility, execution, relative value, modeling, data quality, or speed;
- hypothesis;
- who/what is plausibly on the other side;
- why the inefficiency can persist;
- what causes decay;
- an observable invalidation rule;
- owner and stage.

## Graduation pipeline

`research -> walk_forward -> shadow -> small_capital -> scaled`

Graduation requires point-in-time data, out-of-sample validation, realistic costs, sufficient observations, explicit failure criteria and a clean integrity check. Promotion is one stage at a time. Stage-specific proof (`shadow_passed`, `small_capital_passed`) is required to leave that stage, not to enter it — otherwise an edge could never start shadow or small-capital observation. Demotion/retirement is always allowed and is an explicit operator action, not a promotion result.

## Historian contract

The canonical record is append-only.

- Preserve source, observation timestamp and recording timestamp.
- Preserve raw facts separately from derived features and agent interpretations.
- Corrections are new events linked by `corrects_event_id`; never overwrite the original. A correction is rejected if that ID is not already in the historian.
- Payloads must be strict JSON. Non-finite numbers and implicit stringification of dates or other objects are rejected.
- Record model/version for probabilistic classifications.
- Hash every canonical event.
- Never permit Jev-class systems, LLMs or adaptive agents to mutate canonical history.
- Backtests must use information available at the simulated point in time.

## Jev-class nervous system

A probabilistic decision engine may classify, index, associate, compare, route, prioritize and flag records. It may propose missing variables or corrections. Deterministic validation and the Historian decide whether a record is admissible. Jev outputs are themselves timestamped derived events so their historical quality can be evaluated.

## Initial edge hunting lanes

Prioritize structural and measurable hypotheses over unconstrained directional prediction:

- options microstructure and volatility risk transfer;
- cross-source information latency;
- event-conditioned distributions;
- relative-value dislocations;
- forced/mechanical flows;
- execution quality.

Each lane must generate falsifiable edge records rather than free-form narratives.

## Separation of clocks

**Fund clock:** survive and compound using only sufficiently validated exposures.

**Research clock:** experiment aggressively in the Laboratory, preserve every observation and graduate only demonstrated edges.

The Research clock has no authority to bypass the Fund clock's risk controls.
