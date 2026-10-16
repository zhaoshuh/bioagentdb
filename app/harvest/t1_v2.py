"""T1 v2 — family-aware, boundary-enforced grounding test with normalization tiers.

Purpose (PLAN v2.2, D4/D17): upgrade the deterministic grounding check used by the
v1.6 re-gating pass. Differences from `app.core.utils.acc_in_text`:

- **Boundary enforcement (slot semantics, v2.1 — L-0355)**: a match is rejected
  only when the maximal alnum run around it forms an **accession-shaped token**
  that differs from the match itself (``GSE12345`` inside ``GSE123450``).
  Whitespace-loss / URL-glue artifacts (``acc=GSE8397https://…``,
  ``numbersGSE325380``) are accepted — they are literal occurrences whose
  neighbours merely ran together. Unknown families keep the strict
  alnum-boundary rule (no pattern to define "accession-shaped").
- **Normalization tiers** (recorded per verdict):
  L0 exact      -- NFC + casefold
  L1 normalized -- + soft hyphen removal, line-wrap hyphen joining, width folding
                   (NFKC), zero-width stripping, whitespace collapse
  L2 variant    -- + whitespace between characters tolerated (catches split forms
                   like ``GSE 12345``), realized as a regex over the L1 haystack
                   so word merging (the global-strip bug) cannot happen; strict
                   boundary + family-checked
- **Family validation**: the matched token must fullmatch a repository pattern
  taken directly from ``app.core.vocab`` (``REPOSITORIES`` + learned patterns).
  The family list is built **independently of ``BIOAGENTDB_EXCLUDE_REPOS``** so
  evaluation verdicts cannot be altered by that env variable.
- **Version-suffix tolerance**: needles are also tried with stripped suffixes
  (``.v2`` / ``.v2.p1`` / ``/v2`` / ``-v2`` / ``;1``).
- **Zone tagging**: callers pass ``[(zone, text)]`` and the verdict records the
  zone that matched. Zone labels are the values stored by the pipeline:
  ``abstract`` / ``data_availability`` / ``supplementary``.

Pure, deterministic, no I/O beyond the vocab read.

API:
    family_of(accession) -> str
    check_text(accession, text) -> dict | None
    check(accession, zone_texts) -> dict | None
"""
from __future__ import annotations

import re
import unicodedata

from app.core import vocab

_VER_SUFFIX = re.compile(r"(\.v\d+(?:\.p\d+)?|/v\d+|-v\d+(?:\.\d+)*|;\d+)$", re.I)

_FAM: list[tuple[str, re.Pattern]] | None = None


def _fam_list() -> list[tuple[str, re.Pattern]]:
    """Compiled (family, pattern) list, built directly from vocab.

    Deliberately NOT ``vocab.iter_accession_patterns()``: that helper honours
    ``BIOAGENTDB_EXCLUDE_REPOS`` for the self-improvement counterfactual mode,
    and letting that switch change gate verdicts would make evaluation runs
    environment-dependent (G0 finding, L-0352).
    """
    global _FAM
    if _FAM is None:
        pats: list[tuple[str, re.Pattern]] = []
        for name, spec in vocab.REPOSITORIES.items():
            for p in spec.get("patterns", ()) or ():
                try:
                    pats.append((name, re.compile(p, re.I)))
                except re.error:
                    continue
        try:
            learned = vocab.learned_repositories()
        except Exception:  # noqa: BLE001 — learned table is optional
            learned = {}
        for name, spec in learned.items():
            for p in spec.get("patterns", []):
                if not p:
                    continue
                try:
                    pats.append((name, re.compile(p, re.I)))
                except re.error:
                    continue
        _FAM = pats
    return _FAM


def family_of(accession: str) -> str:
    """Repository family of an accession via the vocab pattern set ('' if unknown).

    Tries the accession as-is, then its version-stripped base (so
    ``phs000123.v1`` still resolves to the dbGaP family).
    """
    acc = (accession or "").strip()
    if not acc:
        return ""
    for cand in (acc, _VER_SUFFIX.sub("", acc)):
        if not cand:
            continue
        for name, pat in _fam_list():
            try:
                if pat.fullmatch(cand):
                    return name
            except re.error:
                continue
    return ""


