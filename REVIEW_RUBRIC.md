# Pilot review rubric (draft v0.1)

Lock this before anyone looks at model outputs. Changes after that point need a version bump and a note in the changelog at the bottom.

## What the automatic check does and doesn't tell us

The pipeline checks whether each `source_passage` appears in the paper text. A pass means the quote exists. It does not mean the quote supports the scenario, and it says nothing about scenarios the model skipped. Both of those need a person.

## Unit of review

One extracted scenario. The pipeline already groups the three runs per paper, so each row in the review sheet is one scenario cluster (the same scenario found in 1, 2, or 3 runs).

## Rating 1: support

For each scenario, read the summary, the quote, and the surrounding text in the paper. Then pick one:

| Rating | Meaning |
|---|---|
| supported | Everything in the summary (actor, AI role, harm, and the causal link) is stated in or clearly follows from the paper. |
| partially supported | The core scenario is in the paper, but the summary adds or overstates one element. Note which one. |
| unsupported | The quote is real, but the scenario as written is not something the paper says or argues. This is what we count as "invented." |
| not a scenario | The paper says it, but it fails our definition: no causal element, no AI element, or no biological harm. |

Element checklist for anything below "supported": actor, AI role, harm, causal link, extraction type (direct vs inferred mislabeled), completeness label.

For inferred scenarios: rate "supported" only if the inference follows from the paper's own argument. If it needs outside knowledge to get there, it's "unsupported."

## Rating 2: missed scenarios

After rating everything the model extracted for a paper, skim the paper for scenarios that meet the definition but aren't in the sheet. Log each one on the "missed" tab with page/section and a one-line summary.

A scenario counts as missed only if it meets the full definition (AI element, biological harm, causal link). Policy recommendations and bare risk mentions don't count, same as for the model.

When two reviewers disagree on whether something was missed, discuss it and settle it by tightening the definition, not case by case.

## Duplicates

If two rows describe the same scenario (same actor, AI role, harm, and mechanism), mark the second as `duplicate_of: <row id>`. Different actors or different mechanisms are different scenarios.

## Procedure

1. Two reviewers per pilot paper, working independently.
2. Each reviewer rates support for every row and logs missed scenarios.
3. Compare ratings. Record agreement on the support rating (simple percent agreement is fine at this size).
4. Discuss disagreements and update this rubric where the rule was unclear.

## What we report per model

- Quote check pass rate (automatic)
- Share of scenarios rated unsupported (invented rate)
- Share rated not a scenario
- Missed scenarios per paper
- Share of scenarios stable across at least 2 of 3 runs
- JSON failures and refusals

The model with the fewest unsupported scenarios and the most accurate quotes wins. Missed count breaks ties.

## Changelog

- v0.1: first draft.
