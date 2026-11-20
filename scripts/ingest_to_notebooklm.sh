#!/usr/bin/env bash
# ==============================================================================
# scripts/ingest_to_notebooklm.sh
#
# rules.md 第7章準拠：NotebookLM 自動投入・3点要約・一気通貫記事執筆スクリプト
#
# 主な機能:
#   1. nlm 認証確認・セッション自動リフレッシュ (nlm login --check || nlm auth refresh)
#   2. テーマ別の NotebookLM 新規ノートブック作成 (または既存 --notebook-id の利用)
#   3. 一次文献ソースの自動投入 (--file / --url / --deep-research)
#   4. 3点要約（①臨床推論・②評価・③アプローチ）の自動抽出 → notebooklm_summary.md
#   5. (--draft 指定時) rules.md & past_articles.md に準拠した記事下書き自動執筆
#      → article_from_nlm.md & draft_prompt.md
#
# 使用例:
#   # 例1: 既存の下書き・sources.md・PDF を新規ノートブックへ自動投入し3点要約を作成
#   ./scripts/ingest_to_notebooklm.sh \
#     --theme "TKA術後の屈曲制限" \
#     --file "drafts/tka_flexion_limit/TKA屈曲制限_下書き.md"
#
#   # 例2: テーマだけ渡して Deep Research → NotebookLM自動投入 → 3点要約 → 記事執筆を一気通貫実行
#   ./scripts/ingest_to_notebooklm.sh \
#     --theme "大腿骨近位部骨折術後のトレンデレンブルグ歩行に対する中殿筋アプローチの限界と体幹機能評価" \
#     --deep-research \
#     --draft
#
#   # 例3: note-themes.txt の未執筆（最上段）テーマを自動選択して一気通貫実行
#   ./scripts/ingest_to_notebooklm.sh --from-themes --deep-research --draft
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RULES_FILE="${REPO_ROOT}/rules.md"
PAST_ARTICLES_FILE="${REPO_ROOT}/past_articles.md"
THEMES_FILE="${REPO_ROOT}/note-themes.txt"

# デフォルト設定
THEME=""
FROM_THEMES=0
NOTEBOOK_ID=""
OUT_DIR=""
DEEP_RESEARCH=0
RESEARCH_QUERY=""
DO_DRAFT=0
DRAFT_VIA="auto" # auto | api | nlm
DRY_RUN=0
RATE_LIMIT_WAIT=2
FILES=()
URLS=()

usage() {
  cat <<'EOF'
使い方:
  ./scripts/ingest_to_notebooklm.sh [オプション] [テーマ名]

オプション:
  --theme <テーマ>          記事テーマ（ノートブック名および検索・要約の軸）
  --from-themes             note-themes.txt から [済] が付いていない最上段のテーマを自動取得
  --file <パス>             投入するローカルファイル（.md / .pdf / .txt 等、複数回指定可）
  --url <URL>               投入するWeb/PubMed URL（複数回指定可）
  --deep-research           NotebookLM 内蔵の Deep Research で一次文献を自動検索・自動取り込み
  --research-query <クエリ> Deep Research 用の英語検索クエリを明示指定
  --notebook-id <UUID>      新規作成せず既存ノートブックIDを使用する
  --out-dir <ディレクトリ>  要約・記事の出力先ディレクトリ
  --draft                   3点要約の後に rules.md 準拠の記事下書き (article_from_nlm.md) も自動生成
  --draft-via <方式>        記事執筆バックエンド: auto (既定) | api | nlm
  --dry-run                 nlm コマンドを実行せず、実行予定の手順のみ表示
  -h, --help                このヘルプを表示

例:
  # TKA下書きを投入して3点要約を作成
  ./scripts/ingest_to_notebooklm.sh \
    --theme "TKA術後の屈曲制限" \
    --file "drafts/tka_flexion_limit/TKA屈曲制限_下書き.md"

  # テーマを渡すだけで Deep Research → 自動投入 → 3点要約 → 記事執筆を一気通貫実行
  ./scripts/ingest_to_notebooklm.sh \
    --theme "投球障害肩における胸椎伸展可動域と肩甲上腕リズムの臨床推論" \
    --deep-research \
    --draft
EOF
}

