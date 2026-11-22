#!/usr/bin/env python3
"""
Macローカル用：NotebookLM（nlm CLI）の文献ソースを抽出し、
GitHub Actions でも利用できる Markdown ナレッジベース（knowledge/nlm_exported_kb.md）へ書き出すスクリプト。

使い方:
  # 全ノートブックから未登録ソースを最大5件抽出して knowledge/nlm_exported_kb.md に追記
  python3 scripts/export_nlm_knowledge.py

  # 特定のノートブックIDを指定して抽出
  python3 scripts/export_nlm_knowledge.py --notebook-id <UUID> --limit 10

  # 書き出し対象のみ確認（nlm notebook query を実行しない）
  python3 scripts/export_nlm_knowledge.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from noon_post_generator import (  # noqa: E402
    check_nlm_auth,
    extract_existing_note_context,
    fetch_live_nlm_candidates,
    find_nlm_binary,
    is_overlapping_with_existing_notes,
    parse_knowledge_markdown,
    query_nlm_for_noon_post,
    validate_noon_post,
)

JST = timezone(timedelta(hours=9))
DEFAULT_OUT = REPO_ROOT / "knowledge" / "nlm_exported_kb.md"


def format_entry_markdown(
    *,
    source_id: str,
    topic: str,
    notebook_id: str,
    notebook_title: str,
    post: str,
) -> str:
    indented_post = "\n".join(f"  {line}" for line in post.splitlines())
    return (
        f"\n## [{source_id}] {topic}\n"
        f"- source_id: {source_id}\n"
        f"- origin: nlm\n"
        f"- notebook_id: {notebook_id}\n"
        f"- notebook_title: {notebook_title}\n"
        f"- domain: 運動器・NotebookLM文献\n"
        f"- topic: {topic}\n"
        f"- reference: {topic}\n"
        f"- summary: NotebookLMノートブック『{notebook_title}』のソース『{topic}』から抽出。\n"
        f"- post: |\n"
        f"{indented_post}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export NotebookLM sources via nlm CLI into knowledge/nlm_exported_kb.md"
    )
    parser.add_argument("--notebook-id", default="", help="対象ノートブックUUID（カンマ区切り可）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="出力先Markdownファイルパス")
    parser.add_argument("--limit", type=int, default=5, help="1回の実行で新規抽出する最大件数")
    parser.add_argument("--dry-run", action="store_true", help="クエリを実行せず候補のみ表示")
    args = parser.parse_args()

    if args.notebook_id:
        os.environ["NLM_NOTEBOOK_ID"] = args.notebook_id

    nlm_bin = find_nlm_binary()
    if not nlm_bin:
        raise SystemExit(
            "[ERROR] 'nlm' CLI が見つかりません。Mac上で以下を実行してください:\n"
            "  uv tool install notebooklm-mcp-cli\n"
            "  nlm login --storage protected"
        )
    if not check_nlm_auth(nlm_bin):
        raise SystemExit(
            "[ERROR] NotebookLM 認証が切れています。Mac上で 'nlm login --storage protected' を実行してください。"
        )

    out_path = Path(args.out)
    existing_ctx = extract_existing_note_context(
        REPO_ROOT / "past_articles.md",
        REPO_ROOT / "note-themes.txt",
    )
    already_exported = {c.source_id for c in parse_knowledge_markdown(out_path)}

    candidates = fetch_live_nlm_candidates(nlm_bin, existing_ctx)
    targets = []
    for c in candidates:
        if c.source_id in already_exported:
            continue
        overlaps, reason = is_overlapping_with_existing_notes(c, existing_ctx)
        if overlaps:
            print(f"[skip] {c.source_id}: {reason}")
            continue
        targets.append(c)

    print(f"[info] 新規抽出対象: {len(targets)} 件（今回の上限: {args.limit} 件）")
    targets = targets[: args.limit]
    if args.dry_run:
        for c in targets:
            print(f"  - [{c.source_id}] {c.topic} (notebook: {c.notebook_title})")
        return

    if not targets:
        print("[info] 新規に書き出すソースはありませんでした。")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        header = (
            "# NotebookLM 抽出ナレッジベース（昼用X投稿・リンクなし）\n\n"
            f"`scripts/export_nlm_knowledge.py` により `nlm` CLI から自動書き出し（初回作成: {now_jst}）。\n"
            "GitHub Actions 実行時に自動で候補として読み込まれます。\n\n"
            "---\n"
        )
        out_path.write_text(header, encoding="utf-8")

    added = 0
    for c in targets:
        try:
            post = query_nlm_for_noon_post(nlm_bin, c)
            ok, errs = validate_noon_post(post)
            if not ok:
                print(f"[warn] バリデーションNGのためスキップ ({c.source_id}): {errs}")
                continue
            block = format_entry_markdown(
                source_id=c.source_id,
                topic=c.topic,
                notebook_id=c.notebook_id or "",
                notebook_title=c.notebook_title or "",
                post=post,
            )
            with out_path.open("a", encoding="utf-8") as f:
                f.write(block)
            added += 1
            print(f"[ok] 追加: [{c.source_id}] {c.topic}")
            time.sleep(2)
        except Exception as e:
            print(f"[warn] クエリ失敗 ({c.source_id}): {e}")

    print(f"[done] {added} 件を {out_path} へ書き出しました。git commit & push で GitHub Actions に反映されます。")


if __name__ == "__main__":
    main()
