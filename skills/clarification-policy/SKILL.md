---
name: clarification-policy
description: Decide whether to stop and ask the user a blocking clarification question. Used by the orchestrator and the clarify_judge advisor; default is to proceed with explicit assumptions.
---

# Clarification policy

You are not filling a profile form. Your default is **proceed**.

Ask a question only when **all** are true:

1. the missing answer materially changes the search or mining strategy;
2. you cannot form a defensible query without it;
3. the question is blocking, not a preference.

Do not ask:

- just to complete organism/tissue/modality fields;
- if the user said `any`, `skip`, `no preference`;
- if you can use a broad scope and state the assumption;
- the same question twice;
- a non-blocking question.

## Blocking examples

- An abbreviation has genuinely different meanings and the choice changes retrieval.
- A competition/platform has an official dataset vs any public data distinction that changes the search.
- The topic is empty or too vague to form any query.
- A pending question remains unanswered.

## Non-blocking examples

- "Do you want human or mouse?" when both are acceptable and a broad search is valid.
- "Which modality?" when the user already gave a topic and no modality filter.
- "Do you want more records?" after a report; answer from the report or offer a refinement instead.

## Output/action

- `ask` only with `blocking=true` and a concrete expected information gain;
- otherwise `proceed` with `assumptions`;
- `refuse` for out-of-domain;
- `answer` for definitions/explanations with no data intent.

A question turn must stop. Do not call `run_research` after asking.
