"""主题相关性守卫：论文必须与疾病相关才挂主题（标题命中，或摘要 ≥2 次命中）。

被 `app/core/db.py` 启动回填与 `scripts/clean_memberships.py` 共用；
新增按需疾病主题若不在表中，默认放行（不做错误拦截）。
"""

from __future__ import annotations

import re

THEME_KEYWORDS: dict[str, str] = {
    "alzheimers-disease": r"alzheim|dementia|amyloid|\btau\b|\bmci\b|cognitive impairment|down syndrome",
    "als": r"\bals\b|amyotrophic|motor neuron|\bmnd\b|sod1|tdp-?43|c9orf72|frontotemporal",
    "parkinsons-disease": (r"parkinson|lewy|synuclein|lrrk2|\bgba\b|dopamin|substantia nigra|"
                           r"nigrostriatal|dyskinesia|subthalamic"),
    "frontotemporal-dementia": r"frontotemporal|\bftd\b|tdp-?43|\bmapt\b|\bgrn\b|c9orf72",
    "huntingtons-disease": r"huntington|huntingtin|\bhtt\b|polyglutamine|cag repeat",
    "prion-disease": r"prion|creutzfeldt|jakob|\bprp\b",
    "progressive-supranuclear-palsy": r"supranuclear palsy|\bpsp\b|corticobasal|tauopath",
    "dementia-with-lewy-bodies": r"lewy body|lewy",
    "multiple-system-atrophy": r"multiple system atrophy|\bmsa\b|glial cytoplasmic",
    "spinocerebellar-ataxia": r"spinocerebellar ataxia|\bsca\d?\b|ataxin|\bataxia\b",
    "breast-cancer": r"breast cancer|breast carcinoma|breast tumou?r|mammary carcinoma",
}

_PATTERNS = {k: re.compile(v, re.I) for k, v in THEME_KEYWORDS.items()}


def theme_relevant(theme: str, title: str | None, abstract: str | None) -> bool:
    """标题命中主题关键词、或摘要在 ≥2 处命中，则视为相关。未知主题放行。"""
    pat = _PATTERNS.get(theme)
    if pat is None:
        return True
    return bool(pat.search(title or "")) or len(pat.findall(abstract or "")) >= 2
