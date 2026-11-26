#!/usr/bin/env python3
"""
X (Twitter) 16:9 (1200x675px) Clinical Diagram Card Renderer
3-Layer Standalone Clinical Diagram Architecture:
  1. Conclusion Title (<= 28 chars, declarative)
  2. Evidence / Comparison / Steps (max 3 elements, each: focus <= 20 chars & interpretation <= 20 chars)
  3. Clinical Takeaway for Tomorrow (1 sentence, <= 30 chars)

Shared by:
- run_daily_x.py (Night + Next Morning note-intro cards & Next Noon clinical pearl card)
- noon_post_generator.py (Noon standalone clinical pearl card + layout history tracking)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants: 5 Layouts & 3 Theme Color Palettes (16:9 / 1200x675px)
# ---------------------------------------------------------------------------

CARD_WIDTH = 1200
CARD_HEIGHT = 675

MAX_TITLE_CHARS = 28
MAX_FOCUS_CHARS = 20
MAX_INTERP_CHARS = 20
MAX_TAKEAWAY_CHARS = 30
MAX_ELEMENTS = 3

VALID_LAYOUT_IDS: tuple[str, ...] = (
    "layout_matrix",    # 対比型（× vs ◎）
    "layout_flow",      # 3ステップ・フロー型（①→②→③）
    "layout_grid",      # 3段・構造整理型（見るべき点 → 臨床解釈）
    "layout_hero",      # 結論＋3根拠カード型
    "layout_timeline",  # タイムライン・段階進行型（①→②→③）
)

# Internal labels for logs/email metadata ONLY — NEVER rendered inside the PNG image.
LAYOUT_LABELS: dict[str, str] = {
    "layout_matrix": "対比型（× vs ◎）",
    "layout_flow": "3ステップ・フロー型",
    "layout_grid": "3段・構造整理型",
    "layout_hero": "結論＋3根拠カード型",
    "layout_timeline": "タイムライン・段階進行型",
}

PALETTES: dict[str, dict[str, str]] = {
    "musculoskeletal": {
        "id": "musculoskeletal",
        "label": "運動器・バイオメカニクス（ネイビー×オレンジ）",
        "bg": "#F4F6FA",
        "surface": "#FFFFFF",
        "surface_alt": "#EEF2F7",
        "primary": "#102A4C",
        "primary_mid": "#1D4473",
        "primary_soft": "#E6EEF8",
        "accent": "#EA580C",
        "accent_dark": "#9A3412",
        "accent_soft": "#FFF3EB",
        "accent_text_on_dark": "#FDBA74",
        "neg_bg": "#FEF2F2",
        "neg_border": "#EF4444",
        "neg_text": "#991B1B",
        "pos_bg": "#ECFDF5",
        "pos_border": "#059669",
        "pos_text": "#065F46",
        "border": "#CBD5E1",
        "text": "#0F172A",
        "muted": "#334155",
    },
    "pain_neuro": {
        "id": "pain_neuro",
        "label": "疼痛・神経系（ディープグリーン×イエロー）",
        "bg": "#F3F8F6",
        "surface": "#FFFFFF",
        "surface_alt": "#EBF4F0",
        "primary": "#0F3D33",
        "primary_mid": "#17594A",
        "primary_soft": "#E2F2EC",
        "accent": "#CA8A04",
        "accent_dark": "#854D0E",
        "accent_soft": "#FEF9C3",
        "accent_text_on_dark": "#FDE047",
        "neg_bg": "#FEF2F2",
        "neg_border": "#DC2626",
        "neg_text": "#991B1B",
        "pos_bg": "#ECFDF5",
        "pos_border": "#059669",
        "pos_text": "#065F46",
        "border": "#C5DDD5",
        "text": "#0F172A",
        "muted": "#334155",
    },
    "eval_reasoning": {
        "id": "eval_reasoning",
        "label": "評価・臨床推論（スレートグレー×シアン）",
        "bg": "#F5F7FA",
        "surface": "#FFFFFF",
        "surface_alt": "#EDF2F7",
        "primary": "#1E293B",
        "primary_mid": "#334155",
        "primary_soft": "#E2E8F0",
        "accent": "#0891B2",
        "accent_dark": "#155E75",
        "accent_soft": "#E0F7FA",
        "accent_text_on_dark": "#67E8F9",
        "neg_bg": "#FEF2F2",
        "neg_border": "#DC2626",
        "neg_text": "#991B1B",
        "pos_bg": "#ECFDF5",
        "pos_border": "#059669",
        "pos_text": "#065F46",
        "border": "#CBD5E1",
        "text": "#0F172A",
        "muted": "#334155",
    },
}

# ---------------------------------------------------------------------------
# LLM Summarization Prompt (3-Layer Standalone Clinical Diagram)
# ---------------------------------------------------------------------------

X_CARD_SUMMARY_SYSTEM_PROMPT = """あなたは運動器・リハビリテーション領域に精通した理学療法士であり、医療図解の編集者です。
提供された記事・臨床知見をもとに、記事の目次を抜き出すのではなく、「画像1枚だけで明日の臨床のヒントが完結する図解」のテキスト設計をJSONで作成してください。

【必須の3層構成】
1. conclusion_title（①結論タイトル）
   - 必ず28文字以内。
   - 疑問形や目次風タイトル（「〜とは？」「〜の基本」等）は避け、臨床の結論を端的に言い切る。
2. elements（②根拠・比較・ステップ）
   - 必ず3要素の配列にする（最大3要素）。
   - 各要素は次の3キーを持つオブジェクト：
     - symbol: 対比型（relation_type="contrast"）の場合は "×" または "◎"、フロー・根拠型の場合は "①", "②", "③"
     - focus: 「見るべき点」（必ず20文字以内・体言止めまたは言い切りで完結）
     - interpretation: 「臨床解釈」（必ず20文字以内・体言止めまたは言い切りで完結）
3. takeaway（③明日からのClinical Takeaway）
   - 必ず1文・30文字以内。
   - 明日ベッドサイドで実践できる具体的な行動指針を完結文で書く。
4. relation_type
   - "contrast"（× vs ◎ の対比が適するとき）または "flow"（→ の手順・因果の流れが適するとき）。

【絶対禁止事項】
- 文字の途中切れ（「〜で」「〜と」「〜し」「〜を」「〜に」「〜が」「〜は」「〜の」「…」など）を絶対に起こさないこと。すべての項目を単体で意味が通る完結フレーズにすること。
- 「1メッセージ強調」「結論ファースト型」「CONCLUSION FIRST」「3ステップ・フロー型」「STEP 1」「POINT 1」などのテンプレート名・開発用ラベル・英語メタラベルを一切含めないこと。
- 「若手」「新人」「初心者」「〇年目」などの経験年数表記は禁止。
- 「〜してください」「〜すべきです」などの強い指示口調は避け、臨床家同士で視点を共有する簡潔な表現にすること。
- 根拠にない数値や研究結果の捏造は禁止。
- JSONオブジェクトのみを出力し、Markdownのコードフェンスや前置き解説は付けないこと。