log_info() {
  printf '[INFO] %s\n' "$*"
}

log_warn() {
  printf '[WARN] %s\n' "$*" >&2
}

log_err() {
  printf '[ERROR] %s\n' "$*" >&2
}

die() {
  log_err "$*"
  exit 1
}

# 引数パース（Bash 3.2 互換）
while [ $# -gt 0 ]; do
  case "$1" in
    --theme)
      [ $# -ge 2 ] || die "--theme には値が必要です"
      THEME="$2"
      shift 2
      ;;
    --from-themes)
      FROM_THEMES=1
      shift
      ;;
    --file)
      [ $# -ge 2 ] || die "--file にはファイルパスが必要です"
      FILES+=("$2")
      shift 2
      ;;
    --url)
      [ $# -ge 2 ] || die "--url にはURLが必要です"
      URLS+=("$2")
      shift 2
      ;;
    --deep-research)
      DEEP_RESEARCH=1
      shift
      ;;
    --research-query)
      [ $# -ge 2 ] || die "--research-query にはクエリが必要です"
      RESEARCH_QUERY="$2"
      DEEP_RESEARCH=1
      shift 2
      ;;
    --notebook-id)
      [ $# -ge 2 ] || die "--notebook-id にはUUIDが必要です"
      NOTEBOOK_ID="$2"
      shift 2
      ;;
    --out-dir)
      [ $# -ge 2 ] || die "--out-dir にはディレクトリパスが必要です"
      OUT_DIR="$2"
      shift 2
      ;;
    --draft)
      DO_DRAFT=1
      shift
      ;;
    --draft-via)
      [ $# -ge 2 ] || die "--draft-via には auto / api / nlm のいずれかを指定してください"
      DRAFT_VIA="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      die "不明なオプションです: $1（--help で使い方を確認してください）"
      ;;
    *)
      if [ -z "$THEME" ]; then
        THEME="$1"
      else
        die "余分な位置引数があります: $1"
      fi
      shift
      ;;
  esac
done

case "$DRAFT_VIA" in
  auto|api|nlm) ;;
  *) die "--draft-via は auto / api / nlm のいずれかを指定してください（指定値: $DRAFT_VIA）" ;;
esac

# note-themes.txt から未執筆テーマ取得
if [ "$FROM_THEMES" -eq 1 ] && [ -z "$THEME" ]; then
  [ -f "$THEMES_FILE" ] || die "テーマファイルが見つかりません: $THEMES_FILE"
  THEME="$(python3 - "$THEMES_FILE" <<'PY'
import re, sys
from pathlib import Path
p = Path(sys.argv[1])
done_re = re.compile(r"^\s*[\[［]済[\]］]")
for line in p.read_text(encoding="utf-8").splitlines():
    s = line.strip()
    if s and not done_re.match(s):
        print(s)
        break
PY
)"
  [ -n "$THEME" ] || die "note-themes.txt に未執筆のテーマがありません。"
  log_info "note-themes.txt から未執筆テーマを取得しました: ${THEME}"
fi

# テーマ未指定時、ファイル名から推定（--file が指定されている場合）
if [ -z "$THEME" ] && [ "${#FILES[@]}" -gt 0 ]; then
  first_base="$(basename "${FILES[0]}")"
  THEME="${first_base%.*}"
  log_info "--theme 未指定のためファイル名からテーマを設定しました: ${THEME}"
fi

[ -n "$THEME" ] || die "--theme <テーマ> または --from-themes を指定してください（--help で確認）。"

# 投入ソースが何も指定されていない場合、自動的に Deep Research を有効化
if [ "${#FILES[@]}" -eq 0 ] && [ "${#URLS[@]}" -eq 0 ] && [ "$DEEP_RESEARCH" -eq 0 ]; then
  log_info "ソース（--file / --url）が未指定のため、--deep-research を自動で有効にします。"
  DEEP_RESEARCH=1
fi

