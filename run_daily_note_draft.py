#!/usr/bin/env python3
"""
Daily Note Draft Generator (PubMed Primary Literature -> Article + 2 Diagrams -> drafts/ + Gmail + [済] Push)

Designed for unattended execution in GitHub Actions every day at 19:30 JST (10:30 UTC),
and also supports local/VM execution and dry-run testing.

Required environment variables (GitHub Actions Secrets):
  - GEMINI_API_KEY (recommended) or OPENAI_API_KEY
  - GMAIL_APP_PASSWORD (for SMTP SSL delivery to RECIPIENT_EMAIL)

Optional environment variables:
  - RECIPIENT_EMAIL (default: itoshota2006@gmail.com)
  - GEMINI_MODEL (default: gemini-2.5-flash)
  - OPENAI_MODEL (default: gpt-4o)
  - NCBI_API_KEY (optional PubMed API key for higher rate limits)
  - GITHUB_REPOSITORY (default: itoshota2006/note-x-automation)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import smtplib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent
THEMES_FILE = BASE_DIR / "note-themes.txt"
RULES_FILE = BASE_DIR / "rules.md"
PAST_ARTICLES_FILE = BASE_DIR / "past_articles.md"
DRAFTS_DIR = BASE_DIR / "drafts"

RECIPIENT = os.environ.get("RECIPIENT_EMAIL", "itoshota2006@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "itoshota2006/note-x-automation").strip()

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
USER_AGENT = "note-x-automation/1.0 (mailto:itoshota2006@gmail.com)"

DONE_PREFIX_RE = re.compile(r"^\s*[\[［]済[\]］]")


# ---------------------------------------------------------------------------
# Theme queue helpers
# ---------------------------------------------------------------------------

def pick_next_theme(themes_path: Path) -> tuple[int, str] | None:
    """Return (0-based line index, stripped theme) for the topmost unprocessed line."""
    if not themes_path.exists():
        return None
    lines = themes_path.read_text(encoding="utf-8").splitlines()
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        if DONE_PREFIX_RE.match(stripped):
            continue
        return idx, stripped
    return None


def mark_theme_done(themes_path: Path, line_index: int) -> None:
    """Prefix the target line in note-themes.txt with '[済] '."""
    lines = themes_path.read_text(encoding="utf-8").splitlines()
    if line_index < 0 or line_index >= len(lines):
        raise IndexError(f"line_index {line_index} out of range for {themes_path}")
    current = lines[line_index].strip()
    if not DONE_PREFIX_RE.match(current):
        lines[line_index] = f"[済] {current}"
    themes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sanitize_slug(raw_slug: str, fallback_date: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (raw_slug or "").lower()).strip("-")
    slug = slug[:48].strip("-")
    return slug or f"note-draft-{fallback_date}"


# ---------------------------------------------------------------------------
# HTTP & LLM helpers (zero third-party dependencies)
# ---------------------------------------------------------------------------

def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 90,
    retries: int = 3,
) -> bytes:
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            last_err = RuntimeError(f"HTTP {e.code} for {url}: {err_body}")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait_sec = 2 ** attempt
                print(f"[warn] HTTP {e.code}, retrying in {wait_sec}s (attempt {attempt}/{retries})...")
                time.sleep(wait_sec)
                continue
            raise last_err from e
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait_sec = 2 ** attempt
                print(f"[warn] Request error ({e}), retrying in {wait_sec}s (attempt {attempt}/{retries})...")
                time.sleep(wait_sec)
                continue
            raise
    assert last_err is not None
    raise last_err


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        obj = json.loads(cleaned[start : end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"Failed to parse JSON object from LLM response: {cleaned[:400]}")


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.25,
    max_output_tokens: int = 16384,
) -> dict[str, Any]:
    """Call Gemini (primary) or OpenAI (fallback) and return a parsed JSON dict."""
    if GEMINI_API_KEY:
        model = GEMINI_MODEL
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urllib.parse.quote(model, safe='')}:generateContent"
            f"?key={urllib.parse.quote(GEMINI_API_KEY, safe='')}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        raw = http_request(
            url,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=180,
            retries=3,
        )
        resp = json.loads(raw.decode("utf-8"))
        candidates = resp.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {resp}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text.strip():
            raise RuntimeError(f"Gemini returned empty text: {resp}")
        return _extract_json_object(text)

    if OPENAI_API_KEY:
        url = "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": OPENAI_MODEL,
            "temperature": temperature,
            "max_tokens": min(max_output_tokens, 16384),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        raw = http_request(
            url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=180,
            retries=3,
        )
        resp = json.loads(raw.decode("utf-8"))
        text = resp["choices"][0]["message"]["content"]
        return _extract_json_object(text)

    raise RuntimeError(
        "LLM APIキーが設定されていません。GitHub Secrets に GEMINI_API_KEY（推奨）"
        "または OPENAI_API_KEY を登録してください。"
    )


# ---------------------------------------------------------------------------
# PubMed E-utilities primary literature search
# ---------------------------------------------------------------------------

def _pubmed_sleep() -> None:
    time.sleep(0.15 if NCBI_API_KEY else 0.4)


def pubmed_esearch(query: str, retmax: int = 8) -> list[str]:
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": str(retmax),
        "sort": "relevance",
        "tool": "note-x-automation",
        "email": RECIPIENT,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    url = f"{PUBMED_BASE}/esearch.fcgi?{urllib.parse.urlencode(params)}"
    raw = http_request(url, timeout=30, retries=3)
    _pubmed_sleep()
    data = json.loads(raw.decode("utf-8"))
    return list(data.get("esearchresult", {}).get("idlist", []))


def parse_pubmed_xml(xml_bytes: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    articles: list[dict[str, Any]] = []

    for art_el in root.findall(".//PubmedArticle"):
        medline = art_el.find("MedlineCitation")
        if medline is None:
            continue
        pmid_el = medline.find("PMID")
        pmid = (pmid_el.text or "").strip() if pmid_el is not None else ""
        if not pmid:
            continue

        article = medline.find("Article")
        if article is None:
            continue

        title_el = article.find("ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else ""

        # Abstract (supports structured abstracts with Label attributes)
        abs_parts: list[str] = []
        for ab in article.findall(".//Abstract/AbstractText"):
            txt = "".join(ab.itertext()).strip()
            if not txt:
                continue
            label = (ab.attrib.get("Label") or "").strip()
            if label:
                abs_parts.append(f"{label}: {txt}")
            else:
                abs_parts.append(txt)
        abstract = "\n".join(abs_parts).strip()

        # Authors
        authors: list[str] = []
        for au in article.findall(".//AuthorList/Author"):
            last = (au.findtext("LastName") or "").strip()
            init = (au.findtext("Initials") or "").strip()
            coll = (au.findtext("CollectiveName") or "").strip()
            if last:
                authors.append(f"{last} {init}".strip())
            elif coll:
                authors.append(coll)

        # Journal & Year
        journal = (
            article.findtext(".//Journal/ISOAbbreviation")
            or article.findtext(".//Journal/Title")
            or medline.findtext(".//MedlineJournalInfo/MedlineTA")
            or ""
        ).strip()
        year = (
            article.findtext(".//Journal/JournalIssue/PubDate/Year")
            or article.findtext(".//ArticleDate/Year")
            or ""
        ).strip()
        if not year:
            medline_date = (article.findtext(".//Journal/JournalIssue/PubDate/MedlineDate") or "").strip()
            m_year = re.search(r"\b(19|20)\d{2}\b", medline_date)
            if m_year:
                year = m_year.group(0)

        volume = (article.findtext(".//Journal/JournalIssue/Volume") or "").strip()
        issue = (article.findtext(".//Journal/JournalIssue/Issue") or "").strip()
        pages = (article.findtext(".//Pagination/MedlinePgn") or "").strip()

        # DOI
        doi = ""
        for aid in art_el.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if aid.attrib.get("IdType") == "doi" and aid.text:
                doi = aid.text.strip()
                break

        # Publication types
        pub_types = [
            (pt.text or "").strip()
            for pt in article.findall(".//PublicationTypeList/PublicationType")
            if (pt.text or "").strip()
        ]

        # Short author citation
        if len(authors) > 3:
            author_citation = ", ".join(authors[:3]) + ", et al."
        elif authors:
            author_citation = ", ".join(authors)
        else:
            author_citation = "Unknown Authors"

        vol_str = volume
        if volume and issue:
            vol_str = f"{volume}({issue})"
        citation_parts = [p for p in [f"{year}" if year else "", vol_str, pages] if p]
        citation_tail = ";".join(citation_parts[:2]) + (f":{citation_parts[2]}" if len(citation_parts) == 3 else "")

        articles.append(
            {
                "pmid": pmid,
                "doi": doi,
                "title": title,
                "authors": authors,
                "author_citation": author_citation,
                "journal": journal,
                "year": year,
                "volume": volume,
                "issue": issue,
                "pages": pages,
                "citation_tail": citation_tail,
                "pub_types": pub_types,
                "abstract": abstract,
                "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "doi_url": f"https://doi.org/{doi}" if doi else "",
            }
        )
    return articles


def pubmed_efetch(pmids: list[str]) -> list[dict[str, Any]]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": "note-x-automation",
        "email": RECIPIENT,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    url = f"{PUBMED_BASE}/efetch.fcgi?{urllib.parse.urlencode(params)}"
    raw = http_request(url, timeout=45, retries=3)
    _pubmed_sleep()
    return parse_pubmed_xml(raw)


def _evidence_priority_score(article: dict[str, Any]) -> tuple[int, int]:
    """Higher tuple = higher priority (guidelines/SR/meta/RCT first, then newer year)."""
    pt_lower = " | ".join(article.get("pub_types", [])).lower()
    score = 0
    if "practice guideline" in pt_lower or "guideline" in pt_lower:
        score += 50
    if "systematic review" in pt_lower or "meta-analysis" in pt_lower:
        score += 40
    if "randomized controlled trial" in pt_lower:
        score += 35
    if "clinical trial" in pt_lower or "comparative study" in pt_lower:
        score += 20
    if "review" in pt_lower:
        score += 10
    if len(article.get("abstract", "")) >= 400:
        score += 5
    try:
        yr = int(article.get("year") or 0)
    except ValueError:
        yr = 0
    return (score, yr)


def plan_search_queries(theme: str) -> dict[str, Any]:
    system_prompt = (
        "You are a senior clinical physical therapy researcher and medical librarian. "
        "Given a Japanese physical therapy article theme, produce targeted English PubMed search queries "
        "that reliably return high-quality primary literature (Clinical Practice Guidelines, Systematic Reviews, "
        "Meta-Analyses, Randomized Controlled Trials, and Biomechanical/Clinical Assessment studies). "
        "Return strict JSON only."
    )
    user_prompt = f"""テーマ: {theme}

