# NOACC-REVIEW — the no-accessions second-pass review (operator notes)

Some papers announce a dataset but yield **no accession** on the primary path. For those the
pipeline runs a **second-pass review** (`miner_noacc_review=1`; see `docs/CONFIG.md`): a
verbatim-guarded LLM pass over the title, abstract and Data Availability statement that
adopts a recovered identifier or link only when it appears **literally** in the paper text
together with its announcing sentence **and** carries a link — a verbatim URL or a canonical
portal link. Findings without a link are logged, never turned into dead records.

- Config row: `miner_noacc_review` (default `1`)
- Findings sink: `extract_misses`
- Every recovered form is recorded so the curated vocabulary can absorb it; recovered
  records carry their second-pass provenance in the audit trail.
- Method background: the article's section on the zero-candidate tail (second-pass review).
- Ledger history: L-0246 / L-0248.