# 入力ファイルの存在確認（相対パスはリポジトリルート基準でも解決）
RESOLVED_FILES=()
if [ "${#FILES[@]}" -gt 0 ]; then
  for f in "${FILES[@]}"; do
    if [ -f "$f" ]; then
      RESOLVED_FILES+=("$(cd "$(dirname "$f")" && pwd)/$(basename "$f")")
    elif [ -f "${REPO_ROOT}/${f}" ]; then
      RESOLVED_FILES+=("$(cd "$(dirname "${REPO_ROOT}/${f}")" && pwd)/$(basename "${REPO_ROOT}/${f}")")
    else
      die "指定されたファイルが見つかりません: $f"
    fi
  done
fi

# 日付（JST優先）と出力先ディレクトリ決定
TODAY_JST="$(TZ=Asia/Tokyo date +%Y-%m-%d)"
NOW_JST="$(TZ=Asia/Tokyo date +%Y-%m-%dT%H:%M:%S+09:00)"

if [ -z "$OUT_DIR" ]; then
  if [ "${#RESOLVED_FILES[@]}" -eq 1 ]; then
    file_parent="$(dirname "${RESOLVED_FILES[0]}")"
    case "$file_parent" in
      "${REPO_ROOT}/drafts/"*)
        OUT_DIR="$file_parent"
        ;;
    esac
  fi
fi

if [ -z "$OUT_DIR" ]; then
  SLUG="$(python3 - "$THEME" "$TODAY_JST" <<'PY'
import hashlib, re, sys
theme, today = sys.argv[1], sys.argv[2]
ascii_part = re.sub(r"[^a-zA-Z0-9]+", "_", theme).strip("_").lower()
if len(ascii_part) >= 4:
    print(ascii_part[:40])
else:
    h = hashlib.sha1(theme.encode("utf-8")).hexdigest()[:8]
    print(f"topic_{h}")
PY
)"
  OUT_DIR="${REPO_ROOT}/drafts/${TODAY_JST}_${SLUG}"
fi

NOTEBOOK_TITLE="${TODAY_JST} ${THEME}"
SUMMARY_FILE="${OUT_DIR}/notebooklm_summary.md"
SOURCES_LIST_FILE="${OUT_DIR}/notebooklm_sources.txt"
META_FILE="${OUT_DIR}/notebooklm_meta.json"
PROMPT_FILE="${OUT_DIR}/draft_prompt.md"
ARTICLE_FILE="${OUT_DIR}/article_from_nlm.md"

SUMMARY_PROMPT="投入した一次文献ソースのみを根拠に、理学療法士（1〜7年目）の臨床判断に直結する形で以下の3点に整理してください。

1. ①臨床推論（病態・運動学的メカニズム、因果と相関の区別、鑑別すべき要因）
2. ②評価（具体的な評価項目、カットオフ値・感度/特異度・MDC/MCID・対象集団、評価の限界）
3. ③アプローチ（介入プロトコル・負荷設定・優先順位・再評価指標・適応と限界）

【厳守事項】
- ソースに記載されている事実・数値・論文情報のみを使用してください。
- ソースに明記されていない数値、DOI、カットオフ値、プロトコル細部は絶対に捏造せず『ソース上は未確認』と明記してください。
- 抄録のみ確認できる文献については断定を避けてください。"

log_info "テーマ: ${THEME}"
log_info "出力先: ${OUT_DIR}"
log_info "ノートブック名: ${NOTEBOOK_TITLE}"