【出力JSONスキーマ】
{
  "conclusion_title": "28字以内の言い切り結論タイトル",
  "relation_type": "flow または contrast",
  "elements": [
    {"symbol": "①", "focus": "見るべき点（20字以内）", "interpretation": "臨床解釈（20字以内）"},
    {"symbol": "②", "focus": "見るべき点（20字以内）", "interpretation": "臨床解釈（20字以内）"},
    {"symbol": "③", "focus": "見るべき点（20字以内）", "interpretation": "臨床解釈（20字以内）"}
  ],
  "takeaway": "明日からの実践1文（30字以内）"
}
"""

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class XCardElement:
    focus: str           # 見るべき点 (<= 20 chars, complete phrase)
    interpretation: str  # 臨床解釈 (<= 20 chars, complete phrase)
    symbol: str = ""     # "①", "②", "③", "×", "◎"


@dataclass
class XCardSpec:
    badge: str
    conclusion_title: str                # Layer 1: <= 28 chars, declarative
    elements: list[XCardElement]         # Layer 2: max 3 elements
    takeaway: str                        # Layer 3: <= 30 chars, 1 sentence
    relation_type: str = "flow"          # "flow" | "contrast"
    mode: str = "note_intro"             # "note_intro" | "noon_pearl"
    footer_right: str = "@benita_pt"
    layout_id: str = "layout_flow"
    palette_id: str = "eval_reasoning"
    extra_meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Text Cleaning, Validation & Non-Truncating Helpers
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://\S+")

# Endings that indicate a broken / mid-sentence fragment
INCOMPLETE_END_RE = re.compile(
    r"(?:[のをにがはでとしやへ、・…,.(\[（【]|"
    r"して|から|ため|ので|つつ|ながら|および|または|により|に対し|において)$"
)

FORBIDDEN_DEV_LABELS: tuple[str, ...] = (
    "CONCLUSION FIRST",
    "1メッセージ強調",
    "結論ファースト型",
    "2軸マトリクス",
    "3ステップ・フロー型",
    "4象限・チェックリスト型",
    "タイムライン・経過型",
    "layout_hero",
    "layout_flow",
    "layout_matrix",
    "layout_grid",
    "layout_timeline",
)

FORBIDDEN_NOON_WORDS: tuple[str, ...] = (
    "note",
    "ノート",
    "記事",
    "リンク",
    "URL",
    "http",
    "続きは",
    "詳しくは",
)


def strip_html(text: str) -> str:
    cleaned = TAG_RE.sub("", text or "")
    cleaned = html.unescape(cleaned)
    return WS_RE.sub(" ", cleaned).strip()


def _strip_prefixes(text: str) -> str:
    s = strip_html(text)
    s = URL_RE.sub("", s).strip()
    s = re.sub(
        r"^(?:[・●◆■①②③④⑤]+|(?:STEP|POINT|PILLAR|PATTERN|PHASE)\s*\d+[:：.\s]*|\d+[.)．:]\s+|-\s+)",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()
    return s


def _has_incomplete_ending(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    if "…" in s or "..." in s:
        return True
    return bool(INCOMPLETE_END_RE.search(s))


def complete_clinical_phrase(text: str, max_chars: int, fallback: str = "評価と反応を再確認") -> str:
    """
    Produce a grammatically complete concise phrase within `max_chars`.
    NEVER appends '…' and NEVER leaves a dangling particle or mid-sentence cut.
    """
    s = _strip_prefixes(text)
    if not s:
        return fallback[:max_chars]

    # Remove trailing polite fluff or punctuation that wastes characters
    s = re.sub(r"[。！？!?]+$", "", s).strip()

    # If already within limit and clean, return it
    if len(s) <= max_chars and not _has_incomplete_ending(s):
        return s

    # Step 1: Drop parenthetical explanations （...） / (...)
    no_paren = re.sub(r"[（(][^）)]*[）)]", "", s).strip()
    no_paren = re.sub(r"\s+", " ", no_paren).strip()
    if 4 <= len(no_paren) <= max_chars and not _has_incomplete_ending(no_paren):
        return no_paren

    # Step 2: Check clean sentence/clause candidates split by sentence punctuation
    candidates: list[str] = []
    for base in (no_paren, s):
        if not base:
            continue
        for part in re.split(r"[。！？!?｜:：]", base):
            part = part.strip(" ・、")
            if 4 <= len(part) <= max_chars and not _has_incomplete_ending(part):
                candidates.append(part)

    if candidates:
        # Prefer the longest complete candidate within max_chars
        candidates.sort(key=len, reverse=True)
        return candidates[0]

    # Step 3: Split by comma and pick a complete clause, trimming trailing incomplete connectors
    for base in (no_paren, s):
        clauses = [c.strip(" ・") for c in base.split("、") if c.strip(" ・")]
        # Prefer later clauses if they carry the conclusion, or earlier if complete
        for clause in reversed(clauses):
            cleaned = re.sub(
                r"(?:して|しながら|つつ|から|ため|ので|により)$",
                "",
                clause,
            ).strip()
            cleaned = re.sub(r"[のをにがはでとしやへ、・]+$", "", cleaned).strip()
            if 4 <= len(cleaned) <= max_chars and not _has_incomplete_ending(cleaned):
                return cleaned

    # Step 4: Last-resort trim to last clean noun/verb boundary without '…'
    base = no_paren or s
    # Split on middle dot '・' and accumulate complete tokens within max_chars
    if "・" in base:
        tokens = [t.strip() for t in base.split("・") if t.strip()]
        acc = ""
        for tok in tokens:
            nxt = f"{acc}・{tok}" if acc else tok
            if len(nxt) <= max_chars:
                acc = nxt
            else:
                break
        if len(acc) >= 4 and not _has_incomplete_ending(acc):
            return acc

    # Trim dangling particles from prefix up to max_chars
    trimmed = base[:max_chars].strip()
    while len(trimmed) > 4 and _has_incomplete_ending(trimmed):
        trimmed = trimmed[:-1].strip()
    if len(trimmed) >= 4 and not _has_incomplete_ending(trimmed):
        return trimmed
    return fallback[:max_chars]


def validate_x_card_dict(data: dict[str, Any], *, mode: str = "note_intro") -> list[str]:
    """Validate structured 3-layer card content against all strict rules."""
    errors: list[str] = []
    title = str(data.get("conclusion_title") or "").strip()
    takeaway = str(data.get("takeaway") or "").strip()
    elements = data.get("elements")

    if not title:
        errors.append("conclusion_titleが空です")
    elif len(title) > MAX_TITLE_CHARS:
        errors.append(f"conclusion_titleが{len(title)}文字で上限{MAX_TITLE_CHARS}文字を超えています")
    elif _has_incomplete_ending(title):
        errors.append(f"conclusion_titleが途中切れしています: {title}")

    if not takeaway:
        errors.append("takeawayが空です")
    elif len(takeaway) > MAX_TAKEAWAY_CHARS:
        errors.append(f"takeawayが{len(takeaway)}文字で上限{MAX_TAKEAWAY_CHARS}文字を超えています")
    elif _has_incomplete_ending(takeaway):
        errors.append(f"takeawayが途中切れしています: {takeaway}")

    if not isinstance(elements, list) or not (2 <= len(elements) <= MAX_ELEMENTS):
        errors.append(f"elementsは2〜{MAX_ELEMENTS}要素の配列にしてください")
    else:
        for idx, el in enumerate(elements, start=1):
            if not isinstance(el, dict):
                errors.append(f"elements[{idx}]が辞書ではありません")
                continue
            focus = str(el.get("focus") or "").strip()
            interp = str(el.get("interpretation") or "").strip()
            if not focus:
                errors.append(f"elements[{idx}].focusが空です")
            elif len(focus) > MAX_FOCUS_CHARS:
                errors.append(f"elements[{idx}].focusが{len(focus)}文字で上限{MAX_FOCUS_CHARS}文字超過: {focus}")
            elif _has_incomplete_ending(focus):
                errors.append(f"elements[{idx}].focusが途中切れしています: {focus}")

            if not interp:
                errors.append(f"elements[{idx}].interpretationが空です")
            elif len(interp) > MAX_INTERP_CHARS:
                errors.append(
                    f"elements[{idx}].interpretationが{len(interp)}文字で上限{MAX_INTERP_CHARS}文字超過: {interp}"
                )
            elif _has_incomplete_ending(interp):
                errors.append(f"elements[{idx}].interpretationが途中切れしています: {interp}")

    # Check forbidden dev labels & experience-year expressions across all text
    all_text = " ".join(
        [title, takeaway]
        + [
            f"{el.get('focus', '')} {el.get('interpretation', '')}"
            for el in (elements if isinstance(elements, list) else [])
            if isinstance(el, dict)
        ]
    )
    for bad in FORBIDDEN_DEV_LABELS:
        if bad.lower() in all_text.lower():
            errors.append(f"開発用ラベル '{bad}' が含まれています")

    for bad_exp in ("若手", "新人", "初心者"):
        if bad_exp in all_text:
            errors.append(f"経験年数表記 '{bad_exp}' は禁止です")

    if mode == "noon_pearl":
        for bad in FORBIDDEN_NOON_WORDS:
            if bad.lower() in all_text.lower():
                errors.append(f"昼用カードに禁止語 '{bad}' が含まれています")

    return errors


# ---------------------------------------------------------------------------
# Optional LLM Summarizer (Gemini / OpenAI, stdlib only)
# ---------------------------------------------------------------------------


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _call_llm_text(system_prompt: str, user_prompt: str) -> str | None:
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not gemini_key and not openai_key:
        return None

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
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1024,
                    "responseMimeType": "application/json",
                },
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
            return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()

        if openai_key:
            model = os.environ.get("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"
            url = "https://api.openai.com/v1/chat/completions"
            payload = {
                "model": model,
                "temperature": 0.2,
                "max_tokens": 1024,
                "response_format": {"type": "json_object"},
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
            return str(data["choices"][0]["message"]["content"]).strip()
    except Exception as exc:
        print(f"[x_card][llm] Summarization API failed, using offline fallback: {exc}")
        return None
    return None


def summarize_for_x_card_via_llm(
    source_text: str,
    *,
    mode: str = "note_intro",
    preferred_relation: str = "",
) -> dict[str, Any] | None:
    """
    Call Gemini / OpenAI with X_CARD_SUMMARY_SYSTEM_PROMPT to produce a validated
    3-layer clinical diagram JSON. Retries once on validation failure.
    """
    extra_rules = ""
    if mode == "noon_pearl":
        extra_rules += (
            "\n※これは昼用の単体完結ポスト画像です。"
            "『note』『ノート』『記事』『URL』『リンク』の語句は絶対に使用しないでください。\n"
        )
    if preferred_relation in ("flow", "contrast"):
        extra_rules += f"\n※relation_type は可能なら \"{preferred_relation}\" を選んでください。\n"

    base_user_prompt = (
        f"以下の臨床記事・知見データから、画像1枚で臨床のヒントが完結する3層図解JSONを作成してください。\n"
        f"{extra_rules}\n"
        f"【元データ】\n{source_text[:6000]}"
    )

    feedback = ""
    for _attempt in range(2):
        prompt = (
            f"{base_user_prompt}\n\n【前回の修正点（必ず解消すること）】\n{feedback}"
            if feedback
            else base_user_prompt
        )
        raw = _call_llm_text(X_CARD_SUMMARY_SYSTEM_PROMPT, prompt)
        if not raw:
            return None
        parsed = _extract_json_object(raw)
        if not parsed:
            feedback = "有効なJSONオブジェクトのみを返してください。"
            continue
        errs = validate_x_card_dict(parsed, mode=mode)
        if not errs:
            return parsed
        feedback = "\n".join(f"- {e}" for e in errs)
    return None


# ---------------------------------------------------------------------------
# Automatic Palette & Layout Selection + Consecutive Duplicate Avoidance
# ---------------------------------------------------------------------------


def detect_palette_id(text: str, domain: str = "") -> str:
    clean_domain = re.sub(r"運動器・?NotebookLM文献|NotebookLM文献", "", domain or "").strip()
    combined = f"{clean_domain} {text}".lower()

    pain_neuro_kw = (
        "神経", "脊髄", "脊髄症", "狭窄症", "lss", "csm", "疼痛", "痛み", "しびれ",
        "麻痺", "反射", "レッドフラッグ", "中枢", "脳卒中", "感覚", "神経性", "慢性痛",
        "腰痛", "圧迫骨折", "間欠跛行", "頚椎", "頸椎",
    )
    msk_kw = (
        "バイオメカニクス", "運動器", "骨盤", "股関節", "膝", "tka", "oa", "骨折",
        "大腿骨", "筋力", "筋活動", "重心", "起立", "座位", "回旋", "rom",
        "可動域", "荷重", "離床", "体幹", "筋電図", "床反力", "アライメント", "肩",
        "足関節", "伸展", "屈曲", "後傾", "前傾", "中臀筋", "中殿筋", "腹圧", "tfl",
    )
    eval_kw = (
        "評価", "臨床推論", "カットオフ", "mcid", "mdc", "bbs", "tug", "spwt",
        "片脚立位", "検査値", "アルブミン", "crp", "hb", "貧血", "栄養", "glim",
        "測定", "再評価", "質問票", "指標", "スケール", "鑑別", "チェック", "中止基準",
        "測定条件", "テスト",
    )

    dom_l = clean_domain.lower()
    scores = {
        "pain_neuro": sum(2 if k in dom_l else 1 for k in pain_neuro_kw if k in combined),
        "musculoskeletal": sum(2 if k in dom_l else 1 for k in msk_kw if k in combined),
        "eval_reasoning": sum(2 if k in dom_l else 1 for k in eval_kw if k in combined),
    }

    best = max(
        ("eval_reasoning", "pain_neuro", "musculoskeletal"),
        key=lambda k: scores[k],
    )
    if scores[best] == 0:
        return "eval_reasoning"
    return best


def get_recent_layout_ids(*states: dict[str, Any] | None, window: int = 2) -> list[str]:
    collected: list[tuple[str, int, str]] = []
    seq = 0
    for st in states:
        if not st or not isinstance(st, dict):
            continue
        for entry in st.get("history", []):
            lid = entry.get("layout_id")
            if lid in VALID_LAYOUT_IDS:
                date_key = str(entry.get("date") or "")
                collected.append((date_key, seq, str(lid)))
                seq += 1
    collected.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in collected[-window:]]


def select_layout_id(
    text: str,
    *,
    avoid_layouts: list[str] | None = None,
    preferred_layout: str | None = None,
) -> str:
    avoid_set = {lid for lid in (avoid_layouts or []) if lid in VALID_LAYOUT_IDS}
    if preferred_layout in VALID_LAYOUT_IDS and preferred_layout not in avoid_set:
        return preferred_layout

    t = text.lower()
    scores: dict[str, int] = {lid: 0 for lid in VALID_LAYOUT_IDS}

    for kw in ("vs", "違い", "別物", "比較", "ではない", "≠", "使い分け", "対比", "思い込み", "決めつけ"):
        if kw in t:
            scores["layout_matrix"] += 2
    for kw in ("ステップ", "step", "手順", "フロー", "順番", "プロセス", "推論", "流れ", "進め方", "セット"):
        if kw in t:
            scores["layout_flow"] += 2
    for kw in ("チェック", "鑑別", "視点", "ポイント", "要因", "原因", "観察所見", "触診"):
        if kw in t:
            scores["layout_grid"] += 2
    for kw in ("実は", "本当", "要注意", "なぜ", "だけ", "mcid", "mdc", "カットオフ", "数値"):
        if kw in t:
            scores["layout_hero"] += 2
    for kw in ("術後", "翌日", "早期", "病期", "急性期", "回復期", "経過", "プロトコル", "段階", "時期別"):
        if kw in t:
            scores["layout_timeline"] += 2

    ranked = sorted(
        VALID_LAYOUT_IDS,
        key=lambda lid: (scores[lid], -VALID_LAYOUT_IDS.index(lid)),
        reverse=True,
    )
    for lid in ranked:
        if lid not in avoid_set:
            return lid
    return ranked[0]


# ---------------------------------------------------------------------------
# Spec Builders (Note-Intro Cards, Noon Pearl Cards, & Direct Dict/Markdown)
# ---------------------------------------------------------------------------


def _elements_from_validated_dict(
    data: dict[str, Any],
    *,
    relation_type: str,
) -> list[XCardElement]:
    default_syms = ("×", "◎", "◎") if relation_type == "contrast" else ("①", "②", "③")
    out: list[XCardElement] = []
    for idx, raw in enumerate(data.get("elements", [])[:MAX_ELEMENTS]):
        sym = str(raw.get("symbol") or "").strip()
        if sym not in ("①", "②", "③", "×", "◎", "1", "2", "3"):
            sym = default_syms[idx] if idx < len(default_syms) else f"{idx + 1}"
        out.append(
            XCardElement(
                focus=str(raw["focus"]).strip(),
                interpretation=str(raw["interpretation"]).strip(),
                symbol=sym,
            )
        )
    return out


def build_x_card_spec_from_structured(
    data: dict[str, Any],
    *,
    badge: str = "臨床の視点",
    mode: str = "note_intro",
    footer_right: str = "@benita_pt",
    layout_id: str | None = None,
    palette_id: str | None = None,
    avoid_layouts: list[str] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> XCardSpec:
    """Build XCardSpec directly from a pre-validated 3-layer dict."""
    errs = validate_x_card_dict(data, mode=mode)
    if errs:
        raise ValueError(f"Invalid X card structured spec: {errs}")

    relation_type = str(data.get("relation_type") or "flow").strip()
    if relation_type not in ("flow", "contrast"):
        relation_type = "flow"

    combined = " ".join(
        [
            badge,
            data["conclusion_title"],
            data["takeaway"],
            *(f"{e['focus']} {e['interpretation']}" for e in data["elements"]),
        ]
    )
    preferred = "layout_matrix" if relation_type == "contrast" else None
    resolved_layout = (
        layout_id
        if layout_id in VALID_LAYOUT_IDS
        else select_layout_id(combined, avoid_layouts=avoid_layouts, preferred_layout=preferred)
    )
    resolved_palette = (
        palette_id if palette_id in PALETTES else detect_palette_id(combined, domain=badge)
    )

    clean_badge = complete_clinical_phrase(badge.split("｜")[0], max_chars=14, fallback="臨床メモ")
    if mode == "noon_pearl":
        for bad in FORBIDDEN_NOON_WORDS:
            clean_badge = re.sub(re.escape(bad), "", clean_badge, flags=re.IGNORECASE).strip()
        if not clean_badge:
            clean_badge = "臨床メモ"

    return XCardSpec(
        badge=clean_badge,
        conclusion_title=str(data["conclusion_title"]).strip(),
        elements=_elements_from_validated_dict(data, relation_type=relation_type),
        takeaway=str(data["takeaway"]).strip(),
        relation_type=relation_type,
        mode=mode,
        footer_right=footer_right,
        layout_id=resolved_layout,
        palette_id=resolved_palette,
        extra_meta=extra_meta or {},
    )


def build_x_card_spec_from_article(
    art: dict[str, Any],
    *,
    avoid_layouts: list[str] | None = None,
    forced_layout: str | None = None,
    forced_palette: str | None = None,
    use_llm: bool = True,
) -> XCardSpec:
    """
    Distill an article entry into a standalone 3-layer 16:9 X clinical diagram card.
    Priority:
      1. Explicit curated `art["x_card"]` dict (if valid)
      2. LLM structured summarization via `X_CARD_SUMMARY_SYSTEM_PROMPT` (if API key available)
      3. Strict non-truncating deterministic fallback
    """
    badge_raw = str(art.get("badge") or "臨床メモ").split("｜")[0]
    badge = complete_clinical_phrase(badge_raw, max_chars=14, fallback="臨床メモ")

    # 1. Pre-curated structured x_card in article dict
    curated = art.get("x_card")
    if isinstance(curated, dict) and not validate_x_card_dict(curated, mode="note_intro"):
        return build_x_card_spec_from_structured(
            curated,
            badge=badge,
            mode="note_intro",
            layout_id=forced_layout,
            palette_id=forced_palette,
            avoid_layouts=avoid_layouts,
            extra_meta={"article_key": art.get("key", ""), "source": "curated"},
        )

    combined = "\n".join(
        filter(
            None,
            [
                f"記事タイトル: {art.get('title', '')}",
                f"核心メッセージ: {strip_html(art.get('header_msg', ''))}",
                f"投稿文: {art.get('post', '')}",
                f"要点1: {strip_html(art.get('box1_title', ''))} / {strip_html(art.get('box1_desc', ''))} / {strip_html(art.get('box1_point', ''))}",
                f"要点2: {strip_html(art.get('box2_title', ''))} / {strip_html(art.get('box2_desc', ''))} / {strip_html(art.get('box2_point', ''))}",
                f"臨床推論: {strip_html(art.get('mid_text', ''))} / {strip_html(art.get('mid_note', ''))}",
                "ステップ:\n"
                + "\n".join(
                    f"- {strip_html(st[1])}: {strip_html(st[2])}"
                    for st in art.get("steps", [])
                    if len(st) >= 3
                ),
            ],
        )
    )

    layout_id = (
        forced_layout
        if forced_layout in VALID_LAYOUT_IDS
        else select_layout_id(combined, avoid_layouts=avoid_layouts)
    )
    palette_id = (
        forced_palette
        if forced_palette in PALETTES
        else detect_palette_id(combined, domain=badge_raw)
    )
    pref_rel = "contrast" if layout_id == "layout_matrix" else "flow"

    # 2. LLM summarization
    if use_llm:
        llm_data = summarize_for_x_card_via_llm(
            combined,
            mode="note_intro",
            preferred_relation=pref_rel,
        )
        if llm_data:
            return build_x_card_spec_from_structured(
                llm_data,
                badge=badge,
                mode="note_intro",
                layout_id=forced_layout or layout_id,
                palette_id=palette_id,
                avoid_layouts=avoid_layouts,
                extra_meta={"article_key": art.get("key", ""), "source": "llm"},
            )

    # 3. Deterministic non-truncating fallback
    conclusion_title = complete_clinical_phrase(
        art.get("header_msg") or art.get("header_title") or art.get("title", ""),
        max_chars=MAX_TITLE_CHARS,
        fallback="局所と動作を統合して評価する",
    )

    elements: list[XCardElement] = []
    default_syms = ("×", "◎", "◎") if pref_rel == "contrast" else ("①", "②", "③")

    for idx, step in enumerate(art.get("steps", [])[:MAX_ELEMENTS]):
        lbl = complete_clinical_phrase(
            step[1] if len(step) > 1 else "",
            max_chars=MAX_FOCUS_CHARS,
            fallback=f"確認ポイント{idx + 1}",
        )
        interp = complete_clinical_phrase(
            step[2] if len(step) > 2 else "",
            max_chars=MAX_INTERP_CHARS,
            fallback="動作と反応で再評価",
        )
        elements.append(XCardElement(focus=lbl, interpretation=interp, symbol=default_syms[idx]))

    if len(elements) < 3:
        fallback_pairs = [
            (art.get("box1_title", ""), art.get("box1_tag") or art.get("box1_desc", "")),
            (art.get("box2_title", ""), art.get("box2_tag") or art.get("box2_desc", "")),
            ("再評価の軸", art.get("mid_text", "")),
        ]
        for idx, (f_raw, i_raw) in enumerate(fallback_pairs):
            if len(elements) >= 3:
                break
            elements.append(
                XCardElement(
                    focus=complete_clinical_phrase(f_raw, MAX_FOCUS_CHARS, f"確認軸{idx + 1}"),
                    interpretation=complete_clinical_phrase(i_raw, MAX_INTERP_CHARS, "反応を見て負荷調整"),
                    symbol=default_syms[len(elements)],
                )
            )

    takeaway = complete_clinical_phrase(
        art.get("mid_text") or art.get("header_msg") or "局所所見と動作反応をセットで再評価する",
        max_chars=MAX_TAKEAWAY_CHARS,
        fallback="局所所見と動作反応をセットで再評価する",
    )

    return XCardSpec(
        badge=badge,
        conclusion_title=conclusion_title,
        elements=elements[:MAX_ELEMENTS],
        takeaway=takeaway,
        relation_type=pref_rel,
        mode="note_intro",
        footer_right="@benita_pt",
        layout_id=layout_id,
        palette_id=palette_id,
        extra_meta={"article_key": art.get("key", ""), "source": "fallback"},
    )


def build_x_card_spec_from_noon(
    *,
    topic: str,
    post: str,
    domain: str = "運動器",
    reference: str = "",
    summary: str = "",
    avoid_layouts: list[str] | None = None,
    forced_layout: str | None = None,
    forced_palette: str | None = None,
    use_llm: bool = True,
) -> XCardSpec:
    """
    Build a standalone 3-layer 16:9 X card spec for a noon clinical pearl post.
    Strictly link-free & zero-promotion (no 'note' or URL text anywhere on the card).
    """
    combined = f"領域: {domain}\nトピック: {topic}\n要約: {summary}\n投稿文:\n{post}\n参考: {reference}"
    layout_id = (
        forced_layout
        if forced_layout in VALID_LAYOUT_IDS
        else select_layout_id(combined, avoid_layouts=avoid_layouts)
    )
    palette_id = (
        forced_palette
        if forced_palette in PALETTES
        else detect_palette_id(combined, domain=domain)
    )
    pref_rel = "contrast" if layout_id == "layout_matrix" else "flow"

    clean_badge = complete_clinical_phrase(domain or "臨床メモ", max_chars=14, fallback="臨床メモ")
    for bad in FORBIDDEN_NOON_WORDS:
        clean_badge = re.sub(re.escape(bad), "", clean_badge, flags=re.IGNORECASE).strip()
    if not clean_badge:
        clean_badge = "臨床メモ"

    if use_llm:
        llm_data = summarize_for_x_card_via_llm(
            combined,
            mode="noon_pearl",
            preferred_relation=pref_rel,
        )
        if llm_data:
            return build_x_card_spec_from_structured(
                llm_data,
                badge=clean_badge,
                mode="noon_pearl",
                footer_right="@benita_pt",
                layout_id=forced_layout or layout_id,
                palette_id=palette_id,
                avoid_layouts=avoid_layouts,
                extra_meta={"source": "llm"},
            )

    # Deterministic non-truncating fallback
    sentences = [
        s.strip()
        for s in re.split(r"[\n。！？!?]+", strip_html(URL_RE.sub("", post)))
        if len(s.strip()) >= 6
    ]
    conclusion_title = complete_clinical_phrase(
        topic,
        max_chars=MAX_TITLE_CHARS,
        fallback="評価の条件統一が臨床判断の軸",
    )
    default_syms = ("×", "◎", "◎") if pref_rel == "contrast" else ("①", "②", "③")
    default_focuses = (
        ("従来の見方", "見るべき評価軸", "次の一手")
        if pref_rel == "contrast"
        else ("確認すべき前提", "評価の見極め", "介入の調整")
    )
    elements: list[XCardElement] = []
    for idx in range(MAX_ELEMENTS):
        raw_sent = sentences[idx] if idx < len(sentences) else topic
        elements.append(
            XCardElement(
                focus=default_focuses[idx],
                interpretation=complete_clinical_phrase(
                    raw_sent,
                    max_chars=MAX_INTERP_CHARS,
                    fallback="条件を揃え反応を確認",
                ),
                symbol=default_syms[idx],
            )
        )

    takeaway_src = sentences[-1] if sentences else topic
    takeaway = complete_clinical_phrase(
        takeaway_src,
        max_chars=MAX_TAKEAWAY_CHARS,
        fallback="測定条件を揃えて反応を再評価する",
    )
    for bad in FORBIDDEN_NOON_WORDS:
        takeaway = re.sub(re.escape(bad), "", takeaway, flags=re.IGNORECASE).strip()

    return XCardSpec(
        badge=clean_badge,
        conclusion_title=conclusion_title,
        elements=elements,
        takeaway=takeaway or "測定条件を揃えて反応を再評価する",
        relation_type=pref_rel,
        mode="noon_pearl",
        footer_right="@benita_pt",
        layout_id=layout_id,
        palette_id=palette_id,
        extra_meta={"source": "fallback"},
    )


# ---------------------------------------------------------------------------
# 3-Layer High-Legibility 1200x675px (16:9) HTML/CSS Templates
# Rules enforced:
# - ZERO layout names or English meta labels (no "CONCLUSION FIRST", no "STEP")
# - Minimum font size >= 22px across EVERY element (Title 38-44px)
# - Large expanded 3-element diagram area (no giant 1-line center box)
# - Explicit visual relation symbols (→, ↓, × vs ◎)
# ---------------------------------------------------------------------------


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _title_font_size(title: str) -> int:
    """Choose 38-44px so <= 28 chars fits cleanly without awkward 1-char wraps."""
    n = len(title)
    if n <= 20:
        return 44
    if n <= 23:
        return 42
    if n <= 26:
        return 40
    return 38


def _symbol_variant_class(sym: str) -> str:
    if sym in ("×", "✕", "✖"):
        return "is-neg"
    if sym in ("◎", "○", "◯"):
        return "is-pos"
    return "is-num"


def _render_body_flow(spec: XCardSpec, p: dict[str, str]) -> str:
    """3 large side-by-side cards connected by bold '→' arrows."""
    cards: list[str] = []
    elems = spec.elements[:MAX_ELEMENTS]
    for idx, el in enumerate(elems, start=1):
        sym = el.symbol or ("①", "②", "③")[idx - 1]
        sym_cls = _symbol_variant_class(sym)
        cards.append(
            f"""
            <div class="col-card {sym_cls}">
              <div class="col-top">
                <span class="sym-badge {sym_cls}">{_esc(sym)}</span>
                <span class="role-tag">見るべき点</span>
              </div>
              <div class="focus-txt">{_esc(el.focus)}</div>
              <div class="down-arrow">↓</div>
              <div class="interp-box {sym_cls}">
                <div class="interp-tag">臨床解釈</div>
                <div class="interp-txt">{_esc(el.interpretation)}</div>
              </div>
            </div>
            """
        )
        if idx < len(elems):
            cards.append('<div class="between-arrow">→</div>')
    return f'<div class="flow-stage">{"".join(cards)}</div>'


def _render_body_matrix(spec: XCardSpec, p: dict[str, str]) -> str:
    """Contrast layout highlighting × vs ◎ with bold '→' causal arrows."""
    elems = spec.elements[:MAX_ELEMENTS]
    rows: list[str] = []
    for idx, el in enumerate(elems):
        sym = el.symbol if el.symbol in ("×", "◎") else ("×" if idx == 0 else "◎")
        sym_cls = _symbol_variant_class(sym)
        role_lbl = "陥りがちな見方" if sym == "×" else "臨床で見るべき軸"
        rows.append(
            f"""
            <div class="row-card {sym_cls}">
              <div class="row-left">
                <span class="sym-badge lg {sym_cls}">{_esc(sym)}</span>
                <div class="row-focus-wrap">
                  <div class="role-tag inline {sym_cls}">{_esc(role_lbl)}</div>
                  <div class="row-focus-txt">{_esc(el.focus)}</div>
                </div>
              </div>
              <div class="row-arrow {sym_cls}">→</div>
              <div class="row-right {sym_cls}">
                <div class="interp-tag inline">臨床解釈</div>
                <div class="row-interp-txt">{_esc(el.interpretation)}</div>
              </div>
            </div>
            """
        )
    return f'<div class="rows-stage">{"".join(rows)}</div>'


def _render_body_grid(spec: XCardSpec, p: dict[str, str]) -> str:
    """3 full-width horizontal structured rows: [① 見るべき点] → [臨床解釈]."""
    elems = spec.elements[:MAX_ELEMENTS]
    rows: list[str] = []
    for idx, el in enumerate(elems, start=1):
        sym = el.symbol or ("①", "②", "③")[idx - 1]
        sym_cls = _symbol_variant_class(sym)
        rows.append(
            f"""
            <div class="row-card {sym_cls}">
              <div class="row-left">
                <span class="sym-badge lg {sym_cls}">{_esc(sym)}</span>
                <div class="row-focus-wrap">
                  <div class="role-tag inline">見るべき点</div>
                  <div class="row-focus-txt">{_esc(el.focus)}</div>
                </div>
              </div>
              <div class="row-arrow">→</div>
              <div class="row-right {sym_cls}">
                <div class="interp-tag inline">臨床解釈</div>
                <div class="row-interp-txt">{_esc(el.interpretation)}</div>
              </div>
            </div>
            """
        )
    return f'<div class="rows-stage">{"".join(rows)}</div>'


def _render_body_hero(spec: XCardSpec, p: dict[str, str]) -> str:
    """
    Expanded 3-element evidence layout (replaces the old huge 1-line conclusion box).
    Uses full central area for 3 bold evidence cards with ×/◎ or ①②③ & ↓ arrows.
    """
    elems = spec.elements[:MAX_ELEMENTS]
    cards: list[str] = []
    for idx, el in enumerate(elems, start=1):
        sym = el.symbol or ("①", "②", "③")[idx - 1]
        sym_cls = _symbol_variant_class(sym)
        cards.append(
            f"""
            <div class="col-card elevated {sym_cls}">
              <div class="col-top">
                <span class="sym-badge {sym_cls}">{_esc(sym)}</span>
                <span class="role-tag">見るべき点</span>
              </div>
              <div class="focus-txt">{_esc(el.focus)}</div>
              <div class="down-arrow">↓</div>
              <div class="interp-box {sym_cls}">
                <div class="interp-tag">臨床解釈</div>
                <div class="interp-txt">{_esc(el.interpretation)}</div>
              </div>
            </div>
            """
        )
    return f'<div class="tri-stage">{"".join(cards)}</div>'


def _render_body_timeline(spec: XCardSpec, p: dict[str, str]) -> str:
    """Horizontal progression rail (① → ② → ③) over 3 large cards."""
    elems = spec.elements[:MAX_ELEMENTS]
    nodes: list[str] = []
    for idx, el in enumerate(elems, start=1):
        sym = el.symbol or ("①", "②", "③")[idx - 1]
        sym_cls = _symbol_variant_class(sym)
        nodes.append(
            f"""
            <div class="tl-col">
              <div class="tl-node-row">
                <span class="tl-circle {sym_cls}">{_esc(sym)}</span>
                {('<span class="tl-arrow">→</span>' if idx < len(elems) else '')}
              </div>
              <div class="col-card compact {sym_cls}">
                <div class="role-tag">見るべき点</div>
                <div class="focus-txt">{_esc(el.focus)}</div>
                <div class="down-arrow">↓</div>
                <div class="interp-box {sym_cls}">
                  <div class="interp-tag">臨床解釈</div>
                  <div class="interp-txt">{_esc(el.interpretation)}</div>
                </div>
              </div>
            </div>
            """
        )
    return f'<div class="tl-stage">{"".join(nodes)}</div>'


def build_x_card_html(spec: XCardSpec) -> str:
    p = PALETTES.get(spec.palette_id, PALETTES["eval_reasoning"])
    layout_id = spec.layout_id if spec.layout_id in VALID_LAYOUT_IDS else "layout_flow"

    if layout_id == "layout_matrix":
        body_html = _render_body_matrix(spec, p)
    elif layout_id == "layout_grid":
        body_html = _render_body_grid(spec, p)
    elif layout_id == "layout_hero":
        body_html = _render_body_hero(spec, p)
    elif layout_id == "layout_timeline":
        body_html = _render_body_timeline(spec, p)
    else:
        body_html = _render_body_flow(spec, p)

    title_px = _title_font_size(spec.conclusion_title)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: {CARD_WIDTH}px;
    height: {CARD_HEIGHT}px;
    background: {p["bg"]};
    font-family: "Hiragino Kaku Gothic ProN", "Hiragino Sans", "Noto Sans CJK JP", "Noto Sans JP", "Yu Gothic", sans-serif;
    color: {p["text"]};
    overflow: hidden;
  }}
  .canvas {{
    width: {CARD_WIDTH}px;
    height: {CARD_HEIGHT}px;
    padding: 20px 28px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 14px;
  }}

  /* Layer 1: Conclusion Title Header (38-44px, zero dev labels) */
  .hdr {{
    background: linear-gradient(125deg, {p["primary"]} 0%, {p["primary_mid"]} 100%);
    color: #FFFFFF;
    border-radius: 16px;
    padding: 16px 26px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.14);
  }}
  .hdr-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  .hdr-badge {{
    display: inline-block;
    background: {p["accent"]};
    color: #FFFFFF;
    font-size: 22px;
    font-weight: 800;
    padding: 3px 16px;
    border-radius: 999px;
    line-height: 1.25;
  }}
  .hdr-handle {{
    font-size: 22px;
    font-weight: 700;
    color: {p["accent_text_on_dark"]};
    line-height: 1.25;
  }}
  .hdr-title {{
    font-size: {title_px}px;
    font-weight: 900;
    line-height: 1.22;
    letter-spacing: -0.015em;
    color: #FFFFFF;
  }}

  /* Layer 2: Expanded 3-Element Clinical Diagram Area */
  .stage {{
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }}

  /* Shared visual symbols & role tags (all >= 22px) */
  .sym-badge {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 44px;
    height: 44px;
    padding: 0 10px;
    border-radius: 12px;
    font-size: 26px;
    font-weight: 900;
    color: #FFFFFF;
    background: {p["primary"]};
    flex-shrink: 0;
  }}
  .sym-badge.lg {{
    min-width: 52px;
    height: 52px;
    font-size: 30px;
    border-radius: 14px;
  }}
  .sym-badge.is-neg {{ background: {p["neg_border"]}; }}
  .sym-badge.is-pos {{ background: {p["pos_border"]}; }}
  .sym-badge.is-num {{ background: {p["primary"]}; }}

  .role-tag {{
    display: inline-block;
    align-self: flex-start;
    background: {p["primary_soft"]};
    color: {p["primary"]};
    font-size: 22px;
    font-weight: 800;
    padding: 3px 12px;
    border-radius: 8px;
    line-height: 1.2;
  }}
  .role-tag.is-neg {{
    background: {p["neg_bg"]};
    color: {p["neg_text"]};
  }}
  .role-tag.is-pos {{
    background: {p["pos_bg"]};
    color: {p["pos_text"]};
  }}
  .role-tag.inline {{
    margin-bottom: 4px;
  }}

  .interp-tag {{
    display: inline-block;
    font-size: 22px;
    font-weight: 800;
    color: {p["accent_dark"]};
    line-height: 1.2;
    margin-bottom: 4px;
  }}
  .interp-tag.inline {{
    margin-bottom: 4px;
  }}

  /* Column-based layouts (flow, hero, timeline) */
  .flow-stage {{
    flex: 1;
    display: flex;
    align-items: stretch;
    gap: 10px;
  }}
  .tri-stage {{
    flex: 1;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
  }}
  .between-arrow {{
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 36px;
    font-weight: 900;
    color: {p["accent"]};
    flex-shrink: 0;
    width: 34px;
  }}
  .col-card {{
    flex: 1;
    background: {p["surface"]};
    border: 2px solid {p["border"]};
    border-top: 8px solid {p["primary"]};
    border-radius: 16px;
    padding: 18px 20px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 8px;
    box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
  }}
  .col-card.is-neg {{ border-top-color: {p["neg_border"]}; }}
  .col-card.is-pos {{ border-top-color: {p["pos_border"]}; }}
  .col-card.elevated {{
    border-width: 2.5px;
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.09);
  }}
  .col-card.compact {{
    padding: 14px 18px;
    gap: 6px;
  }}
  .col-top {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .focus-txt {{
    font-size: 26px;
    font-weight: 900;
    color: {p["text"]};
    line-height: 1.3;
    min-height: 68px;
    display: flex;
    align-items: center;
  }}
  .down-arrow {{
    text-align: center;
    font-size: 28px;
    font-weight: 900;
    color: {p["accent"]};
    line-height: 1;
  }}
  .interp-box {{
    background: {p["accent_soft"]};
    border-left: 6px solid {p["accent"]};
    border-radius: 12px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    flex: 1;
  }}
  .interp-box.is-neg {{
    background: {p["neg_bg"]};
    border-left-color: {p["neg_border"]};
  }}
  .interp-box.is-neg .interp-tag,
  .row-right.is-neg .interp-tag {{
    color: {p["neg_text"]};
  }}
  .interp-box.is-pos {{
    background: {p["pos_bg"]};
    border-left-color: {p["pos_border"]};
  }}
  .interp-box.is-pos .interp-tag,
  .row-right.is-pos .interp-tag {{
    color: {p["pos_text"]};
  }}
  .interp-txt {{
    font-size: 24px;
    font-weight: 800;
    color: {p["text"]};
    line-height: 1.32;
  }}

  /* Row-based layouts (matrix contrast & grid structured rows) */
  .rows-stage {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 12px;
  }}
  .row-card {{
    flex: 1;
    background: {p["surface"]};
    border: 2px solid {p["border"]};
    border-left: 8px solid {p["primary"]};
    border-radius: 14px;
    padding: 12px 20px;
    display: grid;
    grid-template-columns: 1fr 48px 1fr;
    align-items: center;
    gap: 12px;
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05);
  }}
  .row-card.is-neg {{ border-left-color: {p["neg_border"]}; }}
  .row-card.is-pos {{ border-left-color: {p["pos_border"]}; }}
  .row-left {{
    display: flex;
    align-items: center;
    gap: 14px;
  }}
  .row-focus-wrap {{
    display: flex;
    flex-direction: column;
  }}
  .row-focus-txt {{
    font-size: 26px;
    font-weight: 900;
    color: {p["text"]};
    line-height: 1.25;
  }}
  .row-arrow {{
    text-align: center;
    font-size: 36px;
    font-weight: 900;
    color: {p["accent"]};
  }}
  .row-arrow.is-neg {{ color: {p["neg_border"]}; }}
  .row-arrow.is-pos {{ color: {p["pos_border"]}; }}
  .row-right {{
    background: {p["accent_soft"]};
    border-radius: 10px;
    padding: 10px 16px;
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }}
  .row-right.is-neg {{ background: {p["neg_bg"]}; }}
  .row-right.is-pos {{ background: {p["pos_bg"]}; }}
  .row-interp-txt {{
    font-size: 25px;
    font-weight: 800;
    color: {p["text"]};
    line-height: 1.25;
  }}

  /* Timeline layout */
  .tl-stage {{
    flex: 1;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
  }}
  .tl-col {{
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .tl-node-row {{
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    height: 50px;
  }}
  .tl-circle {{
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: {p["accent"]};
    color: #FFFFFF;
    font-size: 26px;
    font-weight: 900;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 4px 10px rgba(15, 23, 42, 0.16);
  }}
  .tl-circle.is-neg {{ background: {p["neg_border"]}; }}
  .tl-circle.is-pos {{ background: {p["pos_border"]}; }}
  .tl-arrow {{
    position: absolute;
    right: -22px;
    font-size: 32px;
    font-weight: 900;
    color: {p["accent"]};
  }}

  /* Layer 3: Clinical Takeaway Bar (>= 22px) */
  .takeaway {{
    background: {p["primary"]};
    color: #FFFFFF;
    border-radius: 14px;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    gap: 16px;
    box-shadow: 0 6px 16px rgba(15, 23, 42, 0.14);
  }}
  .takeaway-tag {{
    background: {p["accent"]};
    color: #FFFFFF;
    font-size: 22px;
    font-weight: 900;
    padding: 5px 16px;
    border-radius: 10px;
    white-space: nowrap;
    flex-shrink: 0;
  }}
  .takeaway-txt {{
    font-size: 26px;
    font-weight: 900;
    color: #FFFFFF;
    line-height: 1.25;
    flex: 1;
  }}
</style>
</head>
<body>
  <div class="canvas">
    <div class="hdr">
      <div class="hdr-top">
        <div class="hdr-badge">{_esc(spec.badge)}</div>
        <div class="hdr-handle">{_esc(spec.footer_right)}</div>
      </div>
      <div class="hdr-title">{_esc(spec.conclusion_title)}</div>
    </div>

    <div class="stage">
      {body_html}
    </div>

    <div class="takeaway">
      <div class="takeaway-tag">明日の一手</div>
      <div class="takeaway-txt">{_esc(spec.takeaway)}</div>
    </div>
  </div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Headless Chrome Renderer (1200x675px @ 2x)
# ---------------------------------------------------------------------------


def find_chrome() -> str:
    for b in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
        p = shutil.which(b)
        if p:
            return p
    for mac_path in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if Path(mac_path).exists():
            return mac_path
    raise RuntimeError("Chrome binary not found")


def render_x_card_png(html_path: Path, png_path: Path) -> None:
    chrome = find_chrome()
    with tempfile.TemporaryDirectory() as user_data_dir:
        subprocess.run(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--ignore-certificate-errors",
                "--hide-scrollbars",
                f"--user-data-dir={user_data_dir}",
                f"--window-size={CARD_WIDTH},{CARD_HEIGHT}",
                "--force-device-scale-factor=2",
                f"--screenshot={png_path}",
                f"file://{html_path.resolve()}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def generate_x_card_files(
    spec: XCardSpec,
    html_path: Path,
    png_path: Path,
    *,
    render: bool = True,
) -> dict[str, str]:
    html_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(build_x_card_html(spec), encoding="utf-8")
    if render:
        render_x_card_png(html_path, png_path)
    return {
        "layout_id": spec.layout_id,
        "layout_label": LAYOUT_LABELS[spec.layout_id],
        "palette_id": spec.palette_id,
        "palette_label": PALETTES[spec.palette_id]["label"],
        "html_path": str(html_path),
        "png_path": str(png_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a 3-layer 16:9 X clinical diagram card.")
    parser.add_argument("--layout", choices=VALID_LAYOUT_IDS, default=None, help="Force a specific layout_id")
    parser.add_argument("--palette", choices=tuple(PALETTES.keys()), default=None, help="Force a specific palette_id")
    parser.add_argument("--mode", choices=("note_intro", "noon_pearl"), default="note_intro")
    parser.add_argument("--spec-json", default=None, help="Path to a 3-layer structured JSON spec file")
    parser.add_argument("--badge", default="臨床の視点")
    parser.add_argument("--topic", default="LSS歩行評価：歩ける距離と日常歩数のズレ")
    parser.add_argument(
        "--post",
        default=(
            "リハ室で500m歩けても、自宅の歩数が伸びないケースは珍しくありません。\n"
            "SPWTの測定条件をそろえ、歩行直後の脚痛と回復時間を見ることが判断の軸になります。\n"
            "7日間の歩数ログから生活障壁を切り分けるのが、次の一手を決める鍵です。"
        ),
    )
    parser.add_argument("--domain", default="疼痛・神経系")
    parser.add_argument("--out-html", default="x_card_test.html")
    parser.add_argument("--out-png", default="x_card_test.png")
    args = parser.parse_args()

    if args.spec_json:
        raw = json.loads(Path(args.spec_json).read_text(encoding="utf-8"))
        spec = build_x_card_spec_from_structured(
            raw,
            badge=raw.get("badge") or args.badge,
            mode=args.mode,
            layout_id=args.layout,
            palette_id=args.palette,
        )
    else:
        spec = build_x_card_spec_from_noon(
            topic=args.topic,
            post=args.post,
            domain=args.domain,
            forced_layout=args.layout,
            forced_palette=args.palette,
        )
        spec.mode = args.mode

    info = generate_x_card_files(spec, Path(args.out_html), Path(args.out_png), render=True)
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
