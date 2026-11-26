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


def extract_recent_header_patterns(past_path: Path, limit: int = 2) -> list[str]:
    """Read the most recent N header thumbnail pattern letters (A-D) from past_articles.md."""
    if not past_path.exists():
        return []
    text = past_path.read_text(encoding="utf-8")
    found = re.findall(r"見出し画像:\s*パターン([A-D])", text)
    return found[-limit:] if found else []


def choose_non_repeating_header_pattern(requested: str, recent_patterns: list[str]) -> str:
    """Guarantee header pattern A-D does not repeat the most recent 1-2 articles."""
    req = str(requested or "").strip().upper()
    valid = ["A", "B", "C", "D"]
    if req in valid and req not in recent_patterns:
        return req
    for cand in valid:
        if cand not in recent_patterns:
            return cand
    # Fallback: avoid at least the immediately preceding pattern
    last = recent_patterns[-1] if recent_patterns else ""
    for cand in valid:
        if cand != last:
            return cand
    return "A"


def generate_article_and_diagrams(
    theme: str,
    sources: list[dict[str, Any]],
    rules_text: str,
    past_summary: str,
    recent_header_patterns: list[str] | None = None,
) -> dict[str, Any]:
    recent_header_patterns = recent_header_patterns or []
    avoid_patterns_str = ", ".join(recent_header_patterns) if recent_header_patterns else "なし"
    system_prompt = (
        "あなたは理学療法・運動学・臨床推論・文献の批判的吟味に精通した医療系note編集長兼校閲者です。"
        "提供されたPubMed一次文献データと編集ルール（rules.md）を厳格に遵守し、経験年数を問わず理学療法士が"
        "明日の臨床でそのまま使える高品質なnote下書き記事と2枚の図解構造データをJSON形式で出力してください。\n"
        "【最優先：読者表記・文体ルール】\n"
        "A. 分かりやすさの内部基準は『臨床1〜5年目でも理解できる水準』とするが、タイトル・見出し・本文・図解・X告知文に"
        "『若手』『新人』『初心者』『1〜5年目』『〇年目』など経験年数を限定する言葉は一切書かないこと。\n"
        "B. 記事本文では『〜してください』『〜すべきです』『〜するように』『〜しましょう』などの指示口調・上から目線を使わず、"
        "『〜という視点が重要になります』『〜と整理すると臨床で迷いにくくなります』『〜が判断の軸になります』のように、"
        "同じ臨床家として思考プロセスを共有するフラットで丁寧な文体にすること。\n"
        "C. 導入文は教科書的な一般論ではなく、現場で実際に迷う具体的な臨床場面への共感から始めること。\n"
        "D. 有料ライン `---【ここから先は有料エリア】---` の直前に、この先で手に入るもの（①具体的な判断フロー ②評価チェックリスト "
        "③介入の組み立て方）が一目で分かる3項目の箇条書き導線ブロックを置くこと。\n"
        "【絶対遵守の医学的誠実性ルール】\n"
        "1. 根拠は提供されたPubMed一次文献データ [1]〜[N] のみ。存在しない論文・著者・DOI・PMID・数値の捏造は絶対禁止。\n"
        "2. 提供されたのは抄録（Abstract）であるため、抄録に明記されていない数値・カットオフ・介入プロトコル細部は断定せず「抄録の範囲では未確認」「原著での確認が必要」と誠実に明記すること。\n"
        "3. 相関と因果、統計学的有意差と臨床的意義（MDC/MCID）を明確に区別すること。\n"
        "4. 過去記事（past_articles.md）とタイトル・構成・切り口・図解が重複しないようゼロベースで設計すること。\n"
        "5. 丁寧な「です・ます」調で、同じ臨床家として視点を共有するフラットなトーン。専門用語は初出時に補足し、1段落は3〜4行以内にする。\n"
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
  "article_title": "臨床で迷う場面が具体的に伝わり、思わず読みたくなる重複しない記事タイトル（経験年数を限定する語は禁止）",
  "article_type": "有料構成",
  "unique_angle": "過去記事との差別化ポイント・今回の独自の切り口（2〜3文）",
  "three_point_summary": {{
    "clinical_reasoning": "① 臨床推論の要点（3〜4行）",
    "assessment": "② 評価の要点（3〜4行）",
    "approach": "③ アプローチの要点（3〜4行）"
  }},
  "editorial_analysis": "【第1部：編集長分析】今回の想定場面（経験年数表記なし）、読者の悩み、中心となる学習ポイント1〜3個、ゼロベース構成設計の意図",
  "medical_review_check": "【第2部：医学的校閲チェック】使用した一次文献番号・抄録のみ確認による限界・未確認事項の扱い・相関と因果の区別チェック結果",
  "x_post_teaser": "140字以内のX告知ポスト案。URLプレースホルダー（https://note.com/benita_pt）の直前に『明日の臨床で迷わないための具体策はこちら👇』のような行動誘導文を1行入れること。経験年数を限定する語（若手・新人・初心者・〇年目）と指示口調（〜してください・〜しましょう・〜すべき）は使わないこと。",
  "article_markdown": "note.comへそのまま貼れる完成稿Markdown。以下の構成要件をすべて満たすこと:\\n- 先頭に `# 記事タイトル`\\n- 導入文（3〜4行/段落で3段落程度。教科書的な一般論ではなく、現場で迷う具体的な臨床場面への共感から書き出す）\\n- 導入文直後に `[toc]`\\n- テーマに最適化した見出し（`##`, `###`）で基礎〜評価・臨床推論を展開（引用箇所に `[1]`, `[2]` を明記）\\n- 本文中の適切な位置に `> **[図解1 挿入位置: diagram_1.png]**` と `> **[図解2 挿入位置: diagram_2.png]**` を配置\\n- 無料エリアの末尾に `---【ここから先は有料エリア】---` を配置し、その直前に『この先の有料パートで手に入るもの（①具体的な判断フロー ②評価チェックリスト ③介入の組み立て方）』が一目で分かる3項目の箇条書き導線ブロックを置く\\n- 有料エリアに臨床推論の深掘り、鑑別、負荷設定・再評価と方針修正、仮想症例（『仮想症例』と明記）または臨床チェックリストを詳述\\n- 最後にまとめ（参考文献セクションはスクリプト側で検証済みPubMed書誌から自動付与するため、本文末尾に重複作成してもよいが検証済み番号 [1]〜[{len(sources)}] 以外は絶対に使わないこと）\\n- 全体の文字数は4,000〜6,500文字程度で読み応えと実践性を両立すること",
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
  }},
  "header_thumbnail": {{
    "pattern": "A / B / C / D のいずれか1文字（直近使用パターン [{avoid_patterns_str}] は避け、記事内容に最もクリック率が高まる型を選択。A=常識の覆し・気づき型、B=ステップ・手順解説型、C=Before/After比較・対比型、D=インパクト重視・1メッセージ特化型）",
    "pattern_reason": "この見出し画像パターンを選択した理由（1〜2文）",
    "theme_tone": "light または dark（白〜ライトグレー基調の抜け感デザイン=light、ディープネイビー基調=dark）",
    "hook_line": "上部1行の短い当事者意識フック（全角18字以内・経験年数表記禁止）",
    "title_lead": "リード行（全角14字以内）",
    "hero_word_1": "特大表示する核心キーワード1（全角2〜6字）",
    "hero_conj": "接続語（例：と / × / の。不要なら空文字）",
    "hero_word_2": "特大表示する核心キーワード2（全角2〜6字。不要なら空文字）",
    "title_tail": "結び行（全角10字以内）",
    "evidence_title": "精密根拠グラフィックの見出し（全角18字以内・英語メタラベル禁止）",
    "evidence_ch1": "主対象筋・指標名（全角8字以内、例：中臀筋）",
    "evidence_ch2": "代償筋・対比指標名（全角8字以内、例：TFL・QL）",
    "evidence_left_label": "改善前ラベル（全角10字以内、例：通常外転）",
    "evidence_left_status": "改善前状態（全角10字以内、例：代償が過多）",
    "evidence_right_label": "改善後ラベル（全角10字以内、例：腹圧＋骨盤セット後）",
    "evidence_right_status": "改善後状態（全角10字以内、例：中臀筋が優位）",
    "ng_callout": "写真・図解上のNG代償ラベル（全角12字以内、例：× 骨盤の引き上げ代償）",
    "ok_callout": "写真・図解上のOK制御ラベル（全角12字以内、例：◎ 腸骨固定 → 中臀筋）",
    "steps": ["ステップ1（パターンB用・全角10字以内）", "ステップ2（全角10字以内）", "ステップ3（全角10字以内）"],
    "hashtags": ["#理学療法士", "#記事内容に即した短いタグ（2個厳守）"]
  }}
}}

