# note-x-automation

理学療法士向け note 記事の下書き自動生成（毎日 19:30 JST）と X 告知ポスト＆図解カードの自動生成（毎日 19:45 JST）を GitHub Actions 上で無人実行するリポジトリです。

## ワークフロー一覧

| ワークフロー | 実行スケジュール (JST) | スクリプト | 処理内容 |
| :--- | :--- | :--- | :--- |
| **Daily Note Draft Generator** (`.github/workflows/daily_note_draft.yml`) | 毎日 19:30 (`30 10 * * *` UTC) | `run_daily_note_draft.py` | `note-themes.txt` から未処理テーマを1件選定 → PubMed 一次文献検索 → `rules.md` / `past_articles.md` 準拠で記事執筆＆図解PNG 2枚生成 → `drafts/` 保存 → `itoshota2006@gmail.com` へメール送信 → 該当行に `[済]` 付与＆Push |
| **Daily X Post & Summary Card** (`.github/workflows/daily_x.yml`) | 毎日 19:45 (`45 10 * * *` UTC) | `run_daily_x.py` | `x_rotation_state.json` に基づき今夜分・翌朝分の2記事を選定 → 140字ポスト＆図解カード生成 → 2通連続でメール送信 → 履歴更新＆Push |

## 必要な GitHub Actions Secrets

`Settings` → `Secrets and variables` → `Actions` → `Repository secrets` で設定します。

| Secret 名 | 用途 | 必須 |
| :--- | :--- | :---: |
| `GMAIL_APP_PASSWORD` | Gmail SMTP SSL 送信用のアプリパスワード（設定済み） | 必須 |
| `GEMINI_API_KEY` | Google AI Studio の Gemini API キー（記事執筆・PubMedクエリ生成・図解構成生成） | 必須（※1） |
| `OPENAI_API_KEY` | Gemini の代わりに OpenAI API を使う場合のキー | 任意（※1） |
| `NCBI_API_KEY` | PubMed E-utilities のレート上限引き上げ用（未設定でも動作） | 任意 |

※1 `GEMINI_API_KEY` または `OPENAI_API_KEY` のいずれか1つが必要です（両方が設定されている場合は `GEMINI_API_KEY` を優先します）。

### オプション Variables（任意）

`Settings` → `Secrets and variables` → `Actions` → `Variables` で使用モデルを変更できます。

- `GEMINI_MODEL`（既定値：`gemini-2.5-flash`、高品質重視なら `gemini-2.5-pro` など）
- `OPENAI_MODEL`（既定値：`gpt-4o`）

## テーマの追加方法

`note-themes.txt` に 1 行 1 テーマで追記してコミットすると、次回 19:30 JST の実行時に `[済]` が付いていない最上段のテーマから順に自動処理されます。

```text
大腿骨近位部骨折術後のトレンデレンブルグ歩行に対する中殿筋アプローチの限界と体幹機能評価
投球障害肩における胸椎伸展可動域と肩甲上腕リズムの臨床推論
```

## 生成物の構成（`drafts/YYYY-MM-DD_<slug>/`）

- `article.md`：note.com へそのまま貼り付けられる完成下書き（検証済み PubMed 参考文献リンク付き）
- `diagram_1.png` / `diagram_1.html`：臨床評価・比較整理表の図解（2400×1600 高解像度PNG）
- `diagram_2.png` / `diagram_2.html`：臨床推論・アプローチの4ステップフロー図解（2400×1600 高解像度PNG）
- `sources.md`：PubMed E-utilities から実取得した一次文献（PMID / DOI / 抄録原文）
- `report.md`：編集長分析・医学的校閲チェック・X告知ポスト案
- `meta.json`：実行メタデータ

## 手動テスト実行（ドライラン）

GitHub の `Actions` タブ → `Daily Note Draft Generator (19:30 JST)` → `Run workflow` から以下を指定して安全にテストできます。

- `dry_run: true` にすると、メール送信・`note-themes.txt` の `[済]` 更新・リポジトリへの Push を行わずに、生成結果を Workflow Artifact として確認できます。

## Macローカルでの NotebookLM 自動投入・一気通貫実行（`scripts/ingest_to_notebooklm.sh`）

Mac 上で `notebooklm-mcp-cli`（`nlm`）の初回ログイン（`nlm login --storage protected`）を済ませた後、以下のワンコマンドで `rules.md` 第7章のワークフローを実行できます。

```bash
# 1) 既存ファイル（下書き・sources.md・PDF等）を新規ノートブックへ自動投入し3点要約を作成
./scripts/ingest_to_notebooklm.sh \
  --theme "TKA術後の屈曲制限" \
  --file "drafts/tka_flexion_limit/TKA屈曲制限_下書き.md"

# 2) テーマを渡すだけで「Deep Research → NotebookLM自動投入 → 3点要約 → 記事執筆」を一気通貫実行
./scripts/ingest_to_notebooklm.sh \
  --theme "大腿骨近位部骨折術後のトレンデレンブルグ歩行に対する中殿筋アプローチの限界と体幹機能評価" \
  --deep-research \
  --draft

# 3) note-themes.txt の未執筆（最上段）テーマを自動選択して一気通貫実行
./scripts/ingest_to_notebooklm.sh --from-themes --deep-research --draft
```

- 出力物：`notebooklm_summary.md`（①臨床推論・②評価・③アプローチの3点要約）、`notebooklm_sources.txt`、`notebooklm_meta.json`、`--draft` 指定時は `draft_prompt.md` と `article_from_nlm.md`（環境変数 `GEMINI_API_KEY` / `OPENAI_API_KEY` があれば LLM API、未設定時は NotebookLM 自身でソース根拠限定執筆）。
- 事前確認には `--dry-run` を付けられます。