def _needles(accession: str) -> list[str]:
    a = (accession or "").strip()
    if not a:
        return []
    out = [a]
    base = _VER_SUFFIX.sub("", a)
    if base and base != a:
        out.append(base)
    return out


def _l0(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def _l1(s: str) -> str:
    s = _l0(s)
    s = s.replace("\u00ad", "")                       # soft hyphen
    s = re.sub(r"[\u2010\u2011\u2012\u2013\-]\s*\n\s*", "", s)  # hyphen at line wrap
    s = unicodedata.normalize("NFKC", s)              # fullwidth -> halfwidth
    s = s.replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", s)


def _spaced_regex(needle: str) -> re.Pattern:
    """L2 matcher: adjacent needle characters may be separated by whitespace."""
    return re.compile(r"\s*".join(re.escape(c) for c in needle), re.I)


def _boundary_ok(hay: str, start: int, end: int) -> bool:
    if start > 0 and hay[start - 1].isalnum():
        return False
    if end < len(hay) and hay[end].isalnum():
        return False
    return True


def _fam_shape(tok: str) -> bool:
    """True when `tok` fullmatches ANY repository pattern (accession-shaped)."""
    for _name, pat in _fam_list():
        try:
            if pat.fullmatch(tok):
                return True
        except re.error:
            continue
    return False


def _slot_ok(hay: str, start: int, end: int, fam: str) -> bool:
    """Slot semantics v2.1 (L-0355).

    Known families: reject only when the maximal alnum run around the match is an
    accession-shaped token differing from the match (``GSE12345`` ⊂
    ``GSE123450``); whitespace-loss glue is accepted. Unknown families: strict
    alnum boundary.
    """
    if not fam:
        return _boundary_ok(hay, start, end)
    ls, le = start, end
    while ls > 0 and hay[ls - 1].isalnum():
        ls -= 1
    while le < len(hay) and hay[le].isalnum():
        le += 1
    if ls == start and le == end:
        return True
    return not _fam_shape(hay[ls:le])


def _token_ok(token: str, fam: str) -> bool:
    if not fam:
        return True
    for name, pat in _fam_list():
        if name == fam:
            try:
                if pat.fullmatch(token):
                    return True
            except re.error:
                continue
    return False


def check_text(accession: str, text: str) -> dict | None:
    """Find `accession` in `text` under slot + family rules; return verdict or None.

    Tiers: L0 exact (NFC+casefold) -> L1 normalized (soft-hyphen / line-wrap /
    width / whitespace-fold) -> L2 whitespace-inserted variant.
    """
    if not text:
        return None
    fam = family_of(accession)
    for needle in _needles(accession):
        for tier, hay in (("L0", _l0(text)), ("L1", _l1(text))):
            n = _l0(needle) if tier == "L0" else _l1(needle)
            if not n:
                continue
            pos = hay.find(n)
            while pos >= 0:
                end = pos + len(n)
                if _slot_ok(hay, pos, end, fam) and _token_ok(n, fam):
                    return {"accession": accession, "matched": needle, "tier": tier,
                            "span": [pos, end], "family": fam}
                pos = hay.find(n, pos + 1)
        n1 = _l1(needle)
        if n1 and " " not in n1:
            hay1 = _l1(text)
            for m in _spaced_regex(n1).finditer(hay1):
                token = re.sub(r"\s+", "", m.group(0))
                if _boundary_ok(hay1, m.start(), m.end()) and _token_ok(token, fam):
                    return {"accession": accession, "matched": needle, "tier": "L2",
                            "span": [m.start(), m.end()], "family": fam}
    return None


def check(accession: str, zone_texts: list[tuple[str, str | None]]) -> dict | None:
    """Zone-aware variant: `zone_texts` = [(zone, text), ...]; first hit wins."""
    for zone, text in zone_texts:
        v = check_text(accession, text or "")
        if v:
            v["zone"] = zone
            return v
    return None