if [ "$DRY_RUN" -eq 1 ]; then
  log_info "[DRY-RUN] 以下を実行予定:"
  echo "  1) nlm login --check || nlm auth refresh"
  if [ -n "$NOTEBOOK_ID" ]; then
    echo "  2) 既存ノートブック使用: ${NOTEBOOK_ID}"
  else
    echo "  2) nlm notebook create \"${NOTEBOOK_TITLE}\""
  fi
  if [ "${#RESOLVED_FILES[@]}" -gt 0 ]; then
    for rf in "${RESOLVED_FILES[@]}"; do
      echo "  3) nlm source add <NOTEBOOK_ID> --file \"${rf}\" --wait"
    done
  fi
  if [ "${#URLS[@]}" -gt 0 ]; then
    for u in "${URLS[@]}"; do
      echo "  3) nlm source add <NOTEBOOK_ID> --url \"${u}\" --wait"
    done
  fi
  if [ "$DEEP_RESEARCH" -eq 1 ]; then
    q="${RESEARCH_QUERY:-${THEME} physical therapy rehabilitation latest clinical practice guideline systematic review RCT MDC MCID cutoff limitations}"
    echo "  3) nlm research start \"${q}\" --notebook-id <NOTEBOOK_ID> --mode deep --auto-import"
  fi
  echo "  4) nlm source list <NOTEBOOK_ID> > \"${SOURCES_LIST_FILE}\""
  echo "  5) nlm notebook query <NOTEBOOK_ID> <3点要約プロンプト> > \"${SUMMARY_FILE}\""
  if [ "$DO_DRAFT" -eq 1 ]; then
    echo "  6) 記事執筆 (--draft-via ${DRAFT_VIA}) -> \"${ARTICLE_FILE}\""
  fi
  exit 0
fi

# 依存コマンド確認
if ! command -v nlm >/dev/null 2>&1; then
  die "'nlm' コマンドが見つかりません。Mac上で以下を実行してインストールしてください:
  uv tool install notebooklm-mcp-cli
  nlm login --storage protected"
fi

mkdir -p "$OUT_DIR"

# ==============================================================================
# Step 1: 認証確認とセッション自動リフレッシュ
# ==============================================================================
log_info "Step 1/5: NotebookLM 認証状態を確認しています..."
if ! nlm login --check >/dev/null 2>&1; then
  log_warn "セッションが未確認または期限切れの可能性があります。nlm auth refresh を試行します..."
  if ! nlm auth refresh; then
    die "NotebookLM の認証更新に失敗しました。Mac のターミナルで 'nlm login --storage protected' を実行して再ログインしてください。"
  fi
fi
log_info "認証 OK"

# ==============================================================================
# Step 2: ノートブック作成（または既存ID使用）
# ==============================================================================
if [ -z "$NOTEBOOK_ID" ]; then
  log_info "Step 2/5: 新規ノートブックを作成しています..."
  CREATE_OUT="$(nlm notebook create "$NOTEBOOK_TITLE")"
  printf '%s\n' "$CREATE_OUT"
  NOTEBOOK_ID="$(printf '%s\n' "$CREATE_OUT" | grep -Eo '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}' | head -1 || true)"
  if [ -z "$NOTEBOOK_ID" ]; then
    sleep "$RATE_LIMIT_WAIT"
    NOTEBOOK_ID="$(nlm notebook list --quiet | head -1 || true)"
  fi
  [ -n "$NOTEBOOK_ID" ] || die "作成されたノートブックの UUID を取得できませんでした。出力を確認してください。"
  log_info "ノートブック作成完了: ID=${NOTEBOOK_ID}"
  sleep "$RATE_LIMIT_WAIT"
else
  log_info "Step 2/5: 指定された既存ノートブックを使用します: ID=${NOTEBOOK_ID}"
fi

# ==============================================================================
# Step 3: 一次文献ソースの自動投入
# ==============================================================================
log_info "Step 3/5: 一次文献ソースを NotebookLM へ投入しています..."

if [ "${#RESOLVED_FILES[@]}" -gt 0 ]; then
  for rf in "${RESOLVED_FILES[@]}"; do
    log_info "  ファイル投入: ${rf}"
    nlm source add "$NOTEBOOK_ID" --file "$rf" --wait
    sleep "$RATE_LIMIT_WAIT"
  done
fi

if [ "${#URLS[@]}" -gt 0 ]; then
  for u in "${URLS[@]}"; do
    log_info "  URL投入: ${u}"
    nlm source add "$NOTEBOOK_ID" --url "$u" --wait
    sleep "$RATE_LIMIT_WAIT"
  done
fi