以下のJSON形式で返してください:
{{
  "slug": "short-english-kebab-case-slug-max-40-chars",
  "english_topic": "Concise English clinical topic",
  "queries": [
    "PubMed query 1 focusing on guidelines / systematic reviews / RCTs (avoid overly narrow quotes)",
    "PubMed query 2 focusing on assessment / biomechanics / evaluation",
    "PubMed query 3 focusing on rehabilitation / physical therapy intervention"
  ],
  "broad_fallback_query": "Simple 3-4 keyword broad PubMed query without publication type filters"
}}

注意:
- PubMed検索で0件にならないよう、AND条件を詰め込みすぎず主要なMeSH/英語キーワードを2〜4語程度に絞ること。
- 必ず英語で作成すること。
"""
    return call_llm_json(system_prompt, user_prompt, temperature=0.1, max_output_tokens=1024)


def search_primary_literature(theme: str) -> tuple[str, list[str], list[dict[str, Any]]]:
    plan = plan_search_queries(theme)
    slug = str(plan.get("slug") or "")
    queries = [q.strip() for q in (plan.get("queries") or []) if isinstance(q, str) and q.strip()]
    fallback_q = str(plan.get("broad_fallback_query") or "").strip()

    seen_pmids: list[str] = []
    used_queries: list[str] = []

    for q in queries[:3]:
        used_queries.append(q)
        try:
            ids = pubmed_esearch(q, retmax=8)
            print(f"[pubmed] query='{q}' -> {len(ids)} hits")
            for pid in ids:
                if pid not in seen_pmids:
                    seen_pmids.append(pid)
        except Exception as e:
            print(f"[warn] PubMed esearch failed for '{q}': {e}")

    if len(seen_pmids) < 6 and fallback_q:
        used_queries.append(fallback_q)
        try:
            ids = pubmed_esearch(fallback_q, retmax=10)
            print(f"[pubmed] fallback query='{fallback_q}' -> {len(ids)} hits")
            for pid in ids:
                if pid not in seen_pmids:
                    seen_pmids.append(pid)
        except Exception as e:
            print(f"[warn] PubMed fallback esearch failed: {e}")

    if not seen_pmids:
        raise RuntimeError(f"PubMed検索で一次文献を取得できませんでした。queries={used_queries}")

    fetched = pubmed_efetch(seen_pmids[:18])
    with_abstract = [a for a in fetched if a.get("abstract")]
    candidates = with_abstract if len(with_abstract) >= 3 else fetched
    candidates.sort(key=_evidence_priority_score, reverse=True)
    selected = candidates[:8]

    for idx, art in enumerate(selected, start=1):
        art["ref_num"] = idx

    print(f"[pubmed] selected {len(selected)} primary sources with abstracts")
    return slug, used_queries, selected


# ---------------------------------------------------------------------------
# Editorial synthesis & article generation
# ---------------------------------------------------------------------------

def summarize_past_articles(past_path: Path) -> str:
    """Extract a compact summary of existing titles and headings to prevent duplication."""
    if not past_path.exists():
        return "(過去記事なし)"
    lines = past_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("| ") or s.startswith("### ") or s.startswith("- **独自の切り口:**"):
            out.append(s)
    text = "\n".join(out)
    return text[:10000]


def format_sources_for_prompt(sources: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for s in sources:
        pt = ", ".join(s.get("pub_types") or []) or "Journal Article"
        blocks.append(
            f"[{s['ref_num']}] {s['author_citation']} ({s['year']}). {s['title']}\n"
            f"Journal: {s['journal']} {s['citation_tail']}\n"
            f"Publication Types: {pt}\n"
            f"PMID: {s['pmid']} | URL: {s['pubmed_url']}"
            + (f" | DOI: {s['doi']}" if s.get("doi") else "")
            + "\n"
            f"Abstract (抄録のみ確認):\n{s['abstract'] or '(抄録なし)'}\n"
        )
    return "\n---\n".join(blocks)


def format_sources_markdown(theme: str, queries: list[str], sources: list[dict[str, Any]], today_str: str) -> str:
    lines = [
        f"# 一次文献レポート（PubMed E-utilities 検証済み）",
        "",
        f"- **作成日:** {today_str} (JST)",
        f"- **テーマ:** {theme}",
        f"- **取得件数:** {len(sources)}件（すべてPubMed実在PMIDに基づく抄録データ）",
        "- **使用した検索クエリ:**",
    ]
    for q in queries:
        lines.append(f"  - `{q}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    for s in sources:
        pt = ", ".join(s.get("pub_types") or []) or "Journal Article"
        lines.append(f"## [{s['ref_num']}] {s['title']}")
        lines.append(f"- **著者:** {s['author_citation']}")
        lines.append(f"- **雑誌 / 年:** *{s['journal']}* ({s['year']}) {s['citation_tail']}")
        lines.append(f"- **研究デザイン / 種別:** {pt}")
        lines.append(f"- **PMID:** [{s['pmid']}]({s['pubmed_url']})")
        if s.get("doi"):
            lines.append(f"- **DOI:** [{s['doi']}]({s['doi_url']})")
        lines.append("")
        lines.append("### Abstract")
        lines.append(s["abstract"] or "（抄録なし）")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def build_verified_references_markdown(sources: list[dict[str, Any]], article_md: str) -> str:
    """Build a verified reference list strictly from fetched PubMed metadata."""
    cited_nums = {int(m) for m in re.findall(r"\[(\d+)\]", article_md)}
    used = [s for s in sources if s["ref_num"] in cited_nums]
    if not used:
        used = sources

    lines = ["## 参考文献（PubMed検証済み一次文献）", ""]
    for s in used:
        doi_part = f" doi:{s['doi']}" if s.get("doi") else ""
        lines.append(
            f"{s['ref_num']}. {s['author_citation']} {s['title']} "
            f"*{s['journal']}*. {s['year']};{s['citation_tail']}.{doi_part} "
            f"PMID: [{s['pmid']}]({s['pubmed_url']})"
        )
    lines.append("")
    lines.append(
        "> ※ 本記事で引用した文献は PubMed 収録の抄録（Abstract）および書誌情報に基づき整理しています。"
        "フルテキスト未確認の詳細プロトコル・未記載数値については断定を避け、原著確認を推奨します。"
    )
    return "\n".join(lines)


def generate_article_and_diagrams(
    theme: str,
    sources: list[dict[str, Any]],
    rules_text: str,
    past_summary: str,
) -> dict[str, Any]:
    system_prompt = (
        "あなたは理学療法・運動学・臨床推論・文献の批判的吟味に精通した医療系note編集長兼校閲者です。"
        "提供されたPubMed一次文献データと編集ルール（rules.md）を厳格に遵守し、新人（1〜2年目）〜中堅（3〜7年目）"
        "の理学療法士が明日の臨床でそのまま使える高品質なnote下書き記事と2枚の図解構造データをJSON形式で出力してください。\n"
        "【絶対遵守の医学的誠実性ルール】\n"
        "1. 根拠は提供されたPubMed一次文献データ [1]〜[N] のみ。存在しない論文・著者・DOI・PMID・数値の捏造は絶対禁止。\n"
        "2. 提供されたのは抄録（Abstract）であるため、抄録に明記されていない数値・カットオフ・介入プロトコル細部は断定せず「抄録の範囲では未確認」「原著での確認が必要」と誠実に明記すること。\n"
        "3. 相関と因果、統計学的有意差と臨床的意義（MDC/MCID）を明確に区別すること。\n"
        "4. 過去記事（past_articles.md）とタイトル・構成・切り口・図解が重複しないようゼロベースで設計すること。\n"
        "5. 丁寧な「です・ます」調で先輩PTが後輩に誠実に解説するトーン。専門用語は初出時に補足し、1段落は3〜4行以内にする。\n"
        "6. 必ず有効なJSONオブジェクトのみを出力すること。"
    )

    sources_block = format_sources_for_prompt(sources)

    user_prompt = f"""## 今回の執筆テーマ
{theme}

