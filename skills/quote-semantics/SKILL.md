---
name: quote-semantics
description: Judge whether a paper quote satisfies a user constraint. Use only for the quote_judge subagent; read a supplied window around an accession and return supported, contradicted, or unknown for organism, tissue, modality, sample count, and usage type.
---

# Quote semantics

## Scope

You receive one or more contexts. Each has an accession, a paper window, a section, and the user's constraints. Judge **only from the supplied text**. Do not use outside knowledge, the paper title alone, or a guess about what the study probably did.

## Output contract

Return JSON only:

```json
{
  "results": [
    {
      "id": "ctx-1",
      "usage_type": "generated|reused|mentioned|unknown",
      "constraints": {
        "organism": {"status": "supported|contradicted|unknown",
                     "value": "Homo sapiens or null",
                     "quote": "verbatim substring of window",
                     "inference": false,
                     "confidence": 0.0
        },
        "tissue": {"status": "unknown", "value": null, "quote": "",
                   "inference": false, "confidence": 0.0},
        "modality": {"status": "unknown", "value": null, "quote": "",
                     "inference": false, "confidence": 0.0},
        "sample_count": {"status": "unknown", "value": null, "quote": "",
                          "inference": false, "confidence": 0.0}
      }
    }
  ]
}
```

## Rules

- `supported` or `contradicted` **must** include a quote that appears verbatim in the window. No quote means `unknown`.
- `inference=true` only when the conclusion follows from context but the exact term is not stated (for example, "patients" implies human). Disclose this in the answer.
- `sample_count` must be a number stated in the quote. If the paper says "8 patients and 8 controls (80 samples)", use 80 only if "80 samples" is in the quote; otherwise report the stated count, not a sum you invented.
- Explicit species wins: human, Homo sapiens, mouse, Mus musculus, rat, etc. A different explicit species is `contradicted`.
- If the only occurrence is in a Data Availability statement with no description, use `mentioned` or `unknown`; do not call it generated.
- `reused` when the text says reanalysis, downloaded from, obtained from, previously published, or a public accession was reused.
- If the window is insufficient, return `unknown`; do not ask for more data.

## Confidence

Use 0.9+ for explicit statements, 0.6–0.8 for strong contextual inference, and 0 for unknown. Confidence never overrides the quote requirement.
