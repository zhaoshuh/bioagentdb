---
name: report-format
description: Output rules for BioAgentDB Research and Ming. Use when summarizing a search, writing a mining proposal, reporting counts, disclosing coverage, or deciding how to present matches, partial matches, unknown records, and restricted data.
---

# Report format

## Counts are evidence, not decoration

Define every count before using it:

- catalog records: curated records found in the local catalog;
- candidates: unique papers returned by the live sources this round;
- datasets: accessions announced in papers, not downloaded datasets;
- full text read: papers with an open full text successfully parsed;
- full / partial / unknown / restricted: constraint-match outcomes.

Never say "datasets found" when you mean "accessions announced". Never add counts from different rounds.

## Research answer shape

1. One or two sentences with the topic and the strongest result.
2. Catalog records, if any, with accession and evidence.
3. Paper findings, clearly labelled *announced in paper; unverified*.
4. A coverage/limitations line: full text vs abstract-only, sources skipped, controlled records.
5. Next step: open a record, export, or inspect one accession.

Keep the final summary to 2–3 sentences when the deterministic report already contains the full details. Do not repeat the whole table.

## Mandatory disclosure

Always disclose:

- `unknown` records and why the evidence is missing;
- `restricted` records and the access route;
- reused accessions (the paper did not generate them);
- partial matches and which constraint is unknown or contradicted;
- papers without open full text.

A match is not `full` unless every requested constraint is supported by a quote or by verified catalog metadata. If a constraint is unknown, present the record as `partial` or `unknown`, never as full.

## Ming proposal

A proposal is a plan, not a result. State the topic, theme, organism/tissue/modalities, the query strategy, expected record types, cadence, and the stopping rule. Do not invent paper counts, accessions, or a promise that data exists.