## 編集ルール（rules.md抜粋）
{rules_text[:4500]}

## 過去記事サマリー（重複防止用）
{past_summary}

## 検証済み一次文献データ（PubMed実取得・これ以外の文献引用は禁止）
{sources_block}

## 出力してほしいJSONスキーマ（すべてのキーを必須で出力）
{{
  "article_title": "新人〜中堅PTが思わず読みたくなる具体的で重複しない記事タイトル",
  "article_type": "有料構成",
  "unique_angle": "過去記事との差別化ポイント・今回の独自の切り口（2〜3文）",
  "three_point_summary": {{
    "clinical_reasoning": "① 臨床推論の要点（3〜4行）",
    "assessment": "② 評価の要点（3〜4行）",
    "approach": "③ アプローチの要点（3〜4行）"
  }},
  "editorial_analysis": "【第1部：編集長分析】今回のターゲット、読者の悩み、中心となる学習ポイント1〜3個、ゼロベース構成設計の意図",
  "medical_review_check": "【第2部：医学的校閲チェック】使用した一次文献番号・抄録のみ確認による限界・未確認事項の扱い・相関と因果の区別チェック結果",
  "x_post_teaser": "140字以内のX告知ポスト案。URLプレースホルダー（https://note.com/benita_pt）の直前に『明日の臨床で迷わないための具体策はこちら👇』のような行動誘導文を1行入れること。",
  "article_markdown": "note.comへそのまま貼れる完成稿Markdown。以下の構成要件をすべて満たすこと:\\n- 先頭に `# 記事タイトル`\\n- 導入文（3〜4行/段落で3段落程度）\\n- 導入文直後に `[toc]`\\n- テーマに最適化した見出し（`##`, `###`）で基礎〜評価・臨床推論を展開（引用箇所に `[1]`, `[2]` を明記）\\n- 本文中の適切な位置に `> **[図解1 挿入位置: diagram_1.png]**` と `> **[図解2 挿入位置: diagram_2.png]**` を配置\\n- 無料エリアの末尾に `---【ここから先は有料エリア】---` を配置\\n- 有料エリアに臨床推論の深掘り、鑑別、負荷設定・再評価と方針修正、仮想症例（『仮想症例』と明記）または臨床チェックリストを詳述\\n- 最後にまとめ（参考文献セクションはスクリプト側で検証済みPubMed書誌から自動付与するため、本文末尾に重複作成してもよいが検証済み番号 [1]〜[{len(sources)}] 以外は絶対に使わないこと）\\n- 全体の文字数は4,000〜6,500文字程度で読み応えと実践性を両立すること",
  "diagram_1": {{
    "badge": "CLINICAL ASSESSMENT & COMPARISON（英語大文字・短く）",
    "title": "図解1のタイトル（全角28文字以内・1行で収まる長さ）",
    "subtitle": "右上の補足（2行以内・各行26文字以内、改行は\\n）",
    "columns": ["列1見出し", "列2見出し", "列3見出し", "列4見出し"],
    "rows": [
      {{
        "col1_title": "項目名（12文字以内）",
        "col1_tag": "短いタグ（12文字以内）",
        "col2": "内容（簡潔に2〜3行、改行は\\n）",
        "col3": "内容（簡潔に2〜3行、改行は\\n）",
        "col4": "臨床での着眼点・注意点（簡潔に2〜3行、改行は\\n）"
      }}
    ],
    "caution_title": "臨床推論の要点：",
    "caution_text": "1〜2文で核心をまとめる（65文字以内）",
    "ref_note": "根拠：引用番号と主著者年（例：[1][2] PubMed抄録ベース）"
  }},
  "diagram_2": {{
    "badge": "CLINICAL REASONING FLOWCHART（英語大文字・短く）",
    "title": "図解2のタイトル（全角28文字以内・1行で収まる長さ）",
    "subtitle": "右上の補足（2行以内・各行26文字以内、改行は\\n）",
    "steps": [
      {{
        "num": "01",
        "heading": "ステップ見出し（28文字以内）",
        "tag": "短いタグ（10文字以内）",
        "bullets": ["箇条書き1（42文字以内）", "箇条書き2（42文字以内）"],
        "highlight": false
      }},
      {{
        "num": "02",
        "heading": "...",
        "tag": "...",
        "bullets": ["...", "..."],
        "highlight": false
      }},
      {{
        "num": "03",
        "heading": "...",
        "tag": "...",
        "bullets": ["...", "..."],
        "highlight": true
      }},
      {{
        "num": "04",
        "heading": "...",
        "tag": "...",
        "bullets": ["...", "..."],
        "highlight": false
      }}
    ],
    "footer_left_title": "再評価・方針修正の軸",
    "footer_left_text": "簡潔な補足（55文字以内）",
    "footer_right_title": "リスク管理・連携の視点",
    "footer_right_text": "簡潔な補足（55文字以内）"
  }}
}}

