"""Shared helpers for extracting about-time hints from Japanese text."""

from __future__ import annotations

import re


# Block: 年抽出
def about_years_from_text(text: str) -> list[int]:
    years: list[int] = []
    for matched_text in re.findall(r"(19\d{2}|20\d{2}|2100)", text):
        year = int(matched_text)
        if year not in years:
            years.append(year)
    return years


# Block: ライフステージ抽出
def life_stage_from_text(text: str) -> str | None:
    for cue, life_stage in (
        ("幼少期", "childhood"),
        ("子ども時代", "childhood"),
        ("小学生", "primary_school"),
        ("中学生", "junior_high"),
        ("高校時代", "high_school"),
        ("高校生", "high_school"),
        ("大学時代", "college"),
        ("大学生", "college"),
        ("社会人", "working_adult"),
    ):
        if cue in text:
            return life_stage
    return None


# Block: ライフステージ表示名
def life_stage_label(life_stage: str) -> str:
    return {
        "childhood": "幼少期",
        "primary_school": "小学生",
        "junior_high": "中学生",
        "high_school": "高校時代",
        "college": "大学時代",
        "working_adult": "社会人",
    }.get(life_stage, life_stage)
