def grounding(*, refs=None, support="person_statement", retention="long_term"):
    return {
        "evidence_refs": ["input"] if refs is None else refs,
        "support_kind": support,
        "retention": retention,
        "retention_reason": "本人が継続的な好みとして報告した。" if retention == "long_term" else "今回の経験に留まる。",
    }