注意:
- `diagram_1.rows` は **3〜4行** にして1200×800px内に美しく収めること。
- `diagram_2.steps` は **ちょうど4ステップ** にして1200×800px内に美しく収めること。
- `diagram_1` や `diagram_2` 内のテキストにはHTMLタグを入れずプレーンテキストで書くこと。
"""
    return call_llm_json(system_prompt, user_prompt, temperature=0.25, max_output_tokens=16384)


# ---------------------------------------------------------------------------
# Diagram HTML builders & Headless Chrome PNG renderer
# ---------------------------------------------------------------------------

def _esc_br(text: str) -> str:
    return html.escape(str(text or "")).replace("\n", "<br>")


def build_table_diagram_html(d: dict[str, Any]) -> str:
    badge = html.escape(str(d.get("badge") or "CLINICAL ASSESSMENT GUIDE"))
    title = html.escape(str(d.get("title") or "臨床評価・推論の整理表"))
    subtitle = _esc_br(str(d.get("subtitle") or ""))
    cols = list(d.get("columns") or ["評価・視点", "主な観察・測定内容", "臨床での解釈", "限界と追加評価"])
    while len(cols) < 4:
        cols.append("")
    cols = cols[:4]

    rows = list(d.get("rows") or [])[:4]
    row_html_list: list[str] = []
    for r in rows:
        c1_title = html.escape(str(r.get("col1_title") or ""))
        c1_tag = html.escape(str(r.get("col1_tag") or ""))
        c2 = _esc_br(str(r.get("col2") or ""))
        c3 = _esc_br(str(r.get("col3") or ""))
        c4 = _esc_br(str(r.get("col4") or ""))
        tag_html = f'<span class="scale-tag">{c1_tag}</span>' if c1_tag else ""
        row_html_list.append(
            f"""        <tr>
          <td>
            <span class="scale-name">{c1_title}</span>
            {tag_html}
          </td>
          <td>{c2}</td>
          <td>{c3}</td>
          <td>{c4}</td>
        </tr>"""
        )

    caution_title = html.escape(str(d.get("caution_title") or "臨床推論のポイント："))
    caution_text = _esc_br(str(d.get("caution_text") or ""))
    ref_note = html.escape(str(d.get("ref_note") or ""))

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<style>
  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}
  html, body {{
    width: 1200px;
    height: 800px;
    overflow: hidden;
    background: #ffffff;
    font-family: "Noto Sans CJK JP", "Noto Sans JP", "Hiragino Sans", sans-serif;
    color: #1a2a3a;
  }}
  body {{
    padding: 30px 38px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}
  .header {{
    background: linear-gradient(135deg, #0f2942 0%, #1e4976 100%);
    color: #ffffff;
    padding: 18px 26px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    box-shadow: 0 4px 12px rgba(15, 41, 66, 0.12);
  }}
  .header-left {{
    display: flex;
    flex-direction: column;
    gap: 5px;
    min-width: 0;
  }}
  .badge {{
    display: inline-block;
    background: rgba(255, 255, 255, 0.18);
    color: #e2f0ff;
    font-size: 11.5px;
    font-weight: 700;
    padding: 3px 11px;
    border-radius: 999px;
    letter-spacing: 0.06em;
    width: fit-content;
  }}
  .title {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.01em;
    line-height: 1.3;
  }}
  .subtitle {{
    font-size: 12.5px;
    color: #dbeafe;
    text-align: right;
    line-height: 1.48;
    flex-shrink: 0;
    max-width: 340px;
  }}
  .table-wrap {{
    border: 2px solid #cbd5e1;
    border-radius: 12px;
    overflow: hidden;
    margin: 12px 0;
    flex: 1;
    display: flex;
    flex-direction: column;
  }}
  table {{
    width: 100%;
    height: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }}
  thead th {{
    background: #1e3a5f;
    color: #ffffff;
    font-size: 14.5px;
    font-weight: 700;
    padding: 12px 15px;
    text-align: left;
    border-right: 1px solid rgba(255, 255, 255, 0.15);
  }}
  thead th:last-child {{
    border-right: none;
  }}
  tbody tr {{
    border-bottom: 1px solid #cbd5e1;
  }}
  tbody tr:nth-child(even) {{
    background: #f8fafc;
  }}
  tbody tr:last-child {{
    border-bottom: none;
  }}
  tbody td {{
    padding: 12px 15px;
    vertical-align: middle;
    font-size: 13.4px;
    line-height: 1.52;
    border-right: 1px solid #e2e8f0;
    word-break: break-word;
  }}
  tbody td:last-child {{
    border-right: none;
  }}
  .scale-name {{
    font-size: 15.5px;
    font-weight: 700;
    color: #0f2942;
    display: block;
    margin-bottom: 5px;
    line-height: 1.35;
  }}
  .scale-tag {{
    display: inline-block;
    background: #e0effe;
    color: #1e4976;
    font-size: 11.5px;
    font-weight: 700;
    padding: 2px 9px;
    border-radius: 6px;
  }}
  .caution-box {{
    background: #f0f6ff;
    border-left: 5px solid #1e4976;
    padding: 12px 18px;
    border-radius: 8px;
    font-size: 13.2px;
    color: #1e293b;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }}
  .caution-main {{
    line-height: 1.48;
  }}
  .caution-title {{
    font-weight: 700;
    color: #0f2942;
    margin-right: 6px;
  }}
  .ref-note {{
    font-size: 11.5px;
    color: #64748b;
    white-space: nowrap;
    flex-shrink: 0;
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="header-left">
      <span class="badge">{badge}</span>
      <div class="title">{title}</div>
    </div>
    <div class="subtitle">{subtitle}</div>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th style="width: 21%;">{html.escape(cols[0])}</th>
          <th style="width: 25%;">{html.escape(cols[1])}</th>
          <th style="width: 25%;">{html.escape(cols[2])}</th>
          <th style="width: 29%;">{html.escape(cols[3])}</th>
        </tr>
      </thead>
      <tbody>
{chr(10).join(row_html_list)}
      </tbody>
    </table>
  </div>

  <div class="caution-box">
    <div class="caution-main">
      <span class="caution-title">{caution_title}</span>{caution_text}
    </div>
    <div class="ref-note">{ref_note}</div>
  </div>
</body>
</html>
"""