if [ "$DEEP_RESEARCH" -eq 1 ]; then
  if [ -z "$RESEARCH_QUERY" ]; then
    RESEARCH_QUERY="${THEME} physical therapy rehabilitation latest clinical practice guideline systematic review RCT MDC MCID cutoff limitations"
  fi
  log_info "  Deep Research 実行＆自動取り込み: ${RESEARCH_QUERY}"
  nlm research start "$RESEARCH_QUERY" --notebook-id "$NOTEBOOK_ID" --mode deep --auto-import
  sleep "$RATE_LIMIT_WAIT"
fi

# 投入ソース一覧の保存
log_info "Step 4/5: 投入済みソース一覧を取得しています..."
nlm source list "$NOTEBOOK_ID" | tee "$SOURCES_LIST_FILE"
sleep "$RATE_LIMIT_WAIT"

# ==============================================================================
# Step 4: 3点要約（①臨床推論・②評価・③アプローチ）の自動抽出
# ==============================================================================
log_info "Step 4/5: NotebookLM から3点要約を抽出しています..."
SUMMARY_RAW="$(nlm notebook query "$NOTEBOOK_ID" "$SUMMARY_PROMPT")"
[ -n "$SUMMARY_RAW" ] || die "NotebookLM からの要約レスポンスが空でした。"

cat > "$SUMMARY_FILE" <<EOF
# NotebookLM 3点要約：${THEME}