注意:
- `diagram_1.rows` は **3〜4行** にして1200×800px内に美しく収めること。
- `diagram_2.steps` は **ちょうど4ステップ** にして1200×800px内に美しく収めること。
- `diagram_1` や `diagram_2`、`header_thumbnail` 内のテキストにはHTMLタグ・英語メタラベル・経験年数表記（「若手」「新人」「〇年目」等）を一切入れずプレーンテキストで書くこと。
- `header_thumbnail.hashtags` は必ず **2個** にすること。
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


def _sanitize_header_hashtags(raw_tags: Any) -> list[str]:
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            s = str(t or "").strip()
            if not s:
                continue
            if not s.startswith("#"):
                s = "#" + s
            if s not in tags:
                tags.append(s)
            if len(tags) >= 2:
                break
    if not tags:
        tags = ["#理学療法士", "#運動器リハ"]
    elif len(tags) == 1:
        tags.append("#運動器リハ" if tags[0] != "#運動器リハ" else "#理学療法士")
    return tags[:2]


def build_header_thumbnail_html(
    h: dict[str, Any],
    *,
    article_title: str = "",
    photo_path: Path | None = None,
) -> str:
    """Build 1280x670px editorial note header HTML supporting Patterns A/B/C/D.

    Adheres to the finalized 70% information-density balance:
    - High typographic jump ratio (>=1.5x) with organic yellow brush highlights
    - Minimal rounded boxes (sharp callout lines, editorial hairlines)
    - 1 precision scientific graphic (oscilloscope sEMG waveform or biomechanical vector)
    - No checklists, no English meta labels, max 2 hashtags + benita_pt
    """
    pattern = str(h.get("pattern") or "A").strip().upper()
    if pattern not in {"A", "B", "C", "D"}:
        pattern = "A"
    tone = str(h.get("theme_tone") or ("dark" if pattern == "D" else "light")).strip().lower()
    if tone not in {"light", "dark"}:
        tone = "light"

    hook_line = html.escape(str(h.get("hook_line") or "臨床の評価・介入で迷う場面へ").strip())
    title_lead = html.escape(str(h.get("title_lead") or "").strip())
    hero_w1 = html.escape(str(h.get("hero_word_1") or (article_title[:8] if article_title else "臨床推論")).strip())
    hero_conj = html.escape(str(h.get("hero_conj") or "").strip())
    hero_w2 = html.escape(str(h.get("hero_word_2") or "").strip())
    title_tail = html.escape(str(h.get("title_tail") or "").strip())

    ev_title = html.escape(str(h.get("evidence_title") or "筋電図（EMG）活動バランス比較").strip())
    ev_ch1 = html.escape(str(h.get("evidence_ch1") or "主動作筋").strip())
    ev_ch2 = html.escape(str(h.get("evidence_ch2") or "代償筋").strip())
    ev_l_lbl = html.escape(str(h.get("evidence_left_label") or "従来アプローチ").strip())
    ev_l_st = html.escape(str(h.get("evidence_left_status") or "代償が過多").strip())
    ev_r_lbl = html.escape(str(h.get("evidence_right_label") or "制御セット後").strip())
    ev_r_st = html.escape(str(h.get("evidence_right_status") or "主動作筋 優位").strip())

    ng_callout = html.escape(str(h.get("ng_callout") or "× 代償パターン").strip())
    ok_callout = html.escape(str(h.get("ok_callout") or "◎ 本来の運動連鎖").strip())

    raw_steps = h.get("steps") if isinstance(h.get("steps"), list) else []
    steps = [html.escape(str(s).strip()) for s in raw_steps if str(s).strip()][:3]
    while len(steps) < 3:
        steps.append(["土台を固定", "軸を整える", "収縮を確認"][len(steps)])

    tags = _sanitize_header_hashtags(h.get("hashtags"))
    tags_html = " &nbsp;".join(html.escape(t) for t in tags)

    has_photo = bool(photo_path and photo_path.exists())
    photo_uri = f"file://{photo_path.resolve()}" if has_photo and photo_path else ""

    # Theme palette tokens
    is_dark = tone == "dark"
    bg_color = "#081426" if is_dark else "#EEF2F6"
    fg_main = "#F8FAFC" if is_dark else "#0A1D37"
    fg_sub = "#CBD5E1" if is_dark else "#1E293B"
    fg_conj = "#94A3B8" if is_dark else "#334155"
    accent_w2 = "#38BDF8" if is_dark else "#0369A1"
    brush_fill = "rgba(250, 204, 21, 0.34)" if is_dark else "rgba(253, 224, 71, 0.92)"
    text_shadow = (
        "0 4px 20px rgba(0, 0, 0, 0.55)"
        if is_dark
        else "0 2px 0 rgba(255, 255, 255, 0.92), 0 8px 22px rgba(10, 29, 55, 0.20)"
    )

    hero_html = f"""
      <div class="title-hero">
        <span class="hero-term">
          {hero_w1}
          <svg class="brush-svg" viewBox="0 0 210 36" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M 4 24 Q 52 14, 108 19 T 204 15 L 207 30 Q 150 34, 98 30 T 3 33 Z" fill="{brush_fill}" />
          </svg>
        </span>
        {f'<span class="hero-conj">{hero_conj}</span>' if hero_conj else ''}
        {f'''<span class="hero-term second">
          {hero_w2}
          <svg class="brush-svg" viewBox="0 0 210 36" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M 4 22 Q 60 15, 112 20 T 205 16 L 207 31 Q 145 34, 95 29 T 2 32 Z" fill="{brush_fill}" />
          </svg>
        </span>''' if hero_w2 else ''}
      </div>
    """

    # Precision biomechanical studio backdrop SVG when no external clinical photo is attached
    fallback_studio_svg = f"""
      <svg class="studio-bio-svg" viewBox="0 0 1280 670" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <radialGradient id="bioGlow" cx="76%" cy="46%" r="44%">
            <stop offset="0%" stop-color="rgba(14, 165, 233, {0.22 if is_dark else 0.16})" />
            <stop offset="65%" stop-color="rgba(14, 165, 233, 0.04)" />
            <stop offset="100%" stop-color="rgba(14, 165, 233, 0)" />
          </radialGradient>
        </defs>
        <rect width="1280" height="670" fill="url(#bioGlow)" />
        <!-- Fine clinical measurement grid -->
        <g stroke="{'rgba(148,163,184,0.08)' if is_dark else 'rgba(15,35,66,0.055)'}" stroke-width="1">
          {''.join(f'<line x1="{x}" y1="0" x2="{x}" y2="670" />' for x in range(64, 1280, 64))}
          {''.join(f'<line x1="0" y1="{y}" x2="1280" y2="{y}" />' for y in range(64, 670, 64))}
        </g>
        <!-- Precision biomechanical goniometer arcs & kinematic axes on right -->
        <g transform="translate(940, 345)" fill="none">
          <circle cx="0" cy="0" r="215" stroke="{'rgba(56,189,248,0.20)' if is_dark else 'rgba(3,105,161,0.16)'}" stroke-width="1.5" stroke-dasharray="6 6" />
          <circle cx="0" cy="0" r="150" stroke="{'rgba(250,204,21,0.26)' if is_dark else 'rgba(15,35,66,0.14)'}" stroke-width="1.2" />
          <circle cx="0" cy="0" r="88" stroke="{'rgba(56,189,248,0.28)' if is_dark else 'rgba(2,132,199,0.22)'}" stroke-width="1.5" stroke-dasharray="4 4" />
          <line x1="-240" y1="0" x2="240" y2="0" stroke="{'rgba(148,163,184,0.22)' if is_dark else 'rgba(15,35,66,0.14)'}" stroke-width="1.2" />
          <line x1="0" y1="-240" x2="0" y2="240" stroke="{'rgba(148,163,184,0.22)' if is_dark else 'rgba(15,35,66,0.14)'}" stroke-width="1.2" />
          <!-- Kinematic joint chain representation -->
          <path d="M -30 -155 L 0 -35 L 0 45 L -115 155" stroke="{'rgba(248,250,252,0.55)' if is_dark else 'rgba(15,35,66,0.45)'}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" />
          <circle cx="-30" cy="-155" r="7" fill="#38BDF8" />
          <circle cx="0" cy="-35" r="9" fill="#FACC15" stroke="#0A1D37" stroke-width="2" />
          <circle cx="0" cy="45" r="11" fill="#0EA5E9" stroke="#0A1D37" stroke-width="2" />
          <circle cx="-115" cy="155" r="7" fill="#38BDF8" />
        </g>
      </svg>
    """

    photo_block = (
        f'<div class="bg-photo"><img src="{photo_uri}" alt="臨床評価ビジュアル" /></div>'
        if has_photo
        else fallback_studio_svg
    )

    veil_css = (
        (
            "radial-gradient(ellipse 64% 92% at 16% 34%, rgba(8, 20, 38, 0.88) 0%, rgba(8, 20, 38, 0.68) 48%, rgba(8, 20, 38, 0.0) 88%),"
            "linear-gradient(90deg, rgba(8, 20, 38, 0.78) 0%, rgba(8, 20, 38, 0.45) 36%, rgba(8, 20, 38, 0.0) 64%)"
        )
        if is_dark
        else (
            "radial-gradient(ellipse 64% 92% at 16% 34%, rgba(248, 250, 252, 0.88) 0%, rgba(248, 250, 252, 0.70) 48%, rgba(248, 250, 252, 0.0) 88%),"
            "linear-gradient(90deg, rgba(244, 247, 251, 0.72) 0%, rgba(244, 247, 251, 0.45) 36%, rgba(244, 247, 251, 0.0) 62%)"
        )
    )

    # Pattern-specific main body blocks
    if pattern == "A":
        # Pattern A: Myth-busting / Insight (Editorial Headline + Precision sEMG Oscilloscope + NG/OK Photo Callouts)
        body_markup = f"""
          <div class="headline-zone">
            <div class="hook-line"><span class="hook-bar"></span>{hook_line}</div>
            {f'<div class="title-lead">{title_lead}</div>' if title_lead else ''}
            {hero_html}
            {f'<div class="title-tail">{title_tail}</div>' if title_tail else ''}
          </div>

          <div class="emg-panel">
            <div class="emg-head">
              <div class="emg-title">{ev_title}</div>
              <div class="emg-legend">
                <span class="leg"><span class="leg-line" style="background:#38BDF8;"></span>{ev_ch1}</span>
                <span class="leg"><span class="leg-line" style="background:#FB7185;"></span>{ev_ch2}</span>
              </div>
            </div>
            <div class="emg-waves-wrap">
              <div class="wave-col">
                <div class="wave-header">
                  <span style="color:#CBD5E1;">{ev_l_lbl}</span>
                  <span style="color:#FB7185; font-size:12px;">{ev_l_st}</span>
                </div>
                <div class="wave-canvas-box"><svg id="emg-before" viewBox="0 0 228 126" width="228" height="126"></svg></div>
              </div>
              <div class="wave-arrow">→</div>
              <div class="wave-col">
                <div class="wave-header">
                  <span style="color:#FFFFFF;">{ev_r_lbl}</span>
                  <span style="color:#38BDF8; font-size:12px;">{ev_r_st}</span>
                </div>
                <div class="wave-canvas-box ok"><svg id="emg-after" viewBox="0 0 228 126" width="228" height="126"></svg></div>
              </div>
            </div>
          </div>

          <svg class="bio-svg" viewBox="0 0 1280 670" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <marker id="arrRed" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 1 L 10 5 L 0 9 z" fill="#EF4444" />
              </marker>
              <marker id="arrCyan" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 1 L 10 5 L 0 9 z" fill="#0284C7" />
              </marker>
            </defs>
            <line x1="894" y1="296" x2="894" y2="564" stroke="#FACC15" stroke-width="3" stroke-dasharray="7 5" />
            <line x1="884" y1="296" x2="904" y2="296" stroke="#FACC15" stroke-width="2.5" />
            <line x1="884" y1="564" x2="904" y2="564" stroke="#FACC15" stroke-width="2.5" />
            <circle cx="894" cy="432" r="6" fill="#FACC15" stroke="#0A1D37" stroke-width="2" />
            <path d="M 920 422 Q 988 368 1054 382" fill="none" stroke="#EF4444" stroke-width="4" stroke-linecap="round" marker-end="url(#arrRed)" />
            <circle cx="786" cy="450" r="34" fill="rgba(2, 132, 199, 0.14)" stroke="#0284C7" stroke-width="2.5" />
            <circle cx="786" cy="450" r="20" fill="none" stroke="#38BDF8" stroke-width="1.2" stroke-dasharray="4 3" />
            <circle cx="786" cy="450" r="5" fill="#38BDF8" />
            <line x1="786" y1="450" x2="546" y2="358" stroke="#0284C7" stroke-width="3.5" marker-end="url(#arrCyan)" />
            <polyline points="1018,372 1044,332 1244,332" fill="none" stroke="#EF4444" stroke-width="2.2" />
            <rect x="1044" y="298" width="200" height="32" fill="rgba(127, 29, 29, 0.90)" />
            <text x="1144" y="320" text-anchor="middle" fill="#FFFFFF" font-size="16" font-weight="900">{ng_callout}</text>
            <polyline points="798,482 820,524 914,524" fill="none" stroke="#FACC15" stroke-width="2.2" />
            <rect x="914" y="506" width="212" height="34" fill="rgba(10, 29, 55, 0.90)" />
            <text x="1020" y="529" text-anchor="middle" fill="#FACC15" font-size="16" font-weight="900">{ok_callout}</text>
          </svg>
        """
    elif pattern == "B":
        # Pattern B: Step-by-step Flow (Left sharp 3-step axis + Right dominant headline & visual)
        body_markup = f"""
          <div class="step-rail">
            <div class="step-rail-line"></div>
            <div class="step-node">
              <div class="step-num">01</div>
              <div class="step-text">{steps[0]}</div>
            </div>
            <div class="step-node">
              <div class="step-num">02</div>
              <div class="step-text">{steps[1]}</div>
            </div>
            <div class="step-node active">
              <div class="step-num">03</div>
              <div class="step-text">{steps[2]}</div>
            </div>
          </div>

          <div class="headline-zone pattern-b">
            <div class="hook-line"><span class="hook-bar"></span>{hook_line}</div>
            {f'<div class="title-lead">{title_lead}</div>' if title_lead else ''}
            {hero_html}
            {f'<div class="title-tail">{title_tail}</div>' if title_tail else ''}
          </div>

          <div class="emg-panel pattern-b-emg">
            <div class="emg-head">
              <div class="emg-title">{ev_title}</div>
              <div class="emg-legend">
                <span class="leg"><span class="leg-line" style="background:#38BDF8;"></span>{ev_ch1}</span>
                <span class="leg"><span class="leg-line" style="background:#FB7185;"></span>{ev_ch2}</span>
              </div>
            </div>
            <div class="emg-waves-wrap">
              <div class="wave-col">
                <div class="wave-header"><span style="color:#CBD5E1;">{ev_l_lbl}</span><span style="color:#FB7185;font-size:12px;">{ev_l_st}</span></div>
                <div class="wave-canvas-box"><svg id="emg-before" viewBox="0 0 228 126" width="228" height="126"></svg></div>
              </div>
              <div class="wave-arrow">→</div>
              <div class="wave-col">
                <div class="wave-header"><span style="color:#FFFFFF;">{ev_r_lbl}</span><span style="color:#38BDF8;font-size:12px;">{ev_r_st}</span></div>
                <div class="wave-canvas-box ok"><svg id="emg-after" viewBox="0 0 228 126" width="228" height="126"></svg></div>
              </div>
            </div>
          </div>
        """
    elif pattern == "C":
        # Pattern C: Before / After Split Contrast
        body_markup = f"""
          <div class="headline-zone pattern-c">
            <div class="hook-line"><span class="hook-bar"></span>{hook_line}</div>
            <div class="pattern-c-title-row">
              {f'<span class="title-lead" style="margin-right:12px;">{title_lead}</span>' if title_lead else ''}
              {hero_html}
              {f'<span class="title-tail" style="margin-left:10px;">{title_tail}</span>' if title_tail else ''}
            </div>
          </div>

          <div class="split-contrast">
            <div class="split-pane ng">
              <div class="split-head"><span class="split-tag ng">NG</span>{ev_l_lbl}（{ev_l_st}）</div>
              <div class="split-wave-box"><svg id="emg-before" viewBox="0 0 228 126" width="100%" height="186" preserveAspectRatio="none"></svg></div>
              <div class="split-caption ng">{ng_callout}</div>
            </div>
            <div class="split-vs">VS</div>
            <div class="split-pane ok">
              <div class="split-head"><span class="split-tag ok">OK</span>{ev_r_lbl}（{ev_r_st}）</div>
              <div class="split-wave-box ok"><svg id="emg-after" viewBox="0 0 228 126" width="100%" height="186" preserveAspectRatio="none"></svg></div>
              <div class="split-caption ok">{ok_callout}</div>
            </div>
          </div>
        """
    else:
        # Pattern D: Magazine Cover / High-Impact Single Message
        body_markup = f"""
          <div class="headline-zone pattern-d">
            <div class="hook-line"><span class="hook-bar"></span>{hook_line}</div>
            {f'<div class="title-lead" style="font-size:44px;margin-bottom:8px;">{title_lead}</div>' if title_lead else ''}
            {hero_html}
            {f'<div class="title-tail" style="font-size:54px;margin-top:8px;">{title_tail}</div>' if title_tail else ''}
          </div>
        """

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8" />
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1280px;
    height: 670px;
    overflow: hidden;
    font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP", "Noto Sans JP", "Yu Gothic", sans-serif;
    background: {bg_color};
    color: {fg_main};
    -webkit-font-smoothing: antialiased;
  }}
  .poster {{
    position: relative;
    width: 1280px;
    height: 670px;
    overflow: hidden;
    background: {bg_color};
  }}
  .bg-photo, .studio-bio-svg {{
    position: absolute;
    inset: 0;
    width: 1280px;
    height: 670px;
    z-index: 1;
  }}
  .bg-photo img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: 82% 28%;
    transform: scale(1.06) translateX(52px);
    display: block;
    filter: contrast(1.05) saturate(0.96);
  }}
  .studio-veil {{
    position: absolute;
    inset: 0;
    z-index: 2;
    background: {veil_css};
    pointer-events: none;
  }}
  .brand-vignette {{
    position: absolute;
    right: 0;
    bottom: 0;
    width: 440px;
    height: 92px;
    z-index: 3;
    background: radial-gradient(ellipse at 90% 100%, rgba(10, 29, 55, 0.68) 0%, transparent 72%);
    pointer-events: none;
  }}
  .top-hairline {{
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 5px;
    z-index: 8;
    background: linear-gradient(90deg, #0A1D37 0%, #0A1D37 42%, #FACC15 42%, #FACC15 62%, rgba(10, 29, 55, 0.25) 100%);
  }}
  .headline-zone {{
    position: absolute;
    left: 48px;
    top: 38px;
    width: 720px;
    z-index: 5;
  }}
  .headline-zone.pattern-b {{
    left: 410px;
    top: 42px;
    width: 820px;
  }}
  .headline-zone.pattern-c {{
    left: 48px;
    top: 26px;
    width: 1184px;
  }}
  .headline-zone.pattern-d {{
    left: 56px;
    top: 136px;
    width: 900px;
  }}
  .pattern-c-title-row {{
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 6px;
  }}
  .hook-line {{
    display: inline-flex;
    align-items: center;
    gap: 12px;
    font-size: 22px;
    font-weight: 900;
    color: {fg_main};
    letter-spacing: 0.04em;
    margin-bottom: 14px;
    text-shadow: {text_shadow};
  }}
  .hook-bar {{
    width: 5px;
    height: 24px;
    background: #DC2626;
    display: inline-block;
  }}
  .title-lead {{
    font-size: 38px;
    font-weight: 900;
    line-height: 1.25;
    color: {fg_sub};
    letter-spacing: -0.02em;
    margin-bottom: 4px;
    text-shadow: {text_shadow};
  }}
  .title-hero {{
    position: relative;
    display: inline-flex;
    align-items: baseline;
    gap: 10px;
    line-height: 1.04;
    margin: 4px 0 8px 0;
  }}
  .hero-term {{
    position: relative;
    display: inline-block;
    font-size: {'98px' if pattern == 'D' else ('78px' if pattern == 'C' else '90px')};
    font-weight: 900;
    letter-spacing: -0.04em;
    color: {fg_main};
    z-index: 2;
    text-shadow: {text_shadow};
  }}
  .hero-term.second {{ color: {accent_w2}; }}
  .brush-svg {{
    position: absolute;
    left: -10px;
    bottom: -4px;
    width: calc(100% + 20px);
    height: 44px;
    z-index: -1;
    pointer-events: none;
  }}
  .hero-conj {{
    font-size: 50px;
    font-weight: 900;
    color: {fg_conj};
    text-shadow: {text_shadow};
  }}
  .title-tail {{
    font-size: 46px;
    font-weight: 900;
    line-height: 1.15;
    color: {fg_main};
    letter-spacing: -0.02em;
    text-shadow: {text_shadow};
  }}
  /* Pattern A/B Precision sEMG Panel */
  .emg-panel {{
    position: absolute;
    left: 48px;
    bottom: 28px;
    width: 524px;
    height: 214px;
    z-index: 5;
    background: rgba(10, 24, 46, 0.92);
    border: 1px solid rgba(56, 189, 248, 0.35);
    border-top: 3px solid #0EA5E9;
    border-radius: 4px;
    padding: 12px 16px;
    box-shadow: 0 16px 36px rgba(10, 24, 46, 0.26);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}
  .emg-panel.pattern-b-emg {{
    left: 410px;
    bottom: 52px;
    width: 560px;
  }}
  .emg-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid rgba(148, 163, 184, 0.20);
    padding-bottom: 7px;
  }}
  .emg-title {{ font-size: 15px; font-weight: 800; color: #F1F5F9; letter-spacing: 0.03em; }}
  .emg-legend {{ display: flex; gap: 14px; font-size: 13px; font-weight: 700; color: #CBD5E1; }}
  .leg {{ display: inline-flex; align-items: center; gap: 6px; }}
  .leg-line {{ width: 14px; height: 3px; border-radius: 2px; }}
  .emg-waves-wrap {{
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 22px 1fr;
    align-items: center;
    gap: 6px;
    margin-top: 8px;
  }}
  .wave-col {{ display: flex; flex-direction: column; height: 100%; justify-content: space-between; }}
  .wave-header {{ display: flex; justify-content: space-between; align-items: baseline; font-size: 13px; font-weight: 800; margin-bottom: 4px; }}
  .wave-canvas-box {{
    position: relative;
    width: 100%;
    height: 126px;
    background: rgba(6, 15, 30, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 3px;
    overflow: hidden;
  }}
  .wave-canvas-box.ok {{ border-color: rgba(56, 189, 248, 0.45); }}
  .wave-arrow {{ text-align: center; font-size: 20px; font-weight: 900; color: #FACC15; padding-top: 16px; }}

  /* Pattern B: Sharp vertical 3-step rail */
  .step-rail {{
    position: absolute;
    left: 48px;
    top: 64px;
    bottom: 64px;
    width: 320px;
    z-index: 5;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}
  .step-rail-line {{
    position: absolute;
    left: 26px;
    top: 32px;
    bottom: 32px;
    width: 3px;
    background: linear-gradient(180deg, #0EA5E9 0%, #FACC15 100%);
    z-index: 1;
  }}
  .step-node {{
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    gap: 18px;
    background: rgba(10, 24, 46, 0.90);
    border-left: 4px solid #38BDF8;
    padding: 20px 20px 20px 14px;
    box-shadow: 0 12px 28px rgba(10, 24, 46, 0.22);
  }}
  .step-node.active {{ border-left-color: #FACC15; }}
  .step-num {{
    font-size: 30px;
    font-weight: 900;
    color: #38BDF8;
    min-width: 44px;
    text-align: center;
    letter-spacing: -0.02em;
  }}
  .step-node.active .step-num {{ color: #FACC15; }}
  .step-text {{ font-size: 24px; font-weight: 900; color: #FFFFFF; line-height: 1.2; }}

  /* Pattern C: Before / After Split Contrast */
  .split-contrast {{
    position: absolute;
    left: 48px;
    right: 48px;
    bottom: 60px;
    height: 332px;
    z-index: 5;
    display: grid;
    grid-template-columns: 1fr 64px 1fr;
    align-items: center;
  }}
  .split-pane {{
    height: 100%;
    background: rgba(10, 24, 46, 0.92);
    border: 1px solid rgba(148, 163, 184, 0.28);
    border-top: 4px solid #EF4444;
    padding: 18px 22px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    box-shadow: 0 16px 36px rgba(10, 24, 46, 0.28);
  }}
  .split-pane.ok {{ border-top-color: #38BDF8; }}
  .split-head {{
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 20px;
    font-weight: 900;
    color: #F8FAFC;
  }}
  .split-tag {{
    font-size: 14px;
    font-weight: 900;
    padding: 3px 10px;
    color: #FFFFFF;
    background: #DC2626;
  }}
  .split-tag.ok {{ background: #0284C7; }}
  .split-wave-box {{
    height: 190px;
    background: rgba(6, 15, 30, 0.88);
    border: 1px solid rgba(251, 113, 133, 0.35);
    overflow: hidden;
  }}
  .split-wave-box.ok {{ border-color: rgba(56, 189, 248, 0.45); }}
  .split-caption {{ font-size: 18px; font-weight: 900; color: #FCA5A5; }}
  .split-caption.ok {{ color: #FACC15; }}
  .split-vs {{
    text-align: center;
    font-size: 28px;
    font-weight: 900;
    color: {fg_main};
    text-shadow: {text_shadow};
  }}

  .bio-svg {{ position: absolute; inset: 0; width: 1280px; height: 670px; z-index: 4; pointer-events: none; }}
  .brand-footer {{
    position: absolute;
    right: 26px;
    bottom: 16px;
    z-index: 7;
    display: flex;
    align-items: center;
    gap: 14px;
  }}
  .hashtags {{
    font-size: 15px;
    font-weight: 700;
    color: rgba(255, 255, 255, 0.94);
    text-shadow: 0 2px 8px rgba(0, 0, 0, 0.70);
    letter-spacing: 0.03em;
  }}
  .brand-divider {{ width: 1px; height: 16px; background: rgba(255, 255, 255, 0.45); }}
  .brand-name {{
    font-size: 17px;
    font-weight: 900;
    color: #FACC15;
    letter-spacing: 0.06em;
    text-shadow: 0 2px 10px rgba(0, 0, 0, 0.75);
  }}
</style>
</head>
<body>
  <div class="poster">
    <div class="top-hairline"></div>
    {photo_block}
    <div class="studio-veil"></div>
    <div class="brand-vignette"></div>
    {body_markup}
    <div class="brand-footer">
      <span class="hashtags">{tags_html}</span>
      <span class="brand-divider"></span>
      <span class="brand-name">benita_pt</span>
    </div>
  </div>
  <script>
    (function () {{
      function makeSeededRandom(seed) {{
        let s = seed % 2147483647;
        if (s <= 0) s += 2147483646;
        return function () {{
          s = (s * 16807) % 2147483647;
          return (s - 1) / 2147483646;
        }};
      }}
      function buildWavePath(width, centerY, maxAmp, seed, burstStart, burstEnd) {{
        const rand = makeSeededRandom(seed);
        const pts = [];
        const step = 1.6;
        for (let x = 2; x <= width - 2; x += step) {{
          const norm = x / width;
          let env = 0.08;
          if (norm >= burstStart && norm <= burstEnd) {{
            const t = (norm - burstStart) / (burstEnd - burstStart);
            env = 0.12 + 0.88 * Math.pow(Math.sin(Math.PI * t), 0.72);
          }}
          const polarity = (Math.floor(x / step) % 2 === 0 ? 1 : -1);
          const spike = (0.25 + 0.75 * Math.pow(rand(), 0.65)) * maxAmp * env * polarity;
          const jitter = (rand() - 0.5) * 2.2;
          pts.push(`${{x.toFixed(1)}},${{(centerY + spike + jitter).toFixed(1)}}`);
        }}
        return pts.join(" ");
      }}
      function renderOscilloscope(svgId, cfg) {{
        const svg = document.getElementById(svgId);
        if (!svg) return;
        const W = 228, H = 126;
        let out = "";
        for (let x = 19; x < W; x += 19) out += `<line x1="${{x}}" y1="0" x2="${{x}}" y2="${{H}}" stroke="rgba(148,163,184,0.10)" stroke-width="1" />`;
        for (let y = 21; y < H; y += 21) out += `<line x1="0" y1="${{y}}" x2="${{W}}" y2="${{y}}" stroke="rgba(148,163,184,0.10)" stroke-width="1" />`;
        out += `<line x1="0" y1="63" x2="${{W}}" y2="63" stroke="rgba(148,163,184,0.28)" stroke-width="1" />`;
        out += `<line x1="0" y1="33" x2="${{W}}" y2="33" stroke="rgba(56,189,248,0.22)" stroke-width="1" stroke-dasharray="3 3" />`;
        out += `<line x1="0" y1="95" x2="${{W}}" y2="95" stroke="rgba(251,113,133,0.22)" stroke-width="1" stroke-dasharray="3 3" />`;
        out += `<polyline fill="none" stroke="#38BDF8" stroke-width="1.35" stroke-linejoin="round" points="${{buildWavePath(W, 33, cfg.ch1Amp, cfg.seed1, 0.14, 0.88)}}" />`;
        out += `<polyline fill="none" stroke="#FB7185" stroke-width="1.35" stroke-linejoin="round" points="${{buildWavePath(W, 95, cfg.ch2Amp, cfg.seed2, 0.10, 0.90)}}" />`;
        out += `<text x="6" y="13" fill="#7DD3FC" font-size="10" font-weight="800">{ev_ch1}</text>`;
        out += `<text x="6" y="76" fill="#FDA4AF" font-size="10" font-weight="800">{ev_ch2}</text>`;
        svg.innerHTML = out;
      }}
      renderOscilloscope("emg-before", {{ ch1Amp: 8, ch2Amp: 25, seed1: 101, seed2: 202 }});
      renderOscilloscope("emg-after", {{ ch1Amp: 26, ch2Amp: 7, seed1: 303, seed2: 404 }});
    }})();
  </script>
</body>
</html>
"""


def find_chrome() -> str:
    for b in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
        p = shutil.which(b)
        if p:
            return p
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_chrome.exists():
        return str(mac_chrome)
    raise RuntimeError("Chrome binary not found")


def render_png(
    html_path: Path,
    png_path: Path,
    width: int = 1200,
    height: int = 800,
    *,
    exact_size: bool = False,
) -> None:
    chrome = find_chrome()
    sips_bin = shutil.which("sips")
    use_sips_downsample = bool(exact_size and sips_bin)
    scale_factor = 2 if (not exact_size or use_sips_downsample) else 1
    target_png = png_path.with_name(f"{png_path.stem}_2x.png") if use_sips_downsample else png_path

    cmd = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--ignore-certificate-errors",
        "--hide-scrollbars",
        f"--window-size={width},{height}",
        f"--force-device-scale-factor={scale_factor}",
        f"--screenshot={target_png}",
        f"file://{html_path.resolve()}",
    ]
    if not os.environ.get("GITHUB_ACTIONS"):
        tmp_profile = Path("/tmp/chrome_note_draft_tmp")
        tmp_profile.mkdir(parents=True, exist_ok=True)
        cmd.insert(-1, f"--user-data-dir={tmp_profile}")

    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if use_sips_downsample and sips_bin:
        subprocess.run(
            [sips_bin, "-z", str(height), str(width), str(target_png), "--out", str(png_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        target_png.unlink(missing_ok=True)


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
    header_pattern: str = "A",
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
        f"- **記事キー / 状態:** `{draft_rel_dir}` / GitHub Actions自動下書き（{today_str} 作成）`[見出し画像: パターン{header_pattern}]`",
        f"- **無料 / 有料の別:** {article_type}",
        "- **独自の切り口:**",
        f"  - {unique_angle}",
        "- **見出し構成:**",
    ]
    for h in headings:
        indent = "    " if h.startswith("### ") else "  "
        entry_lines.append(f"{indent}- `{h}`")
    entry_lines.append("- **使用した表や図解:**")
    entry_lines.append(f"  - `header.png`：見出し画像（パターン{header_pattern}・1280×670px）")
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

def export_spinoff_noon_pearl(
    *,
    theme: str,
    article_title: str,
    today_str: str,
    slug: str,
    sources: list[dict[str, Any]],
) -> bool:
    """
    Extract 1 distinct sub-angle clinical pearl (NOT the main note theme itself, no URL, no 'note'/'ノート')
    from the fetched primary literature sources and append it to `knowledge/nlm_exported_kb.md`.
    """
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    from noon_post_generator import (
        NoonCandidate,
        append_kb_entry_markdown,
        ensure_valid_noon_post,
    )

    if not sources:
        return False

    source_snippets = []
    for s in sources[:5]:
        source_snippets.append(
            f"- PMID {s.get('pmid', '')} | {s.get('authors', '')} ({s.get('year', '')}) "
            f"{s.get('journal', '')}: {s.get('title', '')}\n"
            f"  Abstract: {str(s.get('abstract', ''))[:650]}"
        )
    sources_block = "\n".join(source_snippets)

    sys_prompt = (
        "あなたは運動器領域に精通した理学療法士です。"
        "与えられたPubMed一次文献の抄録のみを根拠に、元のnote記事テーマそのものの要約ではなく、"
        "文献内に含まれる『別の臨床ポイント・評価のコツ・測定時の注意点・負荷設定のワンポイント』を1つだけ切り出し、"
        "X単体で完結する投稿文をJSONで出力してください。\n"
        "【厳守ルール】\n"
        "- post は必ず全角110〜130文字以内（最大でも全角130字・X換算260ウェイト以内）に収めること。\n"
        "- 『note』『ノート』『ノートブック』『記事』『リンク』『URL』『詳しくは』『続きは』『プロフ』『👇』は絶対に使用禁止。\n"
        "- ソースに記載のない数値・論文名・基準の捏造は厳禁。\n"
        "- 丁寧な『です・ます』調、2〜3行（改行あり）で完結させること。\n"
        "- 『若手』『新人』『初心者』『〇年目』など経験年数を限定する言葉は一切使わないこと。\n"
        "- 『〜してください』『〜すべきです』『〜するように』『〜ましょう』などの指示口調は使わず、同じ臨床家として視点を共有するフラットな文体にすること。\n"
        "- 出力JSONキー: topic (20〜35字の別角度ポイント名), reference (著者・年・誌名), summary (1〜2文の要約), post (X投稿本文)"
    )
    user_prompt = (
        f"元の記事テーマ（これ自体の要約は避けること）: {theme}\n"
        f"元の記事タイトル: {article_title}\n\n"
        f"参照した一次文献:\n{sources_block}\n\n"
        "上記一次文献から、元の記事テーマとは別角度の臨床ワンポイント（評価のコツ・鑑別・測定誤差等）を1つJSONで出力してください。"
    )

    data = call_llm_json(sys_prompt, user_prompt, temperature=0.35, max_output_tokens=1024)
    sub_topic = str(data.get("topic") or "").strip()
    raw_post = str(data.get("post") or "").strip()
    reference = str(data.get("reference") or "").strip() or str(sources[0].get("title", ""))
    summary = str(data.get("summary") or "").strip()
    if not sub_topic or not raw_post:
        return False

    source_id = f"draft-{today_str}-{slug[:24]}__spinoff"
    cand = NoonCandidate(
        source_id=source_id,
        origin="nlm_spinoff",
        topic=sub_topic,
        domain="運動器・記事関連スピンオフ",
        reference=reference,
        summary=summary,
        parent_theme=theme,
    )
    final_post, ok, errs = ensure_valid_noon_post(raw_post, cand, avoid_theme=theme)
    if not ok:
        print(f"[noon-kb][warn] Generated spinoff post failed validation: {errs}")
        return False

    kb_path = BASE_DIR / "knowledge" / "nlm_exported_kb.md"
    written = append_kb_entry_markdown(
        kb_path,
        source_id=source_id,
        topic=sub_topic,
        post=final_post,
        origin="nlm_spinoff",
        domain="運動器・記事関連スピンオフ",
        reference=reference,
        summary=summary or f"記事テーマ『{theme}』の一次文献から切り出した別角度の臨床ポイント。",
        parent_theme=theme,
    )
    if written:
        print(f"[noon-kb] Appended spinoff pearl [{source_id}] {sub_topic} -> {kb_path.name}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily Note Draft Generator for GitHub Actions")
    parser.add_argument("--theme", type=str, default="", help="Override theme (does not mark note-themes.txt)")
    parser.add_argument("--dry-run", action="store_true", help="Skip email sending and note-themes.txt [済] update")
    parser.add_argument("--skip-email", action="store_true", help="Skip email delivery")
    parser.add_argument("--skip-mark-done", action="store_true", help="Do not update note-themes.txt or past_articles.md")
    parser.add_argument("--header-photo", type=str, default="", help="Optional clinical photo path for header.png")
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

    # Step 2: Article drafting + 2 diagrams + header thumbnail JSON
    rules_text = RULES_FILE.read_text(encoding="utf-8") if RULES_FILE.exists() else ""
    past_summary = summarize_past_articles(PAST_ARTICLES_FILE)
    recent_header_patterns = extract_recent_header_patterns(PAST_ARTICLES_FILE, limit=2)
    result = generate_article_and_diagrams(
        theme,
        sources,
        rules_text,
        past_summary,
        recent_header_patterns=recent_header_patterns,
    )

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

    # Step 3: Build & render header.png (1280x670px) and 2 high-resolution PNG diagrams
    header_spec = dict(result.get("header_thumbnail") or {})
    header_pattern = choose_non_repeating_header_pattern(
        str(header_spec.get("pattern") or ""),
        recent_header_patterns,
    )
    header_spec["pattern"] = header_pattern
    header_pattern_reason = str(header_spec.get("pattern_reason") or "").strip()

    header_photo_path = Path(args.header_photo).expanduser() if args.header_photo.strip() else None
    if header_photo_path and not header_photo_path.exists():
        print(f"[warn] --header-photo not found: {header_photo_path}, using vector studio backdrop")
        header_photo_path = None

    header_html_path = out_dir / "header.html"
    header_png_path = out_dir / "header.png"
    header_html_path.write_text(
        build_header_thumbnail_html(
            header_spec,
            article_title=article_title,
            photo_path=header_photo_path,
        ),
        encoding="utf-8",
    )
    render_png(header_html_path, header_png_path, width=1280, height=670, exact_size=True)
    print(f"[header] Generated {draft_rel_dir}/header.png (1280x670px, pattern={header_pattern})")

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
            f"- **見出し画像:** `header.png`（1280×670px / パターン{header_pattern}）{f' — {header_pattern_reason}' if header_pattern_reason else ''}",
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
        "header_pattern": header_pattern,
        "header_pattern_reason": header_pattern_reason,
        "queries": queries,
        "pmids": [s["pmid"] for s in sources],
        "files": {
            "article": f"{draft_rel_dir}/article.md",
            "sources": f"{draft_rel_dir}/sources.md",
            "report": f"{draft_rel_dir}/report.md",
            "header": f"{draft_rel_dir}/header.png",
            "diagram_1": f"{draft_rel_dir}/diagram_1.png",
            "diagram_2": f"{draft_rel_dir}/diagram_2.png",
        },
        "note_draft_assets": {
            "title": article_title,
            "header_image": str(header_png_path.resolve()),
            "body_markdown": str(article_path.resolve()),
            "diagrams": [str(d1_png_path.resolve()), str(d2_png_path.resolve())],
            "note_editor_url": "https://note.com/notes/new",
        },
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[saved] Draft, header.png & diagrams written to {draft_rel_dir}")

    # Step 5: Send email to RECIPIENT (unless --dry-run / --skip-email)
    github_dir_url = f"https://github.com/{GITHUB_REPO}/tree/main/{draft_rel_dir}"
    subject = f"【note自動下書き】{today_str} {article_title}"
    email_body = "\n".join(
        [
            "note自動下書き生成が完了しました。",
            "添付の見出し画像（header.png / 1280×670px）・article.md・図解PNG（diagram_1.png / diagram_2.png）を使って note.com に下書き作成してください。",
            "",
            f"■ テーマ: {theme}",
            f"■ 記事タイトル: {article_title}",
            f"■ 種別: {article_type}",
            f"■ 見出し画像: header.png（パターン{header_pattern}・1280×670px）",
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
            [article_path, header_png_path, d1_png_path, d2_png_path, sources_path],
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
            header_pattern=header_pattern,
        )
        print(f"[state] Marked line {selected_line_idx + 1} as [済] and updated past_articles.md")

    # Step 7: Append a distinct sub-angle clinical pearl for noon X posts into knowledge/nlm_exported_kb.md
    if not args.dry_run:
        try:
            export_spinoff_noon_pearl(
                theme=theme,
                article_title=article_title,
                today_str=today_str,
                slug=slug,
                sources=sources,
            )
        except Exception as e:
            print(f"[noon-kb][warn] Failed to export spinoff noon pearl (non-fatal): {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
