#!/usr/bin/env python3
"""
X (Twitter) 16:9 (1200x675px) Dynamic Diagram Card Renderer
Supports 5 layout patterns x 3 theme color palettes with consecutive-duplicate avoidance.

Shared by:
- run_daily_x.py (Night + Next Morning note-intro cards & Next Noon clinical pearl card)
- noon_post_generator.py (Noon standalone clinical pearl card + layout history tracking)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants: 5 Layouts & 3 Theme Color Palettes (16:9 / 1200x675px)
# ---------------------------------------------------------------------------

CARD_WIDTH = 1200
CARD_HEIGHT = 675

VALID_LAYOUT_IDS: tuple[str, ...] = (
    "layout_matrix",    # 2軸マトリクス・比較型（「〇〇 vs △△」「違いの比較」向け）
    "layout_flow",      # 3ステップ・フロー型（評価手順・臨床推論プロセス向け）
    "layout_grid",      # 4象限・チェックリスト型（鑑別ポイント・4つの視点向け）
    "layout_hero",      # 1メッセージ強調・結論ファースト型（逆説フック・数値データ提示向け）
    "layout_timeline",  # タイムライン・経過型（病期別アプローチ・術後プロトコル向け）
)

LAYOUT_LABELS: dict[str, str] = {
    "layout_matrix": "2軸マトリクス・比較型",
    "layout_flow": "3ステップ・フロー型",
    "layout_grid": "4象限・チェックリスト型",
    "layout_hero": "1メッセージ強調・結論ファースト型",
    "layout_timeline": "タイムライン・経過型",
}

PALETTES: dict[str, dict[str, str]] = {
    "musculoskeletal": {
        "id": "musculoskeletal",
        "label": "運動器・バイオメカニクス（ネイビー×オレンジ）",
        "bg": "#F5F7FB",
        "surface": "#FFFFFF",
        "surface_alt": "#EEF2F7",
        "primary": "#102A4C",
        "primary_mid": "#1D4473",
        "primary_soft": "#E6EEF8",
        "accent": "#EA580C",
        "accent_dark": "#9A3412",
        "accent_soft": "#FFF3EB",
        "accent_text_on_dark": "#FDBA74",
        "border": "#CBD5E1",
        "text": "#0F172A",
        "muted": "#475569",
    },
    "pain_neuro": {
        "id": "pain_neuro",
        "label": "疼痛・神経系（ディープグリーン×イエロー）",
        "bg": "#F4F9F7",
        "surface": "#FFFFFF",
        "surface_alt": "#EBF4F0",
        "primary": "#0F3D33",
        "primary_mid": "#17594A",
        "primary_soft": "#E2F2EC",
        "accent": "#CA8A04",
        "accent_dark": "#854D0E",
        "accent_soft": "#FEF9C3",
        "accent_text_on_dark": "#FDE047",
        "border": "#C5DDD5",
        "text": "#0F172A",
        "muted": "#3F5952",
    },
    "eval_reasoning": {
        "id": "eval_reasoning",
        "label": "評価・臨床推論（スレートグレー×シアン）",
        "bg": "#F6F8FA",
        "surface": "#FFFFFF",
        "surface_alt": "#EDF2F7",
        "primary": "#1E293B",
        "primary_mid": "#334155",
        "primary_soft": "#E2E8F0",
        "accent": "#0891B2",
        "accent_dark": "#155E75",
        "accent_soft": "#E0F7FA",
        "accent_text_on_dark": "#67E8F9",
        "border": "#CBD5E1",
        "text": "#0F172A",
        "muted": "#475569",
    },
}


@dataclass
class XCardSpec:
    badge: str
    headline: str
    sub_headline: str
    items: list[tuple[str, str]]  # list of (short_label, short_phrase) - 3 to 4 concise points
    mode: str = "note_intro"      # "note_intro" | "noon_pearl"
    footer_right: str = "@benita_pt"
    layout_id: str = "layout_flow"
    palette_id: str = "eval_reasoning"
    compare_left: tuple[str, list[str]] | None = None
    compare_right: tuple[str, list[str]] | None = None
    hero_stat: str = ""
    extra_meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Text Cleaning & Shortening Helpers (30-40% tighter than note diagrams)
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://\S+")


def strip_html(text: str) -> str:
    cleaned = TAG_RE.sub("", text or "")
    cleaned = html.unescape(cleaned)
    return WS_RE.sub(" ", cleaned).strip()


def shorten_phrase(text: str, max_chars: int = 28) -> str:
    s = strip_html(text)
    s = URL_RE.sub("", s).strip()
    # Strip ONLY explicit bullet/list prefixes (never strip meaningful leading numbers like "7日間" or "30%")
    s = re.sub(r"^(?:[・●◆■①②③④⑤]+|\d+[.)．:]\s+|-\s+)", "", s).strip()
    if len(s) <= max_chars:
        return s
    # First try splitting cleanly at sentence-level boundaries
    for sep in ("。", "｜", "：", "（"):
        idx = s.find(sep)
        if 10 <= idx <= max_chars:
            return s[:idx].strip()
    # If still longer than max_chars, prefer the LAST comma before max_chars if it keeps >= 14 chars
    comma_idx = s.rfind("、", 14, max_chars + 1)
    if comma_idx >= 14:
        return s[:comma_idx].strip()
    return s[: max_chars - 1].rstrip("、。・（(") + "…"


def _split_into_short_phrases(text: str, target_count: int = 4, max_chars: int = 28) -> list[str]:
    raw = strip_html(URL_RE.sub("", text))
    # Split on newlines and sentence boundaries first
    sentences = [
        c.strip()
        for c in re.split(r"[\n。！？!?]+", raw)
        if c.strip() and len(c.strip()) >= 6
    ]
    out: list[str] = []
    for sent in sentences:
        if any(w in sent for w in ("具体策はこちら", "note記事", "👇", "http")):
            continue
        p = shorten_phrase(sent, max_chars=max_chars)
        if p and p not in out:
            out.append(p)
        if len(out) >= target_count:
            break
    return out


# ---------------------------------------------------------------------------
# Automatic Palette & Layout Selection + Consecutive Duplicate Avoidance
# ---------------------------------------------------------------------------

def detect_palette_id(text: str, domain: str = "") -> str:
    # Strip generic fallback domain label so the actual post topic drives palette selection
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
        "足関節", "伸展", "屈曲", "後傾", "前傾",
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
    """Extract the most recent `window` layout_ids across one or more history state dicts."""
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
    recent = [item[2] for item in collected[-window:]]
    return recent


def select_layout_id(
    text: str,
    *,
    avoid_layouts: list[str] | None = None,
    preferred_layout: str | None = None,
) -> str:
    """
    Score the 5 layouts for best fit with the content, then pick the highest-scoring
    layout that does NOT collide with `avoid_layouts` (last 1-2 posts).
    """
    avoid_set = {lid for lid in (avoid_layouts or []) if lid in VALID_LAYOUT_IDS}

    if preferred_layout in VALID_LAYOUT_IDS and preferred_layout not in avoid_set:
        return preferred_layout

    t = text.lower()
    scores: dict[str, int] = {lid: 0 for lid in VALID_LAYOUT_IDS}

    # layout_matrix: comparison / vs / difference
    for kw in ("vs", "違い", "別物", "比較", "ではない", "≠", "と", "使い分け", "対比", "良い", "悪い", "保存療法と術後"):
        if kw in t:
            scores["layout_matrix"] += 2

    # layout_flow: 3-step / procedure / clinical reasoning process
    for kw in ("ステップ", "step", "手順", "フロー", "順番", "プロセス", "推論", "流れ", "進め方", "評価から"):
        if kw in t:
            scores["layout_flow"] += 2

    # layout_grid: 4 perspectives / checklist / differentiation
    for kw in ("4つ", "4項目", "4局面", "4パターン", "チェック", "鑑別", "視点", "ポイント", "要因", "原因", "観察所見"):
        if kw in t:
            scores["layout_grid"] += 2

    # layout_hero: paradox / single strong conclusion / numerical metric
    for kw in ("実は", "思い込み", "決めつけ", "本当", "要注意", "なぜ", "だけ", "mcid", "mdc", "カットオフ", "秒", "%", "点", "数値", "サボる"):
        if kw in t:
            scores["layout_hero"] += 2

    # layout_timeline: stage / postoperative / protocol / time course
    for kw in ("術後", "翌日", "早期", "病期", "急性期", "回復期", "経過", "プロトコル", "段階", "時期別", "6週", "1年", "推移"):
        if kw in t:
            scores["layout_timeline"] += 2

    # Deterministic tie-break order keeps variety balanced across runs
    ranked = sorted(
        VALID_LAYOUT_IDS,
        key=lambda lid: (scores[lid], -VALID_LAYOUT_IDS.index(lid)),
        reverse=True,
    )

    for lid in ranked:
        if lid not in avoid_set:
            return lid

    # Fallback if avoid_set somehow covered all 5
    return ranked[0]


# ---------------------------------------------------------------------------
# Spec Builders (Note-Intro Cards & Noon Standalone Pearl Cards)
# ---------------------------------------------------------------------------

def build_x_card_spec_from_article(
    art: dict[str, Any],
    *,
    avoid_layouts: list[str] | None = None,
    forced_layout: str | None = None,
    forced_palette: str | None = None,
) -> XCardSpec:
    """Distill a rich run_daily_x.py ARTICLES entry into a concise 16:9 X card spec."""
    combined = " ".join(
        [
            art.get("title", ""),
            art.get("post", ""),
            art.get("badge", ""),
            art.get("header_msg", ""),
            art.get("box1_title", ""),
            art.get("box2_title", ""),
        ]
    )
    layout_id = (
        forced_layout
        if forced_layout in VALID_LAYOUT_IDS
        else select_layout_id(combined, avoid_layouts=avoid_layouts)
    )
    palette_id = (
        forced_palette
        if forced_palette in PALETTES
        else detect_palette_id(combined, domain=art.get("badge", ""))
    )

    headline = shorten_phrase(art.get("header_title") or art.get("title", ""), max_chars=32)
    sub_headline = shorten_phrase(art.get("header_msg") or art.get("mid_text", ""), max_chars=30)
    badge = shorten_phrase(art.get("badge", "理学療法士の臨床メモ").split("｜")[0], max_chars=20)

    # Extract 3-4 ultra-concise phrases (30-40% fewer words than note diagrams)
    items: list[tuple[str, str]] = []
    for idx, step in enumerate(art.get("steps", [])[:4], start=1):
        lbl = shorten_phrase(step[1] if len(step) > 1 else f"POINT {idx}", max_chars=15)
        detail = shorten_phrase(step[2] if len(step) > 2 else "", max_chars=24)
        items.append((lbl, detail))

    if len(items) < 3:
        b1 = (shorten_phrase(art.get("box1_title", "視点1"), 15), shorten_phrase(art.get("box1_tag") or art.get("box1_desc", ""), 24))
        b2 = (shorten_phrase(art.get("box2_title", "視点2"), 15), shorten_phrase(art.get("box2_tag") or art.get("box2_desc", ""), 24))
        m1 = ("臨床の核心", shorten_phrase(art.get("mid_text", ""), 24))
        items = [b1, b2, m1]

    # Ensure 4 items when grid layout is selected
    if layout_id == "layout_grid" and len(items) < 4:
        extra_phrase = shorten_phrase(art.get("mid_text") or art.get("header_msg", "個別反応で再評価"), 24)
        items.append(("再評価の軸", extra_phrase))

    # Comparison pairs for layout_matrix
    left_title = shorten_phrase(art.get("box1_title", "視点 A"), 18)
    left_pts = [
        shorten_phrase(art.get("box1_tag", ""), 22),
        shorten_phrase(art.get("box1_desc", ""), 24),
    ]
    left_pts = [p for p in left_pts if p][:2] or [shorten_phrase(items[0][1], 24)]

    right_title = shorten_phrase(art.get("box2_title", "視点 B"), 18)
    right_pts = [
        shorten_phrase(art.get("box2_tag", ""), 22),
        shorten_phrase(art.get("box2_desc", ""), 24),
    ]
    right_pts = [p for p in right_pts if p][:2] or [shorten_phrase(items[1][1], 24)]

    return XCardSpec(
        badge=badge,
        headline=headline,
        sub_headline=sub_headline,
        items=items[:4],
        mode="note_intro",
        footer_right="@benita_pt",
        layout_id=layout_id,
        palette_id=palette_id,
        compare_left=(left_title, left_pts),
        compare_right=(right_title, right_pts),
        hero_stat=sub_headline,
        extra_meta={"article_key": art.get("key", "")},
    )


def build_x_card_spec_from_noon(
    *,
    topic: str,
    post: str,
    domain: str = "運動器",
    reference: str = "",
    avoid_layouts: list[str] | None = None,
    forced_layout: str | None = None,
    forced_palette: str | None = None,
) -> XCardSpec:
    """
    Build a concise 16:9 standalone X card spec for a noon clinical pearl post.
    Strictly link-free & zero-promotion (no 'note' or URL text anywhere on the card).
    """
    combined = f"{domain} {topic} {post}"
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

    phrases = _split_into_short_phrases(post, target_count=4, max_chars=36)
    headline = shorten_phrase(topic, max_chars=30)
    sub_headline = phrases[0] if phrases else shorten_phrase(post, max_chars=32)

    default_labels = {
        "layout_flow": ["確認ポイント", "評価・見極め", "明日の一手"],
        "layout_timeline": ["初期確認", "動作評価", "介入調整", "再評価"],
        "layout_grid": ["視点 1", "視点 2", "視点 3", "視点 4"],
        "layout_hero": ["臨床の着眼", "評価のコツ", "明日の一手"],
        "layout_matrix": ["よくある見方", "臨床の本質", "実践ポイント"],
    }
    labels = default_labels.get(layout_id, ["POINT 1", "POINT 2", "POINT 3", "POINT 4"])

    items: list[tuple[str, str]] = []
    for idx, ph in enumerate(phrases[:4]):
        lbl = labels[idx] if idx < len(labels) else f"POINT {idx + 1}"
        items.append((lbl, ph))

    while len(items) < (4 if layout_id == "layout_grid" else 3):
        idx = len(items)
        fallback = shorten_phrase(topic if idx == 0 else "再現性ある条件で再評価", 24)
        items.append((labels[idx] if idx < len(labels) else f"POINT {idx + 1}", fallback))

    # Build balanced comparison sides for layout_matrix without duplicating takeaway
    if len(items) >= 4:
        left_pts = [items[0][1], items[1][1]]
        right_pts = [items[2][1], items[3][1]]
        takeaway_msg = shorten_phrase(f"{headline}を3視点で整理", 30)
    elif len(items) == 3:
        left_pts = [items[0][1]]
        right_pts = [items[1][1]]
        takeaway_msg = items[2][1]
    else:
        left_pts = [items[0][1]]
        right_pts = [items[-1][1]]
        takeaway_msg = shorten_phrase(topic, 30)

    badge = shorten_phrase(domain or "臨床ワンポイント", max_chars=18)
    # Strictly avoid 'note' / 'ノート' on noon cards
    for bad in ("note", "ノート", "記事"):
        badge = badge.replace(bad, "臨床メモ")

    ref_short = shorten_phrase(reference, max_chars=34) if reference else "明日の臨床ワンポイント"
    for bad in ("note", "ノート", "記事"):
        ref_short = ref_short.replace(bad, "")

    return XCardSpec(
        badge=f"{badge}｜臨床メモ",
        headline=headline,
        sub_headline=takeaway_msg if layout_id == "layout_matrix" else sub_headline,
        items=items[:4],
        mode="noon_pearl",
        footer_right=f"{ref_short}（@benita_pt）" if ref_short else "@benita_pt",
        layout_id=layout_id,
        palette_id=palette_id,
        compare_left=("従来の見方・注意点", left_pts[:2]),
        compare_right=("明日から変える視点", right_pts[:2]),
        hero_stat=sub_headline,
    )


# ---------------------------------------------------------------------------
# 5 Distinct 1200x675px (16:9) HTML/CSS Layout Templates
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _render_body_matrix(spec: XCardSpec, p: dict[str, str]) -> str:
    l_title, l_pts = spec.compare_left or ("従来の視点", [spec.items[0][1]])
    r_title, r_pts = spec.compare_right or ("臨床の本質", [spec.items[-1][1]])
    l_html = "".join(f'<div class="mx-item"><span class="mx-dot left"></span>{_esc(x)}</div>' for x in l_pts[:2])
    r_html = "".join(f'<div class="mx-item"><span class="mx-dot right"></span>{_esc(x)}</div>' for x in r_pts[:2])
    used = set(l_pts[:2]) | set(r_pts[:2])
    takeaway = spec.sub_headline
    if takeaway in used and len(spec.items) >= 3 and spec.items[2][1] not in used:
        takeaway = spec.items[2][1]
    return f"""
    <div class="matrix-wrap">
      <div class="mx-col left-col">
        <div class="mx-head left-head">{_esc(l_title)}</div>
        <div class="mx-list">{l_html}</div>
      </div>
      <div class="vs-badge">VS</div>
      <div class="mx-col right-col">
        <div class="mx-head right-head">{_esc(r_title)}</div>
        <div class="mx-list">{r_html}</div>
      </div>
    </div>
    <div class="mx-takeaway">
      <span class="mx-take-tag">臨床の軸</span>
      <span class="mx-take-txt">{_esc(takeaway)}</span>
    </div>
    """


def _render_body_flow(spec: XCardSpec, p: dict[str, str]) -> str:
    steps = spec.items[:3]
    cards = []
    for idx, (lbl, phrase) in enumerate(steps, start=1):
        cards.append(
            f"""
            <div class="flow-card">
              <div class="flow-num">STEP {idx}</div>
              <div class="flow-lbl">{_esc(lbl)}</div>
              <div class="flow-txt">{_esc(phrase)}</div>
            </div>
            """
        )
        if idx < len(steps):
            cards.append('<div class="flow-arrow">▶</div>')
    step_texts = {phrase for _, phrase in steps}
    banner_html = (
        f'<div class="flow-sub-banner">{_esc(spec.sub_headline)}</div>'
        if spec.sub_headline and spec.sub_headline not in step_texts
        else ""
    )
    return f"""
    {banner_html}
    <div class="flow-row">{"".join(cards)}</div>
    """


def _render_body_grid(spec: XCardSpec, p: dict[str, str]) -> str:
    items = spec.items[:4]
    while len(items) < 4:
        items.append((f"POINT {len(items) + 1}", spec.sub_headline))
    cells = []
    for idx, (lbl, phrase) in enumerate(items, start=1):
        accent_cls = " is-accent" if idx == 4 else ""
        cells.append(
            f"""
            <div class="grid-cell{accent_cls}">
              <div class="grid-head">
                <span class="grid-num">0{idx}</span>
                <span class="grid-lbl">{_esc(lbl)}</span>
              </div>
              <div class="grid-txt">{_esc(phrase)}</div>
            </div>
            """
        )
    return f'<div class="grid-2x2">{"".join(cells)}</div>'


def _render_body_hero(spec: XCardSpec, p: dict[str, str]) -> str:
    hero_msg = spec.hero_stat or spec.sub_headline
    pills = spec.items[:3]
    pill_html = "".join(
        f"""
        <div class="hero-pill">
          <div class="hero-pill-lbl">{_esc(lbl)}</div>
          <div class="hero-pill-txt">{_esc(phrase)}</div>
        </div>
        """
        for lbl, phrase in pills
    )
    return f"""
    <div class="hero-main">
      <div class="hero-kicker">CONCLUSION FIRST</div>
      <div class="hero-big">{_esc(hero_msg)}</div>
    </div>
    <div class="hero-pills">{pill_html}</div>
    """


def _render_body_timeline(spec: XCardSpec, p: dict[str, str]) -> str:
    phases = spec.items[:4]
    nodes = []
    for idx, (lbl, phrase) in enumerate(phases, start=1):
        nodes.append(
            f"""
            <div class="tl-node">
              <div class="tl-dot-wrap">
                <div class="tl-dot">{idx}</div>
              </div>
              <div class="tl-card">
                <div class="tl-lbl">{_esc(lbl)}</div>
                <div class="tl-txt">{_esc(phrase)}</div>
              </div>
            </div>
            """
        )
    cols = len(phases)
    return f"""
    <div class="tl-sub">{_esc(spec.sub_headline)}</div>
    <div class="tl-wrap" style="--tl-cols:{cols};">
      <div class="tl-line"></div>
      <div class="tl-grid">{"".join(nodes)}</div>
    </div>
    """


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

    if spec.mode == "note_intro":
        footer_left = "▼ 具体的な評価基準と臨床での使い方はnote記事で解説"
    else:
        footer_left = "明日の臨床で使えるワンポイント整理（保存推奨）"

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
    padding: 28px 38px 24px 38px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 16px;
  }}

  /* Header */
  .hdr {{
    background: linear-gradient(125deg, {p["primary"]} 0%, {p["primary_mid"]} 100%);
    color: #FFFFFF;
    border-radius: 16px;
    padding: 20px 28px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 20px;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.14);
  }}
  .hdr-left {{ flex: 1; }}
  .hdr-badge {{
    display: inline-block;
    background: {p["accent"]};
    color: #FFFFFF;
    font-size: 16px;
    font-weight: 800;
    padding: 4px 14px;
    border-radius: 999px;
    margin-bottom: 10px;
    letter-spacing: 0.04em;
  }}
  .hdr-title {{
    font-size: 32px;
    font-weight: 900;
    line-height: 1.25;
    letter-spacing: -0.01em;
  }}
  .hdr-pill {{
    background: rgba(255, 255, 255, 0.12);
    border: 2px solid {p["accent_text_on_dark"]};
    color: {p["accent_text_on_dark"]};
    border-radius: 12px;
    padding: 10px 16px;
    font-size: 16px;
    font-weight: 800;
    white-space: nowrap;
  }}

  /* Main stage */
  .stage {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 14px;
  }}

  /* Layout 1: Matrix */
  .matrix-wrap {{
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 78px 1fr;
    align-items: stretch;
    gap: 10px;
  }}
  .mx-col {{
    background: {p["surface"]};
    border: 2px solid {p["border"]};
    border-radius: 16px;
    padding: 24px 26px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 18px;
    box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
  }}
  .mx-col.left-col {{ border-top: 7px solid {p["primary"]}; }}
  .mx-col.right-col {{ border-top: 7px solid {p["accent"]}; }}
  .mx-head {{
    font-size: 26px;
    font-weight: 900;
    line-height: 1.25;
  }}
  .left-head {{ color: {p["primary"]}; }}
  .right-head {{ color: {p["accent_dark"]}; }}
  .mx-list {{ display: flex; flex-direction: column; gap: 14px; }}
  .mx-item {{
    display: flex;
    align-items: center;
    gap: 12px;
    background: {p["surface_alt"]};
    border-radius: 10px;
    padding: 14px 16px;
    font-size: 22px;
    font-weight: 700;
    line-height: 1.3;
  }}
  .mx-dot {{
    width: 12px;
    height: 12px;
    border-radius: 50%;
    flex-shrink: 0;
  }}
  .mx-dot.left {{ background: {p["primary"]}; }}
  .mx-dot.right {{ background: {p["accent"]}; }}
  .vs-badge {{
    align-self: center;
    justify-self: center;
    width: 64px;
    height: 64px;
    border-radius: 50%;
    background: {p["accent"]};
    color: #FFFFFF;
    font-size: 24px;
    font-weight: 900;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 6px 14px rgba(15, 23, 42, 0.16);
  }}
  .mx-takeaway {{
    background: {p["accent_soft"]};
    border: 2px solid {p["accent"]};
    border-radius: 12px;
    padding: 12px 20px;
    display: flex;
    align-items: center;
    gap: 14px;
  }}
  .mx-take-tag {{
    background: {p["accent"]};
    color: #FFFFFF;
    font-size: 16px;
    font-weight: 800;
    padding: 4px 12px;
    border-radius: 8px;
    white-space: nowrap;
  }}
  .mx-take-txt {{
    font-size: 22px;
    font-weight: 900;
    color: {p["accent_dark"]};
  }}

  /* Layout 2: Flow */
  .flow-sub-banner {{
    background: {p["primary_soft"]};
    border-left: 7px solid {p["accent"]};
    border-radius: 10px;
    padding: 12px 20px;
    font-size: 22px;
    font-weight: 900;
    color: {p["primary"]};
  }}
  .flow-row {{
    flex: 1;
    display: flex;
    align-items: stretch;
    gap: 14px;
  }}
  .flow-card {{
    flex: 1;
    background: {p["surface"]};
    border: 2px solid {p["border"]};
    border-top: 7px solid {p["primary"]};
    border-radius: 16px;
    padding: 22px 22px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 12px;
    box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
  }}
  .flow-num {{
    display: inline-block;
    align-self: flex-start;
    background: {p["accent"]};
    color: #FFFFFF;
    font-size: 17px;
    font-weight: 900;
    padding: 4px 14px;
    border-radius: 8px;
  }}
  .flow-lbl {{
    font-size: 26px;
    font-weight: 900;
    color: {p["primary"]};
    line-height: 1.25;
  }}
  .flow-txt {{
    font-size: 21px;
    font-weight: 700;
    color: {p["muted"]};
    line-height: 1.4;
  }}
  .flow-arrow {{
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 32px;
    font-weight: 900;
    color: {p["accent"]};
  }}

  /* Layout 3: 4-Quadrant Grid */
  .grid-2x2 {{
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: 1fr 1fr;
    gap: 16px;
  }}
  .grid-cell {{
    background: {p["surface"]};
    border: 2px solid {p["border"]};
    border-left: 8px solid {p["primary"]};
    border-radius: 14px;
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 10px;
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05);
  }}
  .grid-cell.is-accent {{
    border-left-color: {p["accent"]};
    background: {p["accent_soft"]};
  }}
  .grid-head {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .grid-num {{
    background: {p["primary"]};
    color: #FFFFFF;
    font-size: 18px;
    font-weight: 900;
    padding: 4px 11px;
    border-radius: 8px;
  }}
  .grid-cell.is-accent .grid-num {{
    background: {p["accent"]};
  }}
  .grid-lbl {{
    font-size: 25px;
    font-weight: 900;
    color: {p["primary"]};
  }}
  .grid-txt {{
    font-size: 21px;
    font-weight: 700;
    color: {p["text"]};
    line-height: 1.35;
  }}

  /* Layout 4: Hero Conclusion First */
  .hero-main {{
    flex: 1.2;
    background: {p["surface"]};
    border: 3px solid {p["accent"]};
    border-radius: 18px;
    padding: 28px 34px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    gap: 12px;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
  }}
  .hero-kicker {{
    background: {p["accent_soft"]};
    color: {p["accent_dark"]};
    font-size: 16px;
    font-weight: 900;
    letter-spacing: 0.1em;
    padding: 5px 16px;
    border-radius: 999px;
  }}
  .hero-big {{
    font-size: 38px;
    font-weight: 900;
    color: {p["primary"]};
    line-height: 1.3;
  }}
  .hero-pills {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
  }}
  .hero-pill {{
    background: {p["surface"]};
    border: 2px solid {p["border"]};
    border-top: 6px solid {p["primary"]};
    border-radius: 14px;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}
  .hero-pill-lbl {{
    font-size: 18px;
    font-weight: 900;
    color: {p["accent_dark"]};
  }}
  .hero-pill-txt {{
    font-size: 20px;
    font-weight: 700;
    color: {p["text"]};
    line-height: 1.35;
  }}

  /* Layout 5: Timeline */
  .tl-sub {{
    background: {p["accent_soft"]};
    border: 2px solid {p["accent"]};
    border-radius: 10px;
    padding: 10px 18px;
    font-size: 21px;
    font-weight: 900;
    color: {p["accent_dark"]};
    text-align: center;
  }}
  .tl-wrap {{
    flex: 1;
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }}
  .tl-line {{
    position: absolute;
    top: 34px;
    left: 60px;
    right: 60px;
    height: 6px;
    background: {p["primary"]};
    border-radius: 3px;
    z-index: 1;
  }}
  .tl-grid {{
    position: relative;
    z-index: 2;
    display: grid;
    grid-template-columns: repeat(var(--tl-cols, 3), 1fr);
    gap: 18px;
    height: 100%;
  }}
  .tl-node {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }}
  .tl-dot-wrap {{
    padding-top: 8px;
  }}
  .tl-dot {{
    width: 54px;
    height: 54px;
    border-radius: 50%;
    background: {p["accent"]};
    border: 5px solid {p["bg"]};
    color: #FFFFFF;
    font-size: 24px;
    font-weight: 900;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 4px 10px rgba(15, 23, 42, 0.16);
  }}
  .tl-card {{
    flex: 1;
    width: 100%;
    background: {p["surface"]};
    border: 2px solid {p["border"]};
    border-top: 6px solid {p["primary"]};
    border-radius: 14px;
    padding: 18px 18px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 10px;
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05);
  }}
  .tl-lbl {{
    font-size: 23px;
    font-weight: 900;
    color: {p["primary"]};
  }}
  .tl-txt {{
    font-size: 20px;
    font-weight: 700;
    color: {p["muted"]};
    line-height: 1.38;
  }}

  /* Footer */
  .ftr {{
    background: {p["primary"]};
    color: #FFFFFF;
    border-radius: 12px;
    padding: 12px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }}
  .ftr-left {{
    font-size: 19px;
    font-weight: 900;
    color: {p["accent_text_on_dark"]};
  }}
  .ftr-right {{
    font-size: 16px;
    font-weight: 700;
    color: #E2E8F0;
  }}
</style>
</head>
<body>
  <div class="canvas">
    <div class="hdr">
      <div class="hdr-left">
        <div class="hdr-badge">{_esc(spec.badge)}</div>
        <div class="hdr-title">{_esc(spec.headline)}</div>
      </div>
      <div class="hdr-pill">{_esc(LAYOUT_LABELS[layout_id])}</div>
    </div>

    <div class="stage">
      {body_html}
    </div>

    <div class="ftr">
      <div class="ftr-left">{_esc(footer_left)}</div>
      <div class="ftr-right">{_esc(spec.footer_right)}</div>
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


def generate_x_card_files(spec: XCardSpec, html_path: Path, png_path: Path, *, render: bool = True) -> dict[str, str]:
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
    parser = argparse.ArgumentParser(description="Render a test/demo 16:9 X diagram card.")
    parser.add_argument("--layout", choices=VALID_LAYOUT_IDS, default=None, help="Force a specific layout_id")
    parser.add_argument("--palette", choices=tuple(PALETTES.keys()), default=None, help="Force a specific palette_id")
    parser.add_argument("--mode", choices=("note_intro", "noon_pearl"), default="noon_pearl")
    parser.add_argument("--topic", default="LSS歩行評価：『歩ける距離』と『日常歩数』のズレ")
    parser.add_argument(
        "--post",
        default=(
            "リハ室で500m歩けても、自宅の歩数が伸びないケースは珍しくありません。\n"
            "まずはSPWTの測定条件をそろえ、歩行直後の脚痛と回復時間を確認しましょう。\n"
            "7日間の歩数ログから生活障壁を切り分けるのが、次の一手を決める鍵です。"
        ),
    )
    parser.add_argument("--domain", default="疼痛・神経系")
    parser.add_argument("--out-html", default="x_card_test.html")
    parser.add_argument("--out-png", default="x_card_test.png")
    args = parser.parse_args()

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