- **作成日時:** ${NOW_JST}
- **NotebookLM ノート名:** ${NOTEBOOK_TITLE}
- **Notebook ID:** \`${NOTEBOOK_ID}\`

---

${SUMMARY_RAW}
EOF

log_info "3点要約を保存しました: ${SUMMARY_FILE}"

# ==============================================================================
# Step 5: (--draft 指定時) rules.md 準拠の記事下書き自動執筆
# ==============================================================================
ARTICLE_GENERATED="false"
if [ "$DO_DRAFT" -eq 1 ]; then
  log_info "Step 5/5: rules.md 準拠の記事執筆を実行しています（方式: ${DRAFT_VIA}）..."

  # 編集長ルール＋過去記事サマリー＋NotebookLM 3点要約を統合したプロンプトを保存
  python3 - "$THEME" "$RULES_FILE" "$PAST_ARTICLES_FILE" "$SUMMARY_FILE" "$PROMPT_FILE" <<'PY'
import sys
from pathlib import Path

theme, rules_p, past_p, summary_p, out_p = [Path(x) if i > 0 else x for i, x in enumerate(sys.argv[1:])]
rules_text = rules_p.read_text(encoding="utf-8")[:4500] if rules_p.exists() else ""
past_lines = []
if past_p.exists():
    for line in past_p.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "タイトル" in line or "切り口" in line:
            past_lines.append(line)
past_summary = "\n".join(past_lines[:120])
summary_text = summary_p.read_text(encoding="utf-8")

prompt = f"""あなたは理学療法・運動学・臨床推論・文献の批判的吟味に精通した医療系note編集長兼校閲者です。
以下の編集ルール（rules.md）、過去記事サマリー（重複防止）、および NotebookLM 一次文献3点要約のみを根拠に、
新人（1〜2年目）〜中堅（3〜7年目）の理学療法士が明日の臨床で使える note 記事の完成下書きを Markdown 形式で執筆してください。

【絶対遵守ルール】
1. 根拠は提供された NotebookLM の一次文献データのみ。存在しない論文・DOI・数値の捏造や独自検索での補完は厳禁（未確認事項は「未確認」と明記）。
2. 相関と因果、統計学的有意差と臨床的意義（MDC/MCID）を明確に区別し、カットオフ値の対象集団を明記。
3. 丁寧な「です・ます」調で、先輩PTが後輩に誠実に解説する文体。1段落は3〜4行以内。
4. 導入文の直後に `[toc]` を配置し、有料構成の場合は無料エリアの末尾に `---【ここから先は有料エリア】---` を明記。
5. 過去記事と構成・切り口が重複しないゼロベース構成にする。

## 今回の執筆テーマ
{theme}

## 編集ルール（rules.md 抜粋）
{rules_text}

## 過去記事サマリー（重複防止用）
{past_summary}

## NotebookLM 一次文献3点要約（唯一の根拠）
{summary_text}
"""
out_p.write_text(prompt, encoding="utf-8")
PY
  log_info "執筆用統合プロンプトを保存しました: ${PROMPT_FILE}"

  USE_MODE="$DRAFT_VIA"
  if [ "$USE_MODE" = "auto" ]; then
    if [ -n "${GEMINI_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
      USE_MODE="api"
    else
      USE_MODE="nlm"
    fi
  fi

  if [ "$USE_MODE" = "api" ]; then
    log_info "LLM API を使って記事下書きを生成します..."
    python3 - "$PROMPT_FILE" "$ARTICLE_FILE" <<'PY'
import json, os, sys, urllib.request
from pathlib import Path

prompt = Path(sys.argv[1]).read_text(encoding="utf-8")
out_path = Path(sys.argv[2])

gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

if gemini_key:
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = data["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts).strip()
elif openai_key:
    model = os.environ.get("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.4,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {openai_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"].strip()
else:
    raise SystemExit("GEMINI_API_KEY または OPENAI_API_KEY が未設定です。--draft-via nlm を使用してください。")

if not text:
    raise SystemExit("LLM API から空のレスポンスが返りました。")
out_path.write_text(text + "\n", encoding="utf-8")
PY
    ARTICLE_GENERATED="true"
    log_info "記事下書きを保存しました: ${ARTICLE_FILE}"
  else
    log_info "NotebookLM（nlm notebook query）を使ってソース根拠限定で記事下書きを生成します..."
    sleep "$RATE_LIMIT_WAIT"
    NLM_DRAFT_PROMPT="投入した一次文献のみを根拠に、テーマ『${THEME}』について新人（1〜2年目）〜中堅（3〜7年目）の理学療法士向け note 記事の完成下書きを Markdown 形式で執筆してください。
【必須ルール】
- 丁寧な『です・ます』調で、先輩PTが後輩に誠実に解説するトーン。1段落は3〜4行以内。
- 導入文の直後に [toc] を配置する。
- 無料エリアで基礎知識・運動学・評価手順を解説し、末尾に ---【ここから先は有料エリア】--- を配置して、有料エリアで臨床推論の深掘り・鑑別・負荷設定・再評価と方針修正を詳述する。
- 相関と因果、統計学的有意差と臨床的意義（MDC/MCID）、カットオフ値の対象集団を明記する。
- ソースに記載のない論文・DOI・数値の捏造は絶対禁止。不明点は『未確認』と明記する。"
    nlm notebook query "$NOTEBOOK_ID" "$NLM_DRAFT_PROMPT" > "$ARTICLE_FILE"
    ARTICLE_GENERATED="true"
    log_info "記事下書きを保存しました: ${ARTICLE_FILE}"
  fi
else
  log_info "Step 5/5: --draft 未指定のため記事執筆はスキップしました（執筆まで行う場合は --draft を追加）。"
fi

# メタデータ保存
python3 - "$META_FILE" "$NOW_JST" "$THEME" "$NOTEBOOK_TITLE" "$NOTEBOOK_ID" "$SUMMARY_FILE" "$SOURCES_LIST_FILE" "$ARTICLE_FILE" "$ARTICLE_GENERATED" <<'PY'
import json, sys
from pathlib import Path

meta_path, now_jst, theme, nb_title, nb_id, summary_f, sources_f, article_f, gen = sys.argv[1:]
meta = {
    "created_at_jst": now_jst,
    "theme": theme,
    "notebook_title": nb_title,
    "notebook_id": nb_id,
    "summary_file": summary_f,
    "sources_list_file": sources_f,
    "article_file": article_f if gen == "true" else None,
}
Path(meta_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

log_info "=============================================="
log_info "完了しました！"
log_info "  Notebook ID : ${NOTEBOOK_ID}"
log_info "  3点要約      : ${SUMMARY_FILE}"
log_info "  ソース一覧   : ${SOURCES_LIST_FILE}"
if [ "$ARTICLE_GENERATED" = "true" ]; then
  log_info "  記事下書き   : ${ARTICLE_FILE}"
fi
log_info "  メタデータ   : ${META_FILE}"
log_info "=============================================="
