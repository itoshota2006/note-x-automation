#!/usr/bin/env python3
"""
Noon X Post Generator (No URL / No Promotion / Standalone Clinical Pearl for Junior-to-Mid-Career PTs)

Extraction priority:
  1. Live `nlm` CLI (NotebookLM) when installed & authenticated (`nlm login --check` or `nlm auth refresh`)
  2. Repository Markdown Knowledge Base (`knowledge/*.md`, including `nlm_exported_kb.md` & `noon_clinical_kb.md`)
     plus any non-overlapping NotebookLM exports under `drafts/`

Deduplication & Rotation:
  - Tracks used `source_id` and `topic` in `x_noon_history.json`.
  - Automatically excludes topics overlapping `past_articles.md` and `note-themes.txt`.
  - Automatically resets cycle history and enters Cycle N+1 when all eligible sources are exhausted.

Post Content Rules:
  - Strictly forbids URLs and note/promotional CTAs.
  - Self-contained musculoskeletal clinical pearl actionable in tomorrow's clinic (<= 280 X weighted chars / 140 JP chars).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
DONE_PREFIX_RE = re.compile(r"^\s*[\[［]済[\]］]\s*")

# Strictly forbidden patterns in noon posts (no URLs, no note/promo CTAs)
URL_PATTERNS = [
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\bwww\.\S+", re.IGNORECASE),
    re.compile(r"note\.com", re.IGNORECASE),
    re.compile(r"\b[a-z0-9-]+\.(?:com|jp|net|org|io|co)\b", re.IGNORECASE),
]

FORBIDDEN_CTA_PHRASES = [
    "note",
    "ノート",
    "詳しくは",
    "続きは",
    "記事はこちら",
    "ブログ",
    "プロフ",
    "プロフィール",
    "固定ポスト",
    "固定ツイート",
    "リンク",
    "url",
    "有料",
    "無料記事",
    "本編",
    "メルマガ",
    "公式line",
    "line登録",
    "👇",
    "▼",
    "➡",
    "こちらから",
    "チェックして",
]

# Generic PT words that must NOT trigger false-positive overlap between distinct clinical conditions
GENERIC_PT_STOPWORDS = {
    "理学療法",
    "理学療法士",
    "若手",
    "新人",
    "中堅",
    "評価",
    "基本",
    "臨床",
    "臨床推論",
    "臨床判断",
    "治療",
    "アプローチ",
    "介入",
    "リハビリ",
    "リハビリテーション",
    "リハ",
    "運動",
    "運動療法",
    "運動器",
    "運動器疾患",
    "整形",
    "整形術後",
    "脊椎",
    "脊椎疾患",
    "神経",
    "筋力",
    "筋力低下",
    "筋活動",
    "可動域",
    "関節",
    "関節可動域",
    "歩行",
    "歩行評価",
    "歩行分析",
    "動作",
    "動作観察",
    "動作分析",
    "基本動作",
    "基本動作分析",
    "姿勢",
    "姿勢制御",
    "疼痛",
    "痛み",
    "バランス",
    "バランス評価",
    "バランス能力",
    "転倒",
    "転倒リスク",
    "指導",
    "視点",
    "考え方",
    "実践",
    "実践編",
    "実践指針",
    "方法",
    "機能",
    "機能評価",
    "機能仮説",
    "患者",
    "術後",
    "保存療法",
    "改善",
    "予防",
    "効果",
    "研究",
    "文献",
    "ガイドライン",
    "エビデンス",
    "測定",
    "測定方法",
    "解釈",
    "基準",
    "限界",
    "無料",
    "有料",
    "下書き",
    "公開済み",
    "まとめ",
    "確認",
    "原因",
    "負荷",
    "負荷設定",
    "負荷調整",
    "再評価",
    "安全管理",
    "悪化徴候",
    "身体所見",
    "画像",
    "検査値",
    "読み方",
    "結果",
    "問題",
    "問題点",
    "整理",
    "具体",
    "具体的",
    "段階",
    "対象",
    "制限",
    "制限因子",
    "早期",
    "重要",
    "活用",
    "作成",
    "最新",
    "旧版",
    "構成",
    "区切り線",
    "移動",
    "体幹",
    "体幹機能",
    "骨盤",
    "骨盤動態",
    "股関節",
    "膝関節",
    "足関節",
    "肩関節",
    "肘関節",
    "手関節",
    "腰椎",
    "頚椎",
    "胸椎",
    "下肢",
    "上肢",
    "筋肉",
    "中殿筋",
    "大腿四頭筋",
    "pt",
    "adl",
    "rom",
    "mmt",
    "nrs",
    "qol",
    "mdc",
    "mcid",
    "rct",
    "cpg",
    "emg",
    "cop",
    "grf",
    "jospt",
    "apta",
    "pdf",
    "pmc",
    "mdpi",
    "html",
    "http",
    "https",
}

MUSCULOSKELETAL_BOOST_TERMS = (
    "運動器",
    "肩",
    "膝",
    "股関節",
    "足関節",
    "足底",
    "アキレス",
    "肘",
    "手関節",
    "手根管",
    "腰",
    "頚椎",
    "骨折",
    "捻挫",
    "腱板",
    "腱障害",
    "半月板",
    "acl",
    "pfps",
    "gtps",
    "fai",
    "mtss",
    "ハムストリングス",
    "オスグッド",
    "肉離れ",
    "凍結肩",
    "orthop",
    "musculoskeletal",
    "tendinopathy",
    "shoulder",
    "knee",
    "ankle",
    "hip",
    "lumbar",
    "cervical",
    "fracture",
    "ligament",
    "meniscus",
)


@dataclass
class NoonCandidate:
    source_id: str
    origin: str  # "nlm" | "nlm_spinoff" | "knowledge_base" | "drafts_nlm_export"
    topic: str
    domain: str = "運動器"
    keywords: list[str] = field(default_factory=list)
    reference: str = ""
    summary: str = ""
    post: str = ""
    notebook_id: str | None = None
    notebook_title: str | None = None
    parent_theme: str = ""


@dataclass
class NoonResult:
    candidate: NoonCandidate
    post: str
    cycle: int
    was_reset: bool
    source_origin_label: str
    generation_method: str


# ---------------------------------------------------------------------------
# Text normalization & X length / validation / auto-trimming helpers
# ---------------------------------------------------------------------------

META_OPENER_PATTERNS = [
    re.compile(r"^(?:はい、?|承知しました[。！]?\s*)"),
    re.compile(r"^(?:提供された|投入された|この)?(?:ソース|文献|資料|ノートブック|ノート)(?:に基づき|をもとに|によると|では|から)[^。\n]*[。：:]\s*"),
    re.compile(r"^(?:以下(?:に|は|が))[^。\n]*(?:投稿文|ポスト|解説文|まとめ)[^。\n]*[。：:]\s*"),
    re.compile(r"^【[^】]{1,40}】\s*"),
]

# Meta phrases that NotebookLM occasionally emits referencing itself ("このノート", "ノートブック")
NLM_META_REPLACEMENTS = [
    (re.compile(r"このノートブック(?:内)?(?:の|では|によると)(?:文献|研究|資料)"), "文献"),
    (re.compile(r"このノート(?:内)?(?:の|では|によると)(?:文献|研究|資料)"), "文献"),
    (re.compile(r"ノートブック(?:内)?(?:の|に|から)(?:文献|研究|資料)"), "文献"),
    (re.compile(r"ノート内(?:の|に|から)(?:文献|研究|資料)"), "文献"),
    (re.compile(r"このノートブック(?:内)?(?:の|では|によると)"), "文献では"),
    (re.compile(r"このノート(?:内)?(?:の|では|によると)"), "文献では"),
    (re.compile(r"ノートブック(?:内)?(?:の|に|から)"), "文献の"),
    (re.compile(r"ノート内(?:の|に|から)"), "文献の"),
]


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def x_weighted_length(text: str) -> int:
    """Compute X character weight (ASCII = 1, non-ASCII / CJK = 2; standard limit = 280)."""
    weight = 0
    for ch in text:
        weight += 1 if ord(ch) <= 0x7F else 2
    return weight


def clean_generated_post(raw: str) -> str:
    text = (raw or "").strip()
    # Remove fenced code blocks if LLM wrapped response
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Strip surrounding quotes
    if len(text) >= 2 and text[0] in ('"', "'", "「") and text[-1] in ('"', "'", "」"):
        text = text[1:-1].strip()
    # Remove NotebookLM inline citations like [1], [2, 3], [1-4]
    text = re.sub(r"\s*\[\d+(?:\s*[-,、]\s*\d+)*\]", "", text)
    # Remove markdown bold/italic/heading markers
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?m)^#+\s*", "", text)
    # Remove accidental NotebookLM meta-openers
    for _ in range(3):
        before = text
        for pat in META_OPENER_PATTERNS:
            text = pat.sub("", text).strip()
        if text == before:
            break
    # Neutralize accidental NotebookLM self-references ("このノートでは", "ノートブック内")
    for pat, repl in NLM_META_REPLACEMENTS:
        text = pat.sub(repl, text)
    # Collapse excessive blank lines
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def trim_post_to_x_limit(text: str, max_weighted_len: int = 280) -> str:
    """
    Intelligently summarize/trim an overly long X post to fit within `max_weighted_len`
    (280 weighted chars = 140 fullwidth Japanese chars) at clean sentence/clause boundaries.
    """
    cleaned = clean_generated_post(text)
    if x_weighted_length(cleaned) <= max_weighted_len:
        return cleaned

    # 1. Try keeping complete sentences (split after 。！？ or newline)
    chunks = re.findall(r"[^\n。！？]+[。！？]|\n|[^\n。！？]+$", cleaned)
    kept: list[str] = []
    for ch in chunks:
        if ch == "\n":
            candidate = "".join(kept) + "\n"
        else:
            candidate = "".join(kept) + ch
        if x_weighted_length(candidate.strip()) <= max_weighted_len:
            kept.append(ch)
        else:
            break

    sentence_trimmed = "".join(kept).strip()
    # Accept sentence-level trim if it keeps a meaningful clinical body (>= 120 weighted = ~60 fullwidth chars)
    if x_weighted_length(sentence_trimmed) >= 120 and sentence_trimmed[-1:] in ("。", "！", "？"):
        return sentence_trimmed

    # 2. Otherwise trim cleanly at clause boundary (、 or space or closing paren) within budget
    budget = max_weighted_len - 2  # reserve room for trailing "。"
    acc: list[str] = []
    w = 0
    for ch in cleaned:
        cw = 1 if ord(ch) <= 0x7F else 2
        if w + cw > budget:
            break
        acc.append(ch)
        w += cw
    prefix = "".join(acc).rstrip()

    # Prefer cutting at the last sentence or clause boundary in the second half
    min_cut = max(len(prefix) // 2, 10)
    cut_pos = -1
    for delim in ("。", "！", "？", "\n", "、", "）", ")"):
        idx = prefix.rfind(delim)
        if idx >= min_cut:
            cut_pos = max(cut_pos, idx)

    if cut_pos != -1:
        ch = prefix[cut_pos]
        if ch in ("。", "！", "？"):
            prefix = prefix[: cut_pos + 1].strip()
        elif ch in ("）", ")"):
            prefix = prefix[: cut_pos + 1].strip()
        else:
            prefix = prefix[:cut_pos].strip()

    # Strip trailing dangling particles/punctuation before appending "。"
    prefix = re.sub(r"[、,・:：/／\-\s]+$", "", prefix)
    if prefix and prefix[-1] not in ("。", "！", "？"):
        prefix = re.sub(r"(?:は|が|を|に|で|の|と|や|も|へ|から|まで|より|ため|など)$", "", prefix).rstrip("、")
        if prefix and prefix[-1] not in ("。", "！", "？"):
            prefix = prefix + "。"

    # Final safety guard against budget overflow
    while x_weighted_length(prefix) > max_weighted_len and len(prefix) > 10:
        prefix = prefix[:-2].rstrip("、") + "。"
    return prefix.strip()


def validate_noon_post(text: str, max_weighted_len: int = 280) -> tuple[bool, list[str]]:
    errors: list[str] = []
    cleaned = text.strip()
    if not cleaned:
        return False, ["投稿本文が空です"]

    wlen = x_weighted_length(cleaned)
    if wlen < 40:
        errors.append(f"本文が短すぎます（weighted={wlen}）")
    if wlen > max_weighted_len:
        errors.append(f"X文字数上限超過（weighted={wlen} > {max_weighted_len}）")

    for pat in URL_PATTERNS:
        if pat.search(cleaned):
            errors.append(f"禁止URLパターン検出: {pat.pattern}")

    lower = cleaned.lower()
    for phrase in FORBIDDEN_CTA_PHRASES:
        if phrase in ("note", "url"):
            # Match standalone ASCII token (avoid false positives like 'Spurling' or 'denote')
            if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lower):
                errors.append(f"禁止誘導語検出: {phrase}")
        elif phrase in lower:
            errors.append(f"禁止誘導語検出: {phrase}")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Deduplication against past_articles.md & note-themes.txt
# ---------------------------------------------------------------------------

def _extract_tokens(text: str) -> set[str]:
    norm = normalize_text(text)
    tokens: set[str] = set()

    # 1. Alphanumeric clinical acronyms / terms (len >= 2)
    for m in re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", norm):
        low = m.lower()
        if low not in GENERIC_PT_STOPWORDS and len(low) >= 2:
            tokens.add(low)

    # 2. Japanese Kanji / Katakana runs (len >= 2)
    for m in re.findall(r"[\u4E00-\u9FFF\u30A1-\u30FAー]{2,}", norm):
        if m in GENERIC_PT_STOPWORDS:
            continue
        # Strip common generic suffixes when checking root disease/test term
        cleaned = m
        for suffix in ("術後リハ", "術後", "評価", "臨床推論", "実践編", "運動療法", "アプローチ", "の基本", "の限界"):
            if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 2:
                sub = cleaned[: -len(suffix)]
                if sub not in GENERIC_PT_STOPWORDS:
                    tokens.add(sub)
        if cleaned not in GENERIC_PT_STOPWORDS and len(cleaned) >= 2:
            tokens.add(cleaned)

    return tokens


def _char_trigrams(text: str) -> set[str]:
    s = re.sub(r"[\s\|｜・、。！？「」『』（）()【】\[\]:：/／\-－_]+", "", normalize_text(text).lower())
    if len(s) < 3:
        return {s} if s else set()
    return {s[i : i + 3] for i in range(len(s) - 2)}


def extract_existing_note_context(
    past_articles_path: Path,
    note_themes_path: Path,
) -> dict[str, Any]:
    """Extract existing note titles, categories, and topic tokens from past_articles.md and note-themes.txt."""
    entries: list[dict[str, Any]] = []

    if past_articles_path.exists():
        content = past_articles_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            # Table row in past_articles.md:
            # | No. | `key` | status | free/paid | theme_category | title |
            if stripped.startswith("|") and "`n" in stripped:
                cols = [c.strip() for c in stripped.strip("|").split("|")]
                if len(cols) >= 6:
                    category = cols[4]
                    title = cols[5]
                    combined = f"{category} {title}"
                    entries.append(
                        {
                            "source": "past_articles.md",
                            "label": title,
                            "category": category,
                            "tokens": _extract_tokens(combined),
                            "trigrams": _char_trigrams(title),
                        }
                    )
            elif stripped.startswith("### "):
                heading = re.sub(r"^###\s*\d+\.\s*", "", stripped).strip()
                if heading:
                    entries.append(
                        {
                            "source": "past_articles.md",
                            "label": heading,
                            "category": "",
                            "tokens": _extract_tokens(heading),
                            "trigrams": _char_trigrams(heading),
                        }
                    )

    if note_themes_path.exists():
        for raw_line in note_themes_path.read_text(encoding="utf-8").splitlines():
            theme = DONE_PREFIX_RE.sub("", raw_line.strip()).strip()
            if not theme or theme.startswith("#"):
                continue
            entries.append(
                {
                    "source": "note-themes.txt",
                    "label": theme,
                    "category": theme,
                    "tokens": _extract_tokens(theme),
                    "trigrams": _char_trigrams(theme),
                }
            )

    # Deduplicate entries by label
    seen_labels: set[str] = set()
    unique_entries: list[dict[str, Any]] = []
    for e in entries:
        if e["label"] not in seen_labels:
            seen_labels.add(e["label"])
            unique_entries.append(e)

    # Specific disease / clinical assessment anchor terms derived dynamically from existing entries
    all_tokens: set[str] = set()
    for e in unique_entries:
        all_tokens.update(e["tokens"])

    return {"entries": unique_entries, "all_tokens": all_tokens}


def _check_topic_overlap_against_entries(
    topic_text: str,
    keywords: list[str],
    entries: list[dict[str, Any]],
) -> tuple[bool, str]:
    cand_text = f"{topic_text} {' '.join(keywords)}"
    cand_tokens = _extract_tokens(cand_text)
    cand_norm_lower = normalize_text(cand_text).lower()
    cand_trigrams = _char_trigrams(topic_text)

    # Strong distinct disease/scale tokens where even 1 exact match indicates topic overlap
    # (e.g. TKA, BBS, TUG, LSS, CSM, 変形性膝関節症, 腰椎圧迫骨折, 片脚立位, etc.)
    for entry in entries:
        shared = cand_tokens & entry["tokens"]
        if not shared:
            # Also check substring containment for long specific Japanese terms (>= 4 chars)
            for tok in entry["tokens"]:
                if len(tok) >= 4 and tok in cand_norm_lower:
                    shared.add(tok)

        if shared:
            # Distinguish strong specific concepts (length >= 3 or known acronym) vs weak single short words
            strong_shared = [
                t
                for t in shared
                if len(t) >= 4
                or t in {"tka", "lss", "csm", "bbs", "tug", "alb", "crp", "hb", "glim", "tis", "fist", "oss", "膝oa", "貧血", "栄養", "座位", "起立"}
            ]
            if strong_shared or len(shared) >= 2:
                return True, f"{entry['source']}『{entry['label'][:40]}』と重複（一致語: {', '.join(sorted(strong_shared or shared))}）"

        # Character trigram similarity check on topic vs existing note title
        if cand_trigrams and entry["trigrams"]:
            inter = len(cand_trigrams & entry["trigrams"])
            min_len = min(len(cand_trigrams), len(entry["trigrams"]))
            if min_len >= 6 and (inter / min_len) >= 0.45:
                return True, f"{entry['source']}『{entry['label'][:40]}』とトピック類似（重複率 {inter/min_len:.0%}）"

    return False, ""


def is_overlapping_with_existing_notes(
    candidate: NoonCandidate,
    existing_ctx: dict[str, Any],
) -> tuple[bool, str]:
    # Spin-off entries are explicitly extracted as distinct sub-angle clinical pearls
    # from a note article's primary literature notebook. Only exclude them if their
    # sub-topic title itself is practically identical to an existing note title.
    if candidate.origin == "nlm_spinoff":
        cand_tri = _char_trigrams(candidate.topic)
        for entry in existing_ctx.get("entries", []):
            if normalize_text(candidate.topic).lower() == normalize_text(entry["label"]).lower():
                return True, f"{entry['source']}『{entry['label'][:40]}』と完全一致"
            if cand_tri and entry["trigrams"]:
                inter = len(cand_tri & entry["trigrams"])
                min_len = min(len(cand_tri), len(entry["trigrams"]))
                if min_len >= 8 and (inter / min_len) >= 0.70:
                    return True, f"{entry['source']}『{entry['label'][:40]}』とトピック酷似（{inter/min_len:.0%}）"
        return False, ""

    check_text = candidate.topic
    if candidate.notebook_title and candidate.origin != "knowledge_base":
        check_text = f"{candidate.notebook_title} {candidate.topic}"
    return _check_topic_overlap_against_entries(
        check_text,
        candidate.keywords,
        existing_ctx.get("entries", []),
    )


def is_used_in_current_cycle(
    candidate: NoonCandidate,
    history_state: dict[str, Any],
) -> tuple[bool, str]:
    used_ids = set(history_state.get("used_source_ids", []))
    if candidate.source_id in used_ids:
        return True, f"今周回で使用済みsource_id: {candidate.source_id}"

    used_topics = history_state.get("used_topics", [])
    cand_norm = normalize_text(candidate.topic).lower()
    cand_trigrams = _char_trigrams(candidate.topic)
    for ut in used_topics:
        ut_norm = normalize_text(ut).lower()
        if cand_norm == ut_norm:
            return True, f"今周回で使用済みトピック: {ut}"
        ut_tri = _char_trigrams(ut)
        if cand_trigrams and ut_tri:
            inter = len(cand_trigrams & ut_tri)
            min_len = min(len(cand_trigrams), len(ut_tri))
            if min_len >= 6 and (inter / min_len) >= 0.65:
                return True, f"今周回で使用済み類似トピック: {ut}"

    return False, ""


# ---------------------------------------------------------------------------
# Knowledge Base Markdown Parser (`knowledge/*.md`)
# ---------------------------------------------------------------------------

def parse_knowledge_markdown(md_path: Path) -> list[NoonCandidate]:
    """Parse structured Markdown knowledge base entries."""
    if not md_path.exists():
        return []

    text = md_path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", text)
    candidates: list[NoonCandidate] = []

    for sec in sections[1:]:
        lines = sec.splitlines()
        if not lines:
            continue
        heading = lines[0].strip()
        meta: dict[str, str] = {}
        post_lines: list[str] = []
        in_post_block = False

        for line in lines[1:]:
            if in_post_block:
                if line.startswith("  ") or line.startswith("\t"):
                    post_lines.append(line.strip())
                    continue
                elif line.strip() == "":
                    if post_lines:
                        post_lines.append("")
                    continue
                else:
                    in_post_block = False

            m = re.match(r"^\s*-\s*([a-zA-Z0-9_]+)\s*:\s*(.*)$", line)
            if m:
                k = m.group(1).strip().lower()
                v = m.group(2).strip()
                if k == "post" and v in ("|", ">", "|-", ">-"):
                    in_post_block = True
                    post_lines = []
                else:
                    meta[k] = v

        # Extract [source_id] from heading if not explicit in bullets
        id_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", heading)
        heading_id = id_match.group(1).strip() if id_match else ""
        heading_title = id_match.group(2).strip() if id_match else heading

        source_id = meta.get("source_id") or heading_id
        topic = meta.get("topic") or heading_title
        if not source_id or not topic:
            continue

        keywords = [kw.strip() for kw in re.split(r"[,、/]", meta.get("keywords", "")) if kw.strip()]
        post_text = "\n".join(post_lines).strip() if post_lines else meta.get("post", "").strip()

        candidates.append(
            NoonCandidate(
                source_id=source_id,
                origin=meta.get("origin") or "knowledge_base",
                topic=topic,
                domain=meta.get("domain") or "運動器",
                keywords=keywords,
                reference=meta.get("reference", ""),
                summary=meta.get("summary", ""),
                post=post_text,
                notebook_id=meta.get("notebook_id") or None,
                notebook_title=meta.get("notebook_title") or None,
                parent_theme=meta.get("parent_theme", ""),
            )
        )

    return candidates


def load_repository_candidates(base_dir: Path) -> list[NoonCandidate]:
    """Load candidates from `knowledge/*.md` (nlm exports prioritized) and non-overlapping `drafts/`."""
    candidates: list[NoonCandidate] = []
    seen_ids: set[str] = set()

    kb_dir = base_dir / "knowledge"
    if kb_dir.exists():
        # Prioritize nlm_exported_kb.md before static noon_clinical_kb.md
        md_files = sorted(
            kb_dir.glob("*.md"),
            key=lambda p: (0 if "nlm" in p.name.lower() else 1, p.name),
        )
        for md_file in md_files:
            for c in parse_knowledge_markdown(md_file):
                if c.source_id not in seen_ids:
                    seen_ids.add(c.source_id)
                    candidates.append(c)

    # Also inspect NotebookLM exports saved inside drafts/*/notebooklm_sources.txt
    drafts_dir = base_dir / "drafts"
    if drafts_dir.exists():
        for meta_file in sorted(drafts_dir.glob("*/notebooklm_meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            nb_id = meta.get("notebook_id")
            nb_title = meta.get("notebook_title") or meta.get("theme") or meta_file.parent.name
            sources_file = meta_file.parent / "notebooklm_sources.txt"
            summary_file = meta_file.parent / "notebooklm_summary.md"
            summary_excerpt = ""
            if summary_file.exists():
                raw_sum = summary_file.read_text(encoding="utf-8")
                # Extract answer field if JSON embedded
                m_ans = re.search(r'"answer"\s*:\s*"((?:\\.|[^"\\])*)"', raw_sum)
                if m_ans:
                    try:
                        summary_excerpt = json.loads(f'"{m_ans.group(1)}"')[:2500]
                    except Exception:
                        summary_excerpt = raw_sum[:2500]
                else:
                    summary_excerpt = raw_sum[:2500]

            if sources_file.exists():
                try:
                    sources_data = json.loads(sources_file.read_text(encoding="utf-8"))
                except Exception:
                    sources_data = []
                if isinstance(sources_data, list):
                    for src in sources_data:
                        if not isinstance(src, dict):
                            continue
                        sid = str(src.get("id") or "").strip()
                        stitle = str(src.get("title") or "").strip()
                        if not sid or not stitle or sid in seen_ids:
                            continue
                        if src.get("status") not in (None, 2):
                            continue
                        seen_ids.add(sid)
                        candidates.append(
                            NoonCandidate(
                                source_id=sid,
                                origin="drafts_nlm_export",
                                topic=stitle,
                                domain="NotebookLM文献",
                                keywords=[],
                                reference=stitle,
                                summary=summary_excerpt,
                                post="",
                                notebook_id=nb_id,
                                notebook_title=nb_title,
                            )
                        )

    return candidates


# ---------------------------------------------------------------------------
# Live `nlm` CLI Integration
# ---------------------------------------------------------------------------

def find_nlm_binary() -> str | None:
    found = shutil.which("nlm")
    if found:
        return found
    for extra in (
        Path.home() / ".local" / "bin" / "nlm",
        Path("/opt/homebrew/bin/nlm"),
        Path("/usr/local/bin/nlm"),
    ):
        try:
            if extra.exists() and os.access(extra, os.X_OK):
                return str(extra)
        except OSError:
            continue
    return None


def check_nlm_auth(nlm_bin: str) -> bool:
    try:
        res = subprocess.run(
            [nlm_bin, "login", "--check"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if res.returncode == 0:
            return True
        refresh = subprocess.run(
            [nlm_bin, "auth", "refresh"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return refresh.returncode == 0
    except Exception:
        return False


def _parse_nlm_json_or_lines(stdout: str) -> Any:
    raw = stdout.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Try finding JSON array/object inside mixed output
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def list_nlm_notebooks(nlm_bin: str) -> list[dict[str, str]]:
    """Return list of {'id': uuid, 'title': title} from nlm CLI."""
    pinned = os.environ.get("NLM_NOTEBOOK_ID", "").strip()
    if pinned:
        return [
            {"id": nid.strip(), "title": f"Pinned Notebook ({nid.strip()[:8]})"}
            for nid in pinned.split(",")
            if nid.strip()
        ]

    for cmd in ([nlm_bin, "notebook", "list", "--json"], [nlm_bin, "notebook", "list"]):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode != 0:
                continue
            parsed = _parse_nlm_json_or_lines(res.stdout)
            if isinstance(parsed, list):
                out = []
                for item in parsed:
                    if isinstance(item, dict) and item.get("id"):
                        out.append(
                            {
                                "id": str(item["id"]),
                                "title": str(item.get("title") or item.get("name") or ""),
                            }
                        )
                if out:
                    return out
            # Fallback regex extraction from table text
            out = []
            seen: set[str] = set()
            for line in res.stdout.splitlines():
                m = UUID_RE.search(line)
                if m and m.group(0) not in seen:
                    uid = m.group(0)
                    seen.add(uid)
                    title = line.replace(uid, "").strip(" |-\t")
                    out.append({"id": uid, "title": title})
            if out:
                return out
        except Exception:
            continue
    return []


def fetch_live_nlm_candidates(
    nlm_bin: str,
    existing_ctx: dict[str, Any],
    max_notebooks: int = 12,
    *,
    allow_overlap_notebooks: bool = False,
) -> list[NoonCandidate]:
    """Query `nlm` CLI for sources across authenticated NotebookLM notebooks."""
    notebooks = list_nlm_notebooks(nlm_bin)
    candidates: list[NoonCandidate] = []
    seen_ids: set[str] = set()

    for nb in notebooks[:max_notebooks]:
        nb_id = nb["id"]
        nb_title = nb.get("title", "")
        # Skip entire notebook if its title explicitly matches an existing note article theme
        # (unless allow_overlap_notebooks=True for spin-off extraction from a newly drafted article's notebook)
        if nb_title and not allow_overlap_notebooks:
            overlaps, reason = _check_topic_overlap_against_entries(
                nb_title, [], existing_ctx.get("entries", [])
            )
            if overlaps:
                print(f"[noon][nlm] Skip notebook '{nb_title}' ({nb_id[:8]}): {reason}")
                continue

        try:
            res = subprocess.run(
                [nlm_bin, "source", "list", nb_id],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode != 0:
                continue
            parsed = _parse_nlm_json_or_lines(res.stdout)
            if isinstance(parsed, list):
                for src in parsed:
                    if not isinstance(src, dict):
                        continue
                    sid = str(src.get("id") or "").strip()
                    stitle = str(src.get("title") or "").strip()
                    if not sid or not stitle or sid in seen_ids:
                        continue
                    if src.get("status") not in (None, 2):
                        continue
                    seen_ids.add(sid)
                    candidates.append(
                        NoonCandidate(
                            source_id=sid,
                            origin="nlm",
                            topic=stitle,
                            domain="NotebookLM文献",
                            keywords=[],
                            reference=stitle,
                            summary="",
                            post="",
                            notebook_id=nb_id,
                            notebook_title=nb_title,
                        )
                    )
            time.sleep(0.5)
        except Exception as e:
            print(f"[noon][nlm] Warning listing sources for {nb_id}: {e}")
            continue

    return candidates


def _run_nlm_query(nlm_bin: str, notebook_id: str, prompt: str) -> str:
    res = subprocess.run(
        [nlm_bin, "notebook", "query", notebook_id, prompt],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if res.returncode != 0:
        raise RuntimeError(f"nlm notebook query failed (code {res.returncode}): {res.stderr[:300]}")
    parsed = _parse_nlm_json_or_lines(res.stdout)
    if isinstance(parsed, dict) and parsed.get("answer"):
        return str(parsed["answer"])
    return res.stdout


def query_nlm_for_noon_post(
    nlm_bin: str,
    candidate: NoonCandidate,
    *,
    avoid_theme: str = "",
    correction_feedback: str = "",
    previous_draft: str = "",
) -> str:
    """Use `nlm notebook query` to generate a strict standalone clinical X post from one source."""
    assert candidate.notebook_id is not None
    spinoff_instruction = ""
    if avoid_theme:
        spinoff_instruction = (
            f"【重要：切り口の差別化】\n"
            f"- 元の記事テーマ『{avoid_theme}』そのものの要約や結論の繰り返しは禁止。\n"
            "- ソースの中に含まれる**別の臨床ポイント・評価のコツ・測定条件の注意点・鑑別の視点・負荷設定のワンポイント**を1つだけ独立して切り出すこと。\n\n"
        )

    retry_instruction = ""
    if correction_feedback:
        retry_instruction = (
            f"【前回のNG理由（必ず修正すること）】\n"
            f"- 理由: {correction_feedback}\n"
            f"- 前回出力:\n{previous_draft}\n"
            "- 上記を必ず全角110〜125文字以内、『note』『ノート』完全不使用で簡潔に書き直してください。\n\n"
        )

    prompt = (
        f"以下の特定ソース（文献名: {candidate.topic} / ID: {candidate.source_id}）の記載内容のみを根拠に、"
        "若手〜中堅の理学療法士（運動器領域メイン）が『明日の臨床でそのまま使える』と感じる"
        "評価・治療のワンポイントまたは文献知見を、X（旧Twitter）単体で完結する投稿文として1つだけ作成してください。\n\n"
        f"{spinoff_instruction}"
        f"{retry_instruction}"
        "【厳守ルール（違反不可）】\n"
        "1. 文字数は**必ず全角110〜130文字以内**（最大でも全角130字・X換算260ウェイト以内）に収めること。130字を絶対に超えないこと。\n"
        "2. 『note』『ノート』『ノートブック』『記事』『リンク』『URL』『詳しくは』『続きは』『プロフ』『👇』という単語・表現は理由を問わず絶対に使わないこと。\n"
        "3. 『このノートでは』『提供されたソースによると』『以下は〜』などの前置きは禁止。いきなり臨床の問いかけや知見から書き始めること。\n"
        "4. ソースに記載された事実・数値・基準のみを使い、捏造しないこと。\n"
        "5. 引用番号（[1]など）やMarkdown記号（**、#など）、見出しは出力せず、投稿本文のみを出力すること。\n"
        "6. 丁寧な『です・ます』調で、2〜3行に改行して読みやすく完結させること。"
    )
    raw = _run_nlm_query(nlm_bin, candidate.notebook_id, prompt)
    return clean_generated_post(raw)


def query_nlm_for_spinoff_pair(
    nlm_bin: str,
    candidate: NoonCandidate,
    *,
    avoid_theme: str,
) -> tuple[str, str]:
    """
    Ask NotebookLM for a distinct sub-angle clinical pearl (both concise sub-topic title and X post)
    so it doesn't repeat the parent note article's main theme.
    """
    assert candidate.notebook_id is not None
    prompt = (
        f"以下の特定ソース（文献名: {candidate.topic} / ID: {candidate.source_id}）の記載内容のみを根拠に、"
        f"元の解説テーマ『{avoid_theme}』そのものではなく、文献内に含まれる**別の臨床ポイント・評価のコツ・測定時の注意点**を1つ切り出してください。\n\n"
        "必ず以下の2行フォーマットのみで出力してください（前置き・解説・Markdown記号・引用番号[1]は一切不要）：\n"
        "TOPIC: <元のテーマと重複しない具体的な臨床ポイント名（20〜35文字）>\n"
        "POST: <X単体で完結する投稿本文（改行可、必ず全角110〜130文字以内）>\n\n"
        "【絶対禁止ルール】\n"
        "- POSTは必ず全角130文字以内（X換算260ウェイト以内）に厳守すること。\n"
        "- 『note』『ノート』『ノートブック』『記事』『リンク』『URL』『詳しくは』『続きは』『プロフ』『👇』は絶対に使わないこと。\n"
        "- 『提供されたソースでは』などの前置き禁止。丁寧な『です・ます』調で臨床に直結する内容にすること。"
    )
    raw = _run_nlm_query(nlm_bin, candidate.notebook_id, prompt)
    sub_topic = ""
    post_lines: list[str] = []
    in_post = False
    for line in raw.splitlines():
        s = line.strip()
        if not in_post and s.upper().startswith("TOPIC:"):
            sub_topic = clean_generated_post(s.split(":", 1)[1]).replace("\n", " ")
        elif s.upper().startswith("POST:"):
            in_post = True
            rest = s.split(":", 1)[1].strip()
            if rest:
                post_lines.append(rest)
        elif in_post:
            if s:
                post_lines.append(s)

    post_raw = "\n".join(post_lines).strip() if post_lines else raw
    post_clean = clean_generated_post(post_raw)
    if not sub_topic:
        first_line = post_clean.splitlines()[0] if post_clean else candidate.topic
        sub_topic = f"【臨床ポイント】{first_line[:32]}"
    return sub_topic, post_clean


# ---------------------------------------------------------------------------
# Optional LLM API Helper (Gemini / OpenAI, zero external dependencies)
# ---------------------------------------------------------------------------

def generate_noon_post_via_llm(
    candidate: NoonCandidate,
    *,
    previous_post_preview: str = "",
    validation_feedback: str = "",
    avoid_theme: str = "",
) -> str | None:
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not gemini_key and not openai_key:
        return None

    system_prompt = (
        "あなたは運動器領域に精通した中堅〜ベテラン理学療法士です。"
        "提供された一次文献・知見メモのみを根拠に、若手〜中堅の理学療法士が"
        "『明日の臨床でそのまま使える』と感じる評価・治療のワンポイントを、"
        "X（旧Twitter）単体で完結する投稿文として作成してください。\n"
        "【絶対禁止事項】\n"
        "- 『note』『ノート』『ノートブック』『記事』『リンク』『URL』『詳しくは』『続きは』『プロフ』『👇』という語句・誘導表現は理由を問わず絶対に使用しないこと。\n"
        "- ソースに記載のない数値・論文名・基準の捏造は禁止。\n"
        "- Markdown記号（**、#等）、引用番号（[1]）、前置き解説は禁止。投稿本文のみ出力すること。\n"
        "【文体・文字数】\n"
        "- **必ず全角110〜130文字以内**（半角1・全角2換算で260以下、最大280厳守）。\n"
        "- 丁寧な『です・ます』調、2〜3段落（改行あり）で読みやすく完結させる。"
    )

    extra_note = ""
    if avoid_theme:
        extra_note += (
            f"\n※元のテーマ『{avoid_theme}』そのものの要約は避け、文献内の別の臨床ポイント・評価のコツを切り出すこと。\n"
        )
    if previous_post_preview:
        extra_note += f"\n※前回投稿（表現・切り口を変えて新しく作成すること）:\n{previous_post_preview}\n"
    if validation_feedback:
        extra_note += f"\n※前回の修正点（必ず解消すること）: {validation_feedback}\n"

    user_prompt = (
        f"トピック: {candidate.topic}\n"
        f"領域: {candidate.domain}\n"
        f"参考根拠: {candidate.reference or '一次文献ソース'}\n"
        f"知見メモ:\n{candidate.summary or candidate.post}\n"
        f"{extra_note}\n"
        "上記のみを根拠に、全角130文字以内・『note』『ノート』不使用でX投稿本文のみを出力してください。"
    )

    try:
        if gemini_key:
            model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{urllib.parse.quote(model, safe='')}:generateContent"
                f"?key={urllib.parse.quote(gemini_key, safe='')}"
            )
            payload = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 512},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            return clean_generated_post(text) if text.strip() else None

        if openai_key:
            model = os.environ.get("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"
            url = "https://api.openai.com/v1/chat/completions"
            payload = {
                "model": model,
                "temperature": 0.4,
                "max_tokens": 512,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {openai_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
            return clean_generated_post(text) if text.strip() else None
    except Exception as e:
        print(f"[noon][llm] API generation failed, falling back: {e}")
        return None

    return None


def ensure_valid_noon_post(
    raw_text: str,
    candidate: NoonCandidate,
    *,
    nlm_bin: str | None = None,
    avoid_theme: str = "",
    max_weighted_len: int = 280,
) -> tuple[str, bool, list[str]]:
    """
    Validate a generated noon post, automatically retrying via LLM / nlm on forbidden words
    or length overflow, and applying clean sentence-boundary trimming as final fallback
    when only length exceeds 280 weighted chars.
    """
    current = clean_generated_post(raw_text)
    ok, errs = validate_noon_post(current, max_weighted_len=max_weighted_len)
    if ok:
        return current, True, []

    # Step 1: If forbidden words or length overflow, attempt concise rewrite via LLM API first
    llm_refined = generate_noon_post_via_llm(
        NoonCandidate(
            source_id=candidate.source_id,
            origin=candidate.origin,
            topic=candidate.topic,
            domain=candidate.domain,
            reference=candidate.reference,
            summary=current,
        ),
        validation_feedback="; ".join(errs),
        avoid_theme=avoid_theme,
    )
    if llm_refined:
        current = clean_generated_post(llm_refined)
        ok, errs = validate_noon_post(current, max_weighted_len=max_weighted_len)
        if ok:
            return current, True, []

    # Step 2: If nlm CLI is available and candidate has a notebook_id, retry nlm query once with explicit error feedback
    if nlm_bin and candidate.notebook_id:
        try:
            retried = query_nlm_for_noon_post(
                nlm_bin,
                candidate,
                avoid_theme=avoid_theme,
                correction_feedback="; ".join(errs),
                previous_draft=current,
            )
            current = clean_generated_post(retried)
            ok, errs = validate_noon_post(current, max_weighted_len=max_weighted_len)
            if ok:
                return current, True, []
        except Exception as e:
            print(f"[noon][nlm] Retry rewrite failed: {e}")

    # Step 3: Automatic summarization/trimming if length overflow remains
    if x_weighted_length(current) > max_weighted_len:
        trimmed = trim_post_to_x_limit(current, max_weighted_len=max_weighted_len)
        ok_trim, errs_trim = validate_noon_post(trimmed, max_weighted_len=max_weighted_len)
        if ok_trim:
            print(
                f"[noon][trim] Auto-trimmed post from {x_weighted_length(current)} "
                f"-> {x_weighted_length(trimmed)} weighted chars."
            )
            return trimmed, True, []
        current, errs = trimmed, errs_trim

    return current, False, errs


def append_kb_entry_markdown(
    out_path: Path,
    *,
    source_id: str,
    topic: str,
    post: str,
    origin: str = "nlm",
    domain: str = "運動器・NotebookLM文献",
    reference: str = "",
    summary: str = "",
    notebook_id: str = "",
    notebook_title: str = "",
    parent_theme: str = "",
    keywords: list[str] | None = None,
) -> bool:
    """Append a structured entry to knowledge/nlm_exported_kb.md (skips if source_id already exists)."""
    existing_ids = {c.source_id for c in parse_knowledge_markdown(out_path)}
    if source_id in existing_ids:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        header = (
            "# NotebookLM 抽出ナレッジベース（昼用X投稿・リンクなし）\n\n"
            f"`scripts/export_nlm_knowledge.py` / 記事作成フローにより自動書き出し（初回作成: {now_jst}）。\n"
            "GitHub Actions 実行時に自動で候補として読み込まれます。\n\n"
            "---\n"
        )
        out_path.write_text(header, encoding="utf-8")

    indented_post = "\n".join(f"  {line}" for line in post.splitlines())
    lines = [
        "",
        f"## [{source_id}] {topic}",
        f"- source_id: {source_id}",
        f"- origin: {origin}",
    ]
    if notebook_id:
        lines.append(f"- notebook_id: {notebook_id}")
    if notebook_title:
        lines.append(f"- notebook_title: {notebook_title}")
    if parent_theme:
        lines.append(f"- parent_theme: {parent_theme}")
    lines.append(f"- domain: {domain}")
    lines.append(f"- topic: {topic}")
    if keywords:
        lines.append(f"- keywords: {', '.join(keywords)}")
    lines.append(f"- reference: {reference or topic}")
    lines.append(f"- summary: {summary or f'一次文献『{reference or topic}』から抽出した臨床ポイント。'}")
    lines.append("- post: |")
    lines.append(indented_post)
    lines.append("")

    with out_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


# ---------------------------------------------------------------------------
# History & Candidate Selection Engine
# ---------------------------------------------------------------------------

def load_noon_history(history_path: Path) -> dict[str, Any]:
    if history_path.exists():
        data = json.loads(history_path.read_text(encoding="utf-8"))
        data.setdefault("cycle", 1)
        data.setdefault("used_source_ids", [])
        data.setdefault("used_topics", [])
        data.setdefault("history", [])
        data.setdefault("reset_log", [])
        return data
    return {
        "description": "翌日昼用X投稿（リンク・宣伝なし／臨床知見）の使用済みソースID・トピック履歴台帳",
        "timezone": "Asia/Tokyo",
        "cycle": 1,
        "used_source_ids": [],
        "used_topics": [],
        "history": [],
        "reset_log": [],
    }


def save_noon_history(history_path: Path, state: dict[str, Any]) -> None:
    state["last_updated_jst"] = datetime.now(JST).isoformat(timespec="seconds")
    history_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _musculoskeletal_score(candidate: NoonCandidate) -> int:
    text = f"{candidate.domain} {candidate.topic} {' '.join(candidate.keywords)}".lower()
    score = 0
    for term in MUSCULOSKELETAL_BOOST_TERMS:
        if term in text:
            score += 1
    return score


def select_candidate_with_rotation(
    candidates: list[NoonCandidate],
    existing_ctx: dict[str, Any],
    history_state: dict[str, Any],
) -> tuple[NoonCandidate, bool]:
    """
    Filter out candidates overlapping past_articles.md / note-themes.txt,
    then pick the next unused candidate for the current cycle.
    If all eligible candidates are exhausted, automatically reset cycle history and enter Cycle N+1.
    """
    eligible: list[NoonCandidate] = []
    excluded_count = 0
    for cand in candidates:
        overlaps, reason = is_overlapping_with_existing_notes(cand, existing_ctx)
        if overlaps:
            excluded_count += 1
            if excluded_count <= 5:
                print(f"[noon][dedup] Excluded '{cand.source_id}' ({cand.topic[:36]}): {reason}")
            continue
        eligible.append(cand)
    if excluded_count > 5:
        print(f"[noon][dedup] ...and {excluded_count - 5} more overlapping candidates excluded.")

    if not eligible:
        raise RuntimeError(
            "昼用X投稿の候補が0件です（全候補が past_articles.md / note-themes.txt と重複、"
            "またはナレッジベースが空です）。knowledge/noon_clinical_kb.md を確認してください。"
        )

    unused = [c for c in eligible if not is_used_in_current_cycle(c, history_state)[0]]
    was_reset = False

    if not unused:
        # All eligible items have been used -> automatic reset for next cycle!
        prev_cycle = int(history_state.get("cycle", 1))
        prev_used_count = len(history_state.get("used_source_ids", []))
        last_source_id = (
            history_state["history"][-1].get("source_id")
            if history_state.get("history")
            else None
        )
        history_state.setdefault("reset_log", []).append(
            {
                "reset_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
                "completed_cycle": prev_cycle,
                "completed_count": prev_used_count,
                "next_cycle": prev_cycle + 1,
            }
        )
        history_state["cycle"] = prev_cycle + 1
        history_state["used_source_ids"] = []
        history_state["used_topics"] = []
        was_reset = True
        print(
            f"[noon][rotation] All {prev_used_count} eligible items exhausted. "
            f"Resetting history -> entering Cycle {history_state['cycle']}."
        )
        # Avoid repeating the immediately preceding day's item at cycle boundary if possible
        unused = [c for c in eligible if c.source_id != last_source_id] or eligible

    # Prioritize live nlm sources, then musculoskeletal relevance, while preserving stable catalog order
    unused_sorted = sorted(
        enumerate(unused),
        key=lambda pair: (
            0 if pair[1].origin == "nlm" else 1,
            -_musculoskeletal_score(pair[1]) if pair[1].origin == "nlm" else 0,
            pair[0],
        ),
    )
    return unused_sorted[0][1], was_reset


def _find_previous_preview(history_state: dict[str, Any], source_id: str) -> str:
    for h in reversed(history_state.get("history", [])):
        if h.get("source_id") == source_id and h.get("post_preview"):
            return str(h["post_preview"])
    return ""


def generate_next_noon_post(
    base_dir: Path,
    *,
    history_path: Path | None = None,
    skip_nlm: bool = False,
) -> tuple[NoonResult, dict[str, Any]]:
    """
    Main entry point to select a non-overlapping clinical source and generate the noon X post.
    Returns (NoonResult, updated_history_state) — caller decides whether to persist (supports --dry-run).
    """
    hist_file = history_path or (base_dir / "x_noon_history.json")
    past_articles_path = base_dir / "past_articles.md"
    note_themes_path = base_dir / "note-themes.txt"

    history_state = load_noon_history(hist_file)
    existing_ctx = extract_existing_note_context(past_articles_path, note_themes_path)

    # Tier 1: Try live `nlm` CLI first
    nlm_bin: str | None = None
    nlm_candidates: list[NoonCandidate] = []
    if not skip_nlm and os.environ.get("X_NOON_SKIP_NLM", "").strip() != "1":
        nlm_bin = find_nlm_binary()
        if nlm_bin:
            if check_nlm_auth(nlm_bin):
                print(f"[noon][nlm] Authenticated nlm CLI detected: {nlm_bin}")
                nlm_candidates = fetch_live_nlm_candidates(nlm_bin, existing_ctx)
                print(f"[noon][nlm] Found {len(nlm_candidates)} live NotebookLM source candidates.")
            else:
                print("[noon][nlm] nlm CLI found, but authentication failed. Falling back to repository KB.")
        else:
            print("[noon][nlm] nlm CLI not available in this environment. Using repository KB.")

    # Tier 2: Repository Knowledge Base (`knowledge/*.md` + non-overlapping `drafts/`)
    repo_candidates = load_repository_candidates(base_dir)
    all_candidates = nlm_candidates + [
        c for c in repo_candidates if c.source_id not in {nc.source_id for nc in nlm_candidates}
    ]

    picked, was_reset = select_candidate_with_rotation(all_candidates, existing_ctx, history_state)
    cycle = int(history_state.get("cycle", 1))

    post_text = ""
    generation_method = ""

    # Generate post text depending on origin
    if picked.origin == "nlm" and nlm_bin:
        try:
            raw_post = query_nlm_for_noon_post(nlm_bin, picked)
            final_post, ok, errs = ensure_valid_noon_post(raw_post, picked, nlm_bin=nlm_bin)
            if ok:
                post_text = final_post
                generation_method = "nlm_cli_query"
            else:
                print(f"[noon][nlm] Query output remained invalid after retry/trim ({errs}); falling back to KB.")
        except Exception as e:
            print(f"[noon][nlm] Live query failed ({e}); falling back to repository KB.")

        # If live nlm generation failed, re-select from repository KB cleanly
        if not post_text:
            picked, was_reset = select_candidate_with_rotation(repo_candidates, existing_ctx, history_state)
            cycle = int(history_state.get("cycle", 1))

    if not post_text:
        rewrite_flag = os.environ.get("X_NOON_REWRITE_WITH_LLM", "").strip() == "1"
        needs_llm = (not picked.post) or (cycle >= 2) or rewrite_flag
        if needs_llm:
            prev_preview = _find_previous_preview(history_state, picked.source_id)
            llm_post = generate_noon_post_via_llm(
                picked,
                previous_post_preview=prev_preview,
                avoid_theme=picked.parent_theme,
            )
            if llm_post:
                final_post, ok, errs = ensure_valid_noon_post(
                    llm_post,
                    picked,
                    avoid_theme=picked.parent_theme,
                )
                if ok:
                    post_text = final_post
                    generation_method = "kb_plus_llm_api"
                else:
                    print(f"[noon][llm] Generated post invalid even after trim: {errs}")

    if not post_text and picked.post:
        final_post, ok, errs = ensure_valid_noon_post(picked.post, picked)
        if not ok:
            raise RuntimeError(f"KB entry '{picked.source_id}' failed validation: {errs}")
        post_text = final_post
        generation_method = "knowledge_base_curated"

    if not post_text:
        raise RuntimeError(
            f"候補 '{picked.source_id}' ({picked.topic}) の投稿文を生成できませんでした。"
            "knowledge/*.md に post を記載するか GEMINI_API_KEY を設定してください。"
        )

    origin_labels = {
        "nlm": "NotebookLM（nlm CLIリアルタイム抽出）",
        "nlm_spinoff": "NotebookLM 記事関連文献からの別角度臨床ポイント（knowledge/nlm_exported_kb.md）",
        "knowledge_base": "リポジトリ内ナレッジベース（knowledge/）",
        "drafts_nlm_export": "リポジトリ内NotebookLM書き出し（drafts/）",
    }
    result = NoonResult(
        candidate=picked,
        post=post_text,
        cycle=cycle,
        was_reset=was_reset,
        source_origin_label=origin_labels.get(picked.origin, picked.origin),
        generation_method=generation_method,
    )
    return result, history_state


def record_noon_history(
    history_state: dict[str, Any],
    result: NoonResult,
    *,
    date_str: str,
    post_file: str,
    email_sent: bool,
    delivery_mode: str,
) -> dict[str, Any]:
    cand = result.candidate
    if cand.source_id not in history_state.setdefault("used_source_ids", []):
        history_state["used_source_ids"].append(cand.source_id)
    if cand.topic not in history_state.setdefault("used_topics", []):
        history_state["used_topics"].append(cand.topic)

    history_state.setdefault("history", []).append(
        {
            "date": date_str,
            "slot": "next_noon",
            "cycle": result.cycle,
            "source_id": cand.source_id,
            "source_origin": cand.origin,
            "generation_method": result.generation_method,
            "notebook_id": cand.notebook_id,
            "topic": cand.topic,
            "domain": cand.domain,
            "reference": cand.reference,
            "post_preview": result.post,
            "post_file": post_file,
            "email_sent": email_sent,
            "delivery_mode": delivery_mode,
        }
    )
    return history_state