def build_flow_diagram_html(d: dict[str, Any]) -> str:
    badge = html.escape(str(d.get("badge") or "CLINICAL REASONING FLOWCHART"))
    title = html.escape(str(d.get("title") or "臨床推論とアプローチの4ステップ"))
    subtitle = _esc_br(str(d.get("subtitle") or ""))
    steps = list(d.get("steps") or [])[:4]

    step_blocks: list[str] = []
    for idx, st in enumerate(steps):
        num = html.escape(str(st.get("num") or f"{idx + 1:02d}"))
        heading = html.escape(str(st.get("heading") or ""))
        tag = html.escape(str(st.get("tag") or ""))
        bullets = [b for b in (st.get("bullets") or []) if isinstance(b, str) and b.strip()][:2]
        urgent_cls = " urgent" if st.get("highlight") else ""
        tag_html = f'<span class="step-tag">{tag}</span>' if tag else ""
        desc_html = "<br>".join(f"・{html.escape(b.strip())}" for b in bullets)

        step_blocks.append(
            f"""    <div class="step-card{urgent_cls}">
      <div class="step-num">
        <span class="step-label">STEP</span>
        <span class="step-number">{num}</span>
      </div>
      <div class="step-body">
        <div class="step-heading">
          <span>{heading}</span>
          {tag_html}
        </div>
        <div class="step-desc">{desc_html}</div>
      </div>
    </div>"""
        )
        if idx < len(steps) - 1:
            step_blocks.append('    <div class="arrow-row">▼</div>')

    fl_title = html.escape(str(d.get("footer_left_title") or "再評価・方針修正の軸"))
    fl_text = _esc_br(str(d.get("footer_left_text") or ""))
    fr_title = html.escape(str(d.get("footer_right_title") or "リスク管理・チーム連携"))
    fr_text = _esc_br(str(d.get("footer_right_text") or ""))

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<style>
  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}
  html, body {{
    width: 1200px;
    height: 800px;
    overflow: hidden;
    background: #ffffff;
    font-family: "Noto Sans CJK JP", "Noto Sans JP", "Hiragino Sans", sans-serif;
    color: #1a2a3a;
  }}
  body {{
    padding: 30px 38px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}
  .header {{
    background: linear-gradient(135deg, #0f2942 0%, #1e4976 100%);
    color: #ffffff;
    padding: 18px 26px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    box-shadow: 0 4px 12px rgba(15, 41, 66, 0.12);
  }}
  .badge {{
    display: inline-block;
    background: rgba(255, 255, 255, 0.18);
    color: #e2f0ff;
    font-size: 11.5px;
    font-weight: 700;
    padding: 3px 11px;
    border-radius: 999px;
    letter-spacing: 0.06em;
    margin-bottom: 5px;
  }}
  .title {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.01em;
    line-height: 1.3;
  }}
  .subtitle {{
    font-size: 12.5px;
    color: #dbeafe;
    text-align: right;
    line-height: 1.48;
    flex-shrink: 0;
    max-width: 340px;
  }}
  .flow-container {{
    display: flex;
    flex-direction: column;
    gap: 7px;
    margin: 10px 0;
  }}
  .step-card {{
    display: flex;
    align-items: stretch;
    border: 2px solid #cbd5e1;
    border-radius: 11px;
    overflow: hidden;
    background: #ffffff;
    box-shadow: 0 2px 5px rgba(15, 41, 66, 0.04);
  }}
  .step-card.urgent {{
    border-color: #1e4976;
    background: #f4f9ff;
  }}
  .step-num {{
    width: 122px;
    background: #1e3a5f;
    color: #ffffff;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 10px;
    flex-shrink: 0;
  }}
  .step-card.urgent .step-num {{
    background: #0f2942;
  }}
  .step-label {{
    font-size: 11.5px;
    font-weight: 700;
    color: #93c5fd;
    letter-spacing: 0.08em;
  }}
  .step-number {{
    font-size: 24px;
    font-weight: 700;
    line-height: 1.1;
    margin-top: 2px;
  }}
  .step-body {{
    padding: 11px 20px;
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
  }}
  .step-heading {{
    font-size: 16px;
    font-weight: 700;
    color: #0f2942;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }}
  .step-tag {{
    font-size: 11px;
    font-weight: 700;
    background: #e0effe;
    color: #1e4976;
    padding: 2px 9px;
    border-radius: 6px;
    white-space: nowrap;
  }}
  .step-desc {{
    font-size: 13.5px;
    color: #334155;
    line-height: 1.5;
  }}
  .arrow-row {{
    display: flex;
    justify-content: center;
    align-items: center;
    height: 12px;
    color: #1e4976;
    font-size: 14px;
    font-weight: 700;
    line-height: 1;
  }}
  .footer-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }}
  .footer-card {{
    background: #f0f6ff;
    border: 1px solid #cbd5e1;
    border-left: 5px solid #1e4976;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 12.8px;
    line-height: 1.48;
    color: #1e293b;
  }}
  .footer-card strong {{
    color: #0f2942;
    display: block;
    margin-bottom: 2px;
  }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <span class="badge">{badge}</span>
      <div class="title">{title}</div>
    </div>
    <div class="subtitle">{subtitle}</div>
  </div>

  <div class="flow-container">
{chr(10).join(step_blocks)}
  </div>

  <div class="footer-grid">
    <div class="footer-card">
      <strong>{fl_title}</strong>
      {fl_text}
    </div>
    <div class="footer-card">
      <strong>{fr_title}</strong>
      {fr_text}
    </div>
  </div>
</body>
</html>
"""


def find_chrome() -> str:
    for b in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
        p = shutil.which(b)
        if p:
            return p
    raise RuntimeError("Chrome binary not found")


def render_png(html_path: Path, png_path: Path, width: int = 1200, height: int = 800) -> None:
    chrome = find_chrome()
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--ignore-certificate-errors",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            "--force-device-scale-factor=2",
            f"--screenshot={png_path}",
            f"file://{html_path.resolve()}",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# past_articles.md append & email delivery
# ---------------------------------------------------------------------------

def extract_headings(article_md: str) -> list[str]:
    headings: list[str] = []
    for line in article_md.splitlines():
        s = line.strip()
        if s.startswith("## ") or s.startswith("### "):
            headings.append(s)
    return headings


def append_past_articles(
    past_path: Path,
    *,
    today_str: str,
    title: str,
    article_type: str,
    unique_angle: str,
    article_md: str,
    draft_rel_dir: str,
    diagram_titles: list[str],
    sources: list[dict[str, Any]],
) -> None:
    if not past_path.exists():
        return
    existing = past_path.read_text(encoding="utf-8")
    nums = [int(m) for m in re.findall(r"^###\s+(\d+)\.\s+", existing, flags=re.MULTILINE)]
    next_num = (max(nums) + 1) if nums else 1

    headings = extract_headings(article_md)
    entry_lines = [
        "",
        "---",
        "",
        f"### {next_num}. {title}",
        f"- **記事キー / 状態:** `{draft_rel_dir}` / GitHub Actions自動下書き（{today_str} 作成）",
        f"- **無料 / 有料の別:** {article_type}",
        "- **独自の切り口:**",
        f"  - {unique_angle}",
        "- **見出し構成:**",
    ]
    for h in headings:
        indent = "    " if h.startswith("### ") else "  "
        entry_lines.append(f"{indent}- `{h}`")
    entry_lines.append("- **使用した表や図解:**")
    for idx, dt in enumerate(diagram_titles, start=1):
        entry_lines.append(f"  - `diagram_{idx}.png`：{dt}")
    entry_lines.append("- **引用文献（PubMed検証済み）:**")
    for s in sources:
        doi_str = f" doi:{s['doi']}" if s.get("doi") else ""
        entry_lines.append(
            f"  {s['ref_num']}. {s['author_citation']} {s['title']} "
            f"*{s['journal']}*. {s['year']};{s['citation_tail']}.{doi_str} PMID:{s['pmid']}"
        )
    entry_lines.append("")

    past_path.write_text(existing.rstrip() + "\n" + "\n".join(entry_lines), encoding="utf-8")


def send_draft_email(
    subject: str,
    body_text: str,
    attachments: list[Path],
) -> str:
    """Send draft email with multiple attachments via Gmail SMTP SSL (or local gmail plugin)."""
    # 1. Local Antigravity VM fallback (only when GMAIL_APP_PASSWORD is not set)
    gmail_cli = shutil.which("gmail")
    if gmail_cli and not os.environ.get("GITHUB_ACTIONS") and not GMAIL_APP_PASSWORD:
        body_tmp = attachments[0].parent / "_email_body.txt"
        body_tmp.write_text(body_text, encoding="utf-8")
        cmd = [
            gmail_cli,
            "mutate",
            "send-with-attachment",
            "--to",
            RECIPIENT,
            "--subject",
            subject,
            "--body-file",
            str(body_tmp),
        ]
        for att in attachments:
            cmd.extend(["--file", str(att)])
        res = subprocess.run(cmd, capture_output=True, text=True)
        body_tmp.unlink(missing_ok=True)
        if res.returncode == 0:
            return "sent_via_gmail_plugin"

    if not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD が設定されていないためメール送信できません。"
            "GitHub Secrets に GMAIL_APP_PASSWORD を設定してください。"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = RECIPIENT
    msg["To"] = RECIPIENT
    msg.set_content(body_text)

    for path in attachments:
        if not path.exists():
            continue
        suffix = path.suffix.lower()
        if suffix == ".png":
            maintype, subtype = "image", "png"
        elif suffix in (".md", ".txt"):
            maintype, subtype = "text", "markdown" if suffix == ".md" else "plain"
        else:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=45) as smtp:
        smtp.login(RECIPIENT, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)
    return "sent_via_smtp_ssl"


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Daily Note Draft Generator for GitHub Actions")
    parser.add_argument("--theme", type=str, default="", help="Override theme (does not mark note-themes.txt)")
    parser.add_argument("--dry-run", action="store_true", help="Skip email sending and note-themes.txt [済] update")
    parser.add_argument("--skip-email", action="store_true", help="Skip email delivery")
    parser.add_argument("--skip-mark-done", action="store_true", help="Do not update note-themes.txt or past_articles.md")
    args = parser.parse_args()

    now_jst = datetime.now(JST)
    today_str = now_jst.strftime("%Y-%m-%d")

    override_theme = args.theme.strip()
    selected_line_idx: int | None = None
    if override_theme:
        theme = override_theme
        print(f"[theme] Using manual override theme: {theme}")
    else:
        picked = pick_next_theme(THEMES_FILE)
        if picked is None:
            print("[info] No unprocessed themes found in note-themes.txt. Nothing to do.")
            return 0
        selected_line_idx, theme = picked
        print(f"[theme] Picked line {selected_line_idx + 1}: {theme}")

    # Step 1: Primary literature search via PubMed E-utilities
    raw_slug, queries, sources = search_primary_literature(theme)
    slug = sanitize_slug(raw_slug, today_str)
    dir_name = f"{today_str}_{slug}"
    out_dir = DRAFTS_DIR / dir_name
    if out_dir.exists():
        dir_name = f"{today_str}_{slug}_{now_jst.strftime('%H%M')}"
        out_dir = DRAFTS_DIR / dir_name
    out_dir.mkdir(parents=True, exist_ok=True)
    draft_rel_dir = f"drafts/{dir_name}"

    sources_md = format_sources_markdown(theme, queries, sources, today_str)
    sources_path = out_dir / "sources.md"
    sources_path.write_text(sources_md, encoding="utf-8")

    # Step 2: Article drafting + 2 diagrams JSON
    rules_text = RULES_FILE.read_text(encoding="utf-8") if RULES_FILE.exists() else ""
    past_summary = summarize_past_articles(PAST_ARTICLES_FILE)
    result = generate_article_and_diagrams(theme, sources, rules_text, past_summary)

    article_title = str(result.get("article_title") or theme).strip()
    article_type = str(result.get("article_type") or "有料構成").strip()
    unique_angle = str(result.get("unique_angle") or "").strip()
    summary_3 = result.get("three_point_summary") or {}
    editorial_analysis = str(result.get("editorial_analysis") or "").strip()
    medical_review = str(result.get("medical_review_check") or "").strip()
    x_post_teaser = str(result.get("x_post_teaser") or "").strip()

    article_md = str(result.get("article_markdown") or "").strip()
    if not article_md:
        raise RuntimeError("LLM returned empty article_markdown")

    # Ensure verified PubMed reference section is appended cleanly
    verified_refs_md = build_verified_references_markdown(sources, article_md)
    # Remove any model-written trailing "## 参考文献" block to guarantee 100% verified bibliographic links
    article_md_clean = re.sub(r"\n##+\s*参考文献[\s\S]*\Z", "", article_md).rstrip()
    final_article_md = f"{article_md_clean}\n\n{verified_refs_md}\n"

    article_path = out_dir / "article.md"
    article_path.write_text(final_article_md, encoding="utf-8")

    # Step 3: Build & render 2 high-resolution PNG diagrams
    d1 = result.get("diagram_1") or {}
    d2 = result.get("diagram_2") or {}

    d1_html_path = out_dir / "diagram_1.html"
    d1_png_path = out_dir / "diagram_1.png"
    d1_html_path.write_text(build_table_diagram_html(d1), encoding="utf-8")
    render_png(d1_html_path, d1_png_path, width=1200, height=800)

    d2_html_path = out_dir / "diagram_2.html"
    d2_png_path = out_dir / "diagram_2.png"
    d2_html_path.write_text(build_flow_diagram_html(d2), encoding="utf-8")
    render_png(d2_html_path, d2_png_path, width=1200, height=800)

    # Step 4: Save 3-part editorial report & metadata
    report_md = "\n".join(
        [
            f"# 自動生成レポート：{article_title}",
            "",
            f"- **作成日時:** {now_jst.strftime('%Y-%m-%d %H:%M JST')}",
            f"- **テーマ:** {theme}",
            f"- **保存先:** `{draft_rel_dir}`",
            "",
            "## 3点要約（臨床推論・評価・アプローチ）",
            f"- **① 臨床推論:** {summary_3.get('clinical_reasoning', '')}",
            f"- **② 評価:** {summary_3.get('assessment', '')}",
            f"- **③ アプローチ:** {summary_3.get('approach', '')}",
            "",
            "## 第1部：編集長分析",
            editorial_analysis,
            "",
            "## 第2部：医学的校閲チェック",
            medical_review,
            "",
            "## 第3部：最終チェック＆X告知素材（案）",
            "```text",
            x_post_teaser,
            "```",
            "",
        ]
    )
    report_path = out_dir / "report.md"
    report_path.write_text(report_md, encoding="utf-8")

    meta = {
        "created_at_jst": now_jst.isoformat(),
        "date": today_str,
        "theme": theme,
        "article_title": article_title,
        "article_type": article_type,
        "draft_dir": draft_rel_dir,
        "queries": queries,
        "pmids": [s["pmid"] for s in sources],
        "files": {
            "article": f"{draft_rel_dir}/article.md",
            "sources": f"{draft_rel_dir}/sources.md",
            "report": f"{draft_rel_dir}/report.md",
            "diagram_1": f"{draft_rel_dir}/diagram_1.png",
            "diagram_2": f"{draft_rel_dir}/diagram_2.png",
        },
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[saved] Draft & diagrams written to {draft_rel_dir}")

    # Step 5: Send email to RECIPIENT (unless --dry-run / --skip-email)
    github_dir_url = f"https://github.com/{GITHUB_REPO}/tree/main/{draft_rel_dir}"
    subject = f"【note自動下書き】{today_str} {article_title}"
    email_body = "\n".join(
        [
            "note自動下書き生成が完了しました。",
            "添付の article.md と図解PNG（diagram_1.png / diagram_2.png）を使って note.com に下書き作成してください。",
            "",
            f"■ テーマ: {theme}",
            f"■ 記事タイトル: {article_title}",
            f"■ 種別: {article_type}",
            f"■ GitHub保存先: {github_dir_url}",
            f"■ 検証済みPubMed一次文献: {len(sources)}件（PMID: {', '.join(s['pmid'] for s in sources)}）",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "【3点要約】",
            f"① 臨床推論: {summary_3.get('clinical_reasoning', '')}",
            f"② 評価: {summary_3.get('assessment', '')}",
            f"③ アプローチ: {summary_3.get('approach', '')}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "【医学的校閲チェック】",
            medical_review,
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "【X告知ポスト案（140字）】",
            x_post_teaser,
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "【note.com コピペ用本文（article.md 全文）】",
            "",
            final_article_md,
        ]
    )

    if args.dry_run or args.skip_email:
        print("[email] Skipped (--dry-run or --skip-email)")
    else:
        delivery_mode = send_draft_email(
            subject,
            email_body,
            [article_path, d1_png_path, d2_png_path, sources_path],
        )
        print(f"[email] Delivered to {RECIPIENT} via {delivery_mode}")

    # Step 6: Mark [済] in note-themes.txt & update past_articles.md (only after full success)
    if args.dry_run or args.skip_mark_done or selected_line_idx is None:
        print("[state] Skipped marking [済] in note-themes.txt")
    else:
        mark_theme_done(THEMES_FILE, selected_line_idx)
        append_past_articles(
            PAST_ARTICLES_FILE,
            today_str=today_str,
            title=article_title,
            article_type=article_type,
            unique_angle=unique_angle,
            article_md=final_article_md,
            draft_rel_dir=draft_rel_dir,
            diagram_titles=[
                str(d1.get("title") or "図解1"),
                str(d2.get("title") or "図解2"),
            ],
            sources=sources,
        )
        print(f"[state] Marked line {selected_line_idx + 1} as [済] and updated past_articles.md")

    return 0


if __name__ == "__main__":
    sys.exit(main())
