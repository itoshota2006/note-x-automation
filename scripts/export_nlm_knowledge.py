#!/usr/bin/env python3
"""
Macローカル用：NotebookLM（nlm CLI）の文献ソースを抽出し、
GitHub Actions でも利用できる Markdown ナレッジベース（knowledge/nlm_exported_kb.md）へ書き出すスクリプト。

特徴:
  - 全角130文字以内（X換算260ウェイト目安、最大280厳守）＆『note』『ノート』等の禁止語をプロンプトで徹底。
  - 禁止語混入や文字数超過時は自動でリトライ再生成＋文境界での要約トリミングを実施。
  - `--spinoff-theme "<記事テーマ>"` を指定すると、記事化したノートブック内の一次文献から
    「元の記事テーマそのものではなく、別の臨床ポイント・評価のコツ」を切り出して追記できる。
  - `--git-push` を付けると、追記した `knowledge/nlm_exported_kb.md` を自動で commit & push する。

使い方:
  # 1) 全ノートブックから未登録ソースを最大5件抽出して knowledge/nlm_exported_kb.md に追記
  python3 scripts/export_nlm_knowledge.py --limit 5

  # 2) 新しく作成したnote記事のノートブックから、別角度の臨床ポイントを2件抽出して自動Push
  python3 scripts/export_nlm_knowledge.py \
    --notebook-id <UUID> \
    --spinoff-theme "投球障害肩における胸椎伸展可動域と肩甲上腕リズムの臨床推論" \
    --limit 2 \
    --git-push

  # 3) 書き出し対象のみ確認（nlm notebook query を実行しない）
  python3 scripts/export_nlm_knowledge.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from noon_post_generator import (  # noqa: E402
    append_kb_entry_markdown,
    check_nlm_auth,
    ensure_valid_noon_post,
    extract_existing_note_context,
    fetch_live_nlm_candidates,
    find_nlm_binary,
    is_overlapping_with_existing_notes,
    parse_knowledge_markdown,
    query_nlm_for_noon_post,
    query_nlm_for_spinoff_pair,
    x_weighted_length,
)

JST = timezone(timedelta(hours=9))
DEFAULT_OUT = REPO_ROOT / "knowledge" / "nlm_exported_kb.md"


def git_commit_and_push(out_path: Path, added_count: int, spinoff_theme: str = "") -> None:
    rel_path = out_path.resolve().relative_to(REPO_ROOT.resolve())
    today_jst = datetime.now(JST).strftime("%Y-%m-%d")
    msg = (
        f"chore: add {added_count} NotebookLM spinoff clinical pearl(s) ({today_jst})"
        if spinoff_theme
        else f"chore: export {added_count} NotebookLM clinical pearl(s) for noon X ({today_jst})"
    )
    subprocess.run(["git", "-C", str(REPO_ROOT), "add", str(rel_path)], check=True)
    diff_res = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--cached", "--quiet", "--", str(rel_path)]
    )
    if diff_res.returncode == 0:
        print("[git] コミット対象の変更がありません。")
        return
    subprocess.run(["git", "-C", str(REPO_ROOT), "commit", "-m", msg], check=True)
    subprocess.run(["git", "-C", str(REPO_ROOT), "push", "origin", "main"], check=True)
    print(f"[git] {rel_path} を commit & push しました。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export NotebookLM sources via nlm CLI into knowledge/nlm_exported_kb.md"
    )
    parser.add_argument("--notebook-id", default="", help="対象ノートブックUUID（カンマ区切り可）")
    parser.add_argument(
        "--spinoff-theme",
        default="",
        help="元になったnote記事テーマを指定すると、そのテーマと重複しない別角度の臨床ポイントを切り出す",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="出力先Markdownファイルパス")
    parser.add_argument("--limit", type=int, default=5, help="1回の実行で新規抽出する最大件数")
    parser.add_argument("--git-push", action="store_true", help="追記後に自動で git commit & push origin main する")
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

    spinoff_mode = bool(args.spinoff_theme.strip())
    candidates = fetch_live_nlm_candidates(
        nlm_bin,
        existing_ctx,
        allow_overlap_notebooks=spinoff_mode or bool(args.notebook_id.strip()),
    )

    targets = []
    for c in candidates:
        entry_id = f"{c.source_id}__spinoff" if spinoff_mode else c.source_id
        if entry_id in already_exported or c.source_id in already_exported:
            continue
        if not spinoff_mode:
            overlaps, reason = is_overlapping_with_existing_notes(c, existing_ctx)
            if overlaps:
                print(f"[skip] {c.source_id}: {reason}")
                continue
        targets.append(c)

    print(
        f"[info] 新規抽出対象: {len(targets)} 件（モード: "
        f"{'別角度スピンオフ抽出' if spinoff_mode else '通常抽出'} / 上限: {args.limit} 件）"
    )
    targets = targets[: args.limit]
    if args.dry_run:
        for c in targets:
            print(f"  - [{c.source_id}] {c.topic} (notebook: {c.notebook_title})")
        return

    if not targets:
        print("[info] 新規に書き出すソースはありませんでした。")
        return

    added = 0
    for c in targets:
        try:
            if spinoff_mode:
                sub_topic, raw_post = query_nlm_for_spinoff_pair(
                    nlm_bin,
                    c,
                    avoid_theme=args.spinoff_theme.strip(),
                )
                entry_id = f"{c.source_id}__spinoff"
                entry_origin = "nlm_spinoff"
                entry_topic = sub_topic
            else:
                raw_post = query_nlm_for_noon_post(nlm_bin, c)
                entry_id = c.source_id
                entry_origin = "nlm"
                entry_topic = c.topic

            final_post, ok, errs = ensure_valid_noon_post(
                raw_post,
                c,
                nlm_bin=nlm_bin,
                avoid_theme=args.spinoff_theme.strip(),
            )
            if not ok:
                print(f"[warn] リトライ・自動トリミング後もバリデーションNGのためスキップ ({c.source_id}): {errs}")
                continue

            written = append_kb_entry_markdown(
                out_path,
                source_id=entry_id,
                topic=entry_topic,
                post=final_post,
                origin=entry_origin,
                domain="運動器・NotebookLMスピンオフ" if spinoff_mode else "運動器・NotebookLM文献",
                reference=c.topic,
                summary=(
                    f"記事テーマ『{args.spinoff_theme.strip()}』の参考文献『{c.topic}』から切り出した別角度の臨床ポイント。"
                    if spinoff_mode
                    else f"NotebookLMノートブック『{c.notebook_title or ''}』のソース『{c.topic}』から抽出。"
                ),
                notebook_id=c.notebook_id or "",
                notebook_title=c.notebook_title or "",
                parent_theme=args.spinoff_theme.strip(),
            )
            if written:
                added += 1
                wlen = x_weighted_length(final_post)
                print(f"[ok] 追加: [{entry_id}] {entry_topic} (weighted={wlen}/280)")
            time.sleep(2)
        except Exception as e:
            print(f"[warn] クエリ失敗 ({c.source_id}): {e}")

    print(f"[done] {added} 件を {out_path} へ書き出しました。")
    if added > 0 and args.git_push:
        git_commit_and_push(out_path, added, spinoff_theme=args.spinoff_theme.strip())
    elif added > 0:
        print("※ GitHub Actions へ反映するには git add / commit / push（または --git-push オプション）を実行してください。")


if __name__ == "__main__":
    main()
