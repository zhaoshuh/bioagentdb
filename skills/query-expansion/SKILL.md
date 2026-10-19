---
name: query-expansion
description: Query planning for biomedical dataset discovery. Use when turning a user topic into search variants, resolving abbreviations or aliases, choosing source syntax, or deciding whether a query needs web research before searching.
---

# Query expansion

## Build the concept set

Represent the request as concepts, not one long string:

- disease or biological topic (full name);
- organism (human, mouse, rat, other);
- tissue or system (brain, CSF, liver, gut, soil, ...);
- modality (scRNA-seq, snRNA-seq, spatial, proteomics, genomics, ...);
- optional constraints (sample size, case/control, paired tissues, time point).

Use the full name first. Put abbreviations and aliases in `concepts_json` with a type and optional parent. Do not invent a synonym or a disease relationship; aliases are resolved by the web-research step and must be confirmed when ambiguous.

## Search variants

- Combine the topic with one or two high-value facets, not every word.
- Add quoted phrases for exact method or disease names.
- Use OR only for true synonyms or spelling variants.
- Do not use accession numbers as search terms; search by topic and then inspect full text.
- Do not add a modality filter unless the user asked for it; it causes false negatives.
- Prefer public full-text sources (Europe PMC/PMC, bioRxiv/medRxiv) before paywalled sources.

## Unknown topics

If the topic is a competition, platform, dataset format, or event you may not know, run web research first. Treat the result as query context, not as data evidence. If aliases conflict, ask one short clarification question before searching.

## Failure behavior

If a source times out or is skipped, keep the other sources' results and report the skipped source. Do not silently replace a failed query with a broader query and call it a match.
