# 昼用X投稿 臨床知見ナレッジベース（運動器領域メイン）

NotebookLM の一次文献・ガイドライン等から抽出した、経験年数を問わず理学療法士が明日の臨床で使える臨床知見（※投稿文に経験年数表記・指示口調は使わない）（URL・宣伝なし、X単体完結用）。
- GitHub Actions 上で `nlm` CLI の認証が利用できない場合のフォールバック知識ベースとして使用されます。
- Mac ローカルで `python3 scripts/export_nlm_knowledge.py` を実行すると、NotebookLM のノートブックから抽出した知見を `knowledge/nlm_exported_kb.md` に同期し、自動的に候補へ追加できます。
- 既存の `past_articles.md` および `note-themes.txt` と重複するテーマは選定時に自動で除外されます。

---

## [kb-ankle-cai-wblt] 足関節捻挫後の背屈制限と荷重位背屈テスト（WBLT）
- source_id: kb-ankle-cai-wblt
- origin: knowledge_base
- domain: 運動器・足関節
- topic: 足関節外側靭帯損傷後の背屈制限とWBLT評価
- keywords: 足関節捻挫, 慢性足関節不安定症, CAI, 背屈制限, WBLT, 距骨後方滑走
- reference: Martin et al., JOSPT CPG 2021 (Ankle Stability and Movement Coordination Impairments)
- summary: 足関節外側靭帯損傷後は疼痛が引いても背屈可動域制限（距骨後方滑走不全）が残存しやすく、慢性足関節不安定症（CAI）や再捻挫の危険因子となる。非荷重位ゴニオメーターだけでなく、壁を使った荷重位背屈テスト（WBLT：壁〜母趾先端の距離・脛骨前傾角度）で左右差を確認し、距骨モビライゼーションとバランス訓練を組み合わせることが推奨される。
- post: |
  足関節捻挫後、「痛みが引いたから終了」にしていませんか？
  背屈制限（距骨の後方滑走不全）が残ると再捻挫や慢性不安定症（CAI）の引き金になります。
  明日の臨床では非荷重ROMだけでなく、壁を使った荷重位背屈テスト（WBLT）で左右差を見ておくと、動作とのつながりが整理しやすくなります。

## [kb-frozen-shoulder-irritability] 凍結肩の病期・刺激性（Irritability）に応じた負荷調整
- source_id: kb-frozen-shoulder-irritability
- origin: knowledge_base
- domain: 運動器・肩関節
- topic: 凍結肩の刺激性（Irritability）分類に基づくROM負荷調整
- keywords: 凍結肩, 肩関節周囲炎, 夜間痛, Irritability, 高刺激性, JOSPT
- reference: Kelley et al., JOSPT CPG 2013 (Shoulder Pain and Mobility Deficits: Adhesive Capsulitis)
- summary: 凍結肩の理学療法では「とにかく伸ばす」のではなく、疼痛の刺激性（High / Moderate / Low Irritability）で強度を切り分けることが重要。安静時痛・夜間痛が強く容易に痛みが誘発される高刺激期は、強引な終末域ストレッチを避け、無痛域での愛護的な自動・他動運動と患者教育・疼痛管理を優先する。
- post: |
  凍結肩の「夜間痛・安静時痛がある時期」に強引なストレッチは逆効果になりやすいです。
  JOSPTガイドラインでも高刺激期は疼痛誘発を避け、無痛域の愛護的運動と患者教育を優先します。
  まずは「夜間痛の有無」と「動かした後の痛み持続」で負荷段階を切り分けるのが鍵です。

## [kb-rcrsp-ssmp] 腱板関連肩痛（RCRSP）におけるインピンジメント徴候の限界と症状修正
- source_id: kb-rcrsp-ssmp
- origin: knowledge_base
- domain: 運動器・肩関節
- topic: 腱板関連肩痛の症状修正手技（SSMP）による負荷応答評価
- keywords: 腱板関連肩痛, RCRSP, 肩峰下インピンジメント, Neer, Hawkins, SSMP
- reference: Lewis, Br J Sports Med 2016 (Rotator cuff related shoulder pain)
- summary: NeerテストやHawkins-Kennedyテストなどのインピンジメント徴候は感度は比較的高いが特異度が低く、単独で特定の組織損傷を断定できない。痛みの誘発だけでなく、胸椎伸展・肩甲骨アシスト・上腕骨頭制御などを加えた際に挙上時痛が即時軽減するかを確認する「症状修正（SSMP）」が介入方針決定に有用。
- post: |
  肩の挙上時痛でNeerやHawkins陽性＝組織損傷の確定、と解釈していませんか？
  これらは特異度が低く単独での構造特定は困難です。
  痛みを出す検査で終わらせず、胸椎伸展や肩甲骨アシストで「その場で痛みが減るか」を見る症状修正の視点が治療選択に直結します。

## [kb-pfps-hip-knee] 膝蓋大腿関節痛（PFPS）に対する股関節＋膝関節の併用強化
- source_id: kb-pfps-hip-knee
- origin: knowledge_base
- domain: 運動器・膝関節
- topic: 膝蓋大腿関節痛における股関節外転・外旋筋強化の併用効果
- keywords: 膝蓋大腿関節痛, PFPS, ニーイン, 股関節外転筋, ステップダウン, JOSPT
- reference: Willy et al., JOSPT CPG 2019 (Patellofemoral Pain)
- summary: 膝蓋大腿関節痛（PFPS）に対する運動療法では、大腿四頭筋単独の強化よりも、股関節外転・外旋筋群と膝周囲筋を組み合わせた強化プログラムの方が、早期の疼痛軽減および機能改善に優れることがJOSPTガイドラインで強く推奨されている。ステップダウンテストでの動的膝外反（ニーイン）観察と組み合わせて評価する。
- post: |
  膝前面痛（PFPS）に大腿四頭筋トレだけで変化が乏しい時は、股関節の評価が抜けていないかという視点が重要になります。
  JOSPTガイドラインでも膝単独より「股関節外転・外旋＋膝」の併用強化が早期の除痛・機能改善に推奨されています。
  ステップダウンでの動的ニーイン観察が第一歩です。

## [kb-achilles-insertional-vs-midportion] アキレス腱障害（付着部 vs 中腹部）の負荷設計の違い
- source_id: kb-achilles-insertional-vs-midportion
- origin: knowledge_base
- domain: 運動器・足部
- topic: 付着部アキレス腱障害で背屈位ヒールドロップを避ける理由
- keywords: アキレス腱障害, 付着部腱障害, 中腹部腱障害, ヒールドロップ, 圧迫ストレス, HSR
- reference: Martin et al., JOSPT CPG 2018 (Achilles Pain, Stiffness, and Muscle Power Deficits)
- summary: アキレス腱障害は中腹部（踵骨付着部から2〜6cm上）と付着部で負荷戦略が異なる。付着部アキレス腱障害では過度な足関節背屈位（段差でのヒールドロップ等）で踵骨後上縁による腱への圧迫ストレスが増大し症状を悪化させやすいため、まずは平坦面でのカーフレイズなど背屈を制限した段階的負荷から開始する。
- post: |
  アキレス腱障害に一律で「段差を使ったヒールドロップ」を処方していませんか？
  付着部タイプでは背屈位で踵骨による腱への圧迫ストレスが強まり悪化しやすいです。
  圧痛部位が中腹部か付着部かを確認し、付着部なら平坦面での段階的負荷から始めるのが鉄則です。

## [kb-plantar-heel-windlass] 足底腱膜炎（足底踵部痛）に対するWindlass高負荷トレーニング
- source_id: kb-plantar-heel-windlass
- origin: knowledge_base
- domain: 運動器・足部
- topic: 足底踵部痛のWindlass機構を活用した高負荷筋力トレーニング
- keywords: 足底腱膜炎, 足底踵部痛, Windlass機構, Rathleff, カーフレイズ, 朝一歩目
- reference: Rathleff et al., Scand J Med Sci Sports 2015; Koc et al., JOSPT CPG 2023
- summary: 足底踵部痛（足底腱膜炎）に対するRCT（Rathleffら）では、足底腱膜ストレッチ単独群に比べ、丸めたタオルを足趾下に敷いて母趾伸展位（Windlass機構）でゆっくり行う高負荷カーフレイズ群が3か月時点で有意に優れた機能改善を示した。負荷適正は実施中〜翌朝の「朝一歩目の痛み」悪化の有無でモニタリングする。
- post: |
  足底腱膜炎にストレッチだけで伸び悩む時は、段階的な高負荷筋力トレも選択肢になります。
  タオルを足趾下に敷き母趾伸展位（Windlass機構）で行うゆっくりしたカーフレイズは、ストレッチ単独より3か月時の改善が優れる報告があります。
  翌朝一歩目の痛み推移が、負荷調整の判断軸になります。

## [kb-lateral-epicondylalgia-pfg] 外側上顆炎の指標としてのPain-Free Grip（無痛握力）
- source_id: kb-lateral-epicondylalgia-pfg
- origin: knowledge_base
- domain: 運動器・肘関節
- topic: 外側上顆炎におけるPain-Free Grip（無痛握力）と負荷管理
- keywords: 外側上顆炎, テニス肘, 無痛握力, Pain-Free Grip, 肘伸展位, 負荷管理
- reference: Coombes et al., JOSPT 2015 (Management of Lateral Elbow Tendinopathy)
- summary: 外側上顆炎（外側肘腱障害）の評価では最大握力よりも、肘伸展位・前腕回内位で「痛みが出る直前で止める無痛握力（Pain-Free Grip）」の方が臨床変化への反応性が高い。また強いストレッチやマッサージで痛みを煽るより、無痛〜軽度疼痛範囲での等尺性・段階的伸筋負荷と把持動作修正が有効。
- post: |
  外側上顆炎（テニス肘）の再評価で「最大握力」だけを測っていませんか？
  実は肘伸展位で痛みが出る直前の「無痛握力（Pain-Free Grip）」の方が臨床変化を鋭敏に捉えます。
  強い揉みほぐしで炎症を煽るより、無痛握力を指標に段階的な伸筋負荷と把持動作を整える考え方が有効です。

## [kb-acl-rts-criteria] ACL再建術後のスポーツ復帰（RTS）判断基準
- source_id: kb-acl-rts-criteria
- origin: knowledge_base
- domain: 運動器・膝関節
- topic: ACL再建術後の期間依存からの脱却と多面的RTS基準
- keywords: ACL再建術, スポーツ復帰, RTS, LSI, ホップテスト, ACL-RSI
- reference: Grindem et al., Br J Sports Med 2016; Webster & Feller, Am J Sports Med 2018
- summary: ACL再建術後の再断裂予防において「術後6か月だから復帰」という期間のみの判断はリスクが高い。術後9か月以降の復帰に加え、大腿四頭筋筋力LSI≧90%、ホップテストバッテリーLSI≧90%の達成により再損傷リスクが大きく低下すると報告されている。さらに心理的準備度（ACL-RSI）の評価も欠かせない。
- post: |
  ACL再建術後の復帰判断、「術後〇か月経ったから」だけで進めていませんか？
  再損傷リスクを下げるには期間だけでなく、大腿四頭筋筋力LSI≧90%・ホップテストLSI≧90%・心理的準備度（ACL-RSI）の統合が鍵です。
  数値と恐怖心の両輪で進捗を可視化すると、復帰判断で迷いにくくなります。

## [kb-hamstring-strain-askling] ハムストリングス肉離れの評価（Askling H-test）と遠心性負荷
- source_id: kb-hamstring-strain-askling
- origin: knowledge_base
- domain: 運動器・大腿
- topic: ハムストリングス肉離れのAskling H-testと再発予防
- keywords: ハムストリングス, 肉離れ, Askling H-test, ノルディックハムストリングス, 遠心性収縮, 再発予防
- reference: Askling et al., Br J Sports Med 2010; van Dyk et al., Br J Sports Med 2019
- summary: ハムストリングス肉離れは再発率が非常に高く、通常の徒手筋力や静的ストレッチ痛が消失しても高速伸張時の不安感が残存することがある。背臥位で素早く最大股関節屈曲を行うAskling H-testでの「不安感（insecurity）」の有無確認が復帰前評価に有用であり、遠心性負荷（ノルディック等）の導入が再発率低減に寄与する。
- post: |
  ハムストリングス肉離れで静的ストレッチ痛や圧痛が消えても、すぐ全力走に戻すのは危険です。
  背臥位で膝伸展のまま素早く脚を振り上げる「Askling H-test」で不安感が残る間は再発リスクが高め。
  段階的な遠心性負荷（ノルディック等）とセットで復帰準備を見極めることが判断の軸になります。

## [kb-gtps-leap-compression] 外側股関節痛（大転子部痛症候群：GTPS）の圧迫回避と負荷管理
- source_id: kb-gtps-leap-compression
- origin: knowledge_base
- domain: 運動器・股関節
- topic: 大転子部痛症候群（GTPS）におけるITBストレッチ禁忌と教育＋運動
- keywords: 大転子部痛症候群, GTPS, 中殿筋腱障害, 腸脛靭帯, 圧迫ストレス, LEAP試験
- reference: Mellor et al., BMJ 2018 (LEAP trial: Education plus exercise vs corticosteroid injection)
- summary: 大転子周囲の外側股関節痛（GTPS：中殿筋・小殿筋腱障害が主因）では、股関節内転位（脚組み、片脚立ちでの骨盤下制、患側を下にした側臥位、腸脛靭帯の強いストレッチ）が腱への圧迫ストレスを増大させる。LEAP試験では、圧迫回避の生活指導と段階的運動療法が注射や経過観察より長期成績に優れることが示された。
- post: |
  大転子周囲の痛み（GTPS・中殿筋腱障害）に、腸脛靭帯の強いストレッチを指導していませんか？
  股関節内転位は腱への圧迫ストレスを強め、痛みを長引かせやすいです。
  脚組みや患側下の横向き寝を避ける生活指導＋段階的な股外転筋負荷が、長期改善の王道（LEAP試験）です。

## [kb-drf-forearm-crps] 橈骨遠位端骨折術後に見落としやすい前腕回外とCRPS早期徴候
- source_id: kb-drf-forearm-crps
- origin: knowledge_base
- domain: 運動器・手関節
- topic: 橈骨遠位端骨折術後の前腕回外・手指屈曲とCRPS早期評価
- keywords: 橈骨遠位端骨折, 掌側ロッキングプレート, 前腕回外, 手指浮腫, CRPS, Budapest基準
- reference: Quadlbauer et al., Arch Orthop Trauma Surg 2017; Harden et al., Pain Med 2010 (Budapest criteria)
- summary: 橈骨遠位端骨折術後は手関節背屈ROMばかり注目されがちだが、ADL（洗顔・茶碗保持・更衣）には前腕回外可動域と手指の完全屈曲（浮腫管理・腱滑走）が直結する。また、損傷に見合わない疼痛・左右差のある皮膚温や色調変化・発汗異常・浮腫などCRPS徴候（Budapest基準）の早期把握と医師共有が極めて重要。
- post: |
  橈骨遠位端骨折術後、手関節の背屈角度ばかり追っていませんか？
  洗顔や茶碗保持など日常動作の満足度に直結するのは「前腕回外」と「手指の浮腫管理・完全屈曲」です。
  あわせて皮膚温・色調差や過敏痛などCRPS初期徴候を毎回チェックし、異変時は早期に医師へ共有する流れが安全管理の軸になります。

## [kb-proximal-humerus-pendulum] 上腕骨近位端骨折保存療法での振り子運動（Codman体操）の落とし穴
- source_id: kb-proximal-humerus-pendulum
- origin: knowledge_base
- domain: 運動器・肩関節
- topic: 上腕骨近位端骨折保存療法での振り子運動の代償と遠位関節管理
- keywords: 上腕骨近位端骨折, 振り子運動, Codman体操, 三角筋代償, 肘拘縮, 保存療法
- reference: Long et al., J Shoulder Elbow Surg 2010 (EMG analysis of pendulum exercises)
- summary: 上腕骨近位端骨折の保存療法初期に行われる振り子運動（Codman体操）は、体幹の揺れで受動的に腕を揺らすことが目的だが、患者が腕の筋力で大きく振り回すと三角筋・棘上筋の筋活動が過剰になり疼痛や転位リスクを高める。小さな振幅での脱力確認と、三角巾固定による肘伸展制限・手部浮腫の予防が不可欠。
- post: |
  上腕骨近位端骨折の初期に処方する振り子運動、「腕の力で大きく振り回す」代償になっていませんか？
  筋電図研究でも大振幅の振り子は三角筋活動が増えやすく疼痛を煽ります。
  体幹の揺れを使った脱力・小振幅の確認と、固定中に硬くなりやすい肘・手関節の運動を組み合わせる視点が重要になります。

## [kb-degenerative-meniscus-fidelity] 変性半月板断裂に対する保存的運動療法の有効性
- source_id: kb-degenerative-meniscus-fidelity
- origin: knowledge_base
- domain: 運動器・膝関節
- topic: 変性半月板断裂（ロッキングなし）における運動療法の位置づけ
- keywords: 半月板損傷, 変性断裂, FIDELITY試験, 関節鏡視下切除, 保存療法, 神経筋トレーニング
- reference: Sihvonen et al., N Engl J Med 2013 / Ann Rheum Dis 2020 (FIDELITY trial); Kise et al., BMJ 2016
- summary: 真のメカニカルロッキングを伴わない中高年の変性半月板断裂では、関節鏡視下半月板部分切除術はシャム手術や監視下運動療法と比較して、疼痛・膝機能の長期成績で明確な優位性が示されていない（FIDELITY試験、KiseらBMJ）。患者の不安を和らげ、下肢アライメント制御と神経筋・筋力トレーニングを段階的に行うことが第一選択となる。
- post: |
  中高年の変性半月板断裂で「手術しかない」と不安を抱える方は多いです。
  実はロッキングがない変性断裂では、監視下運動療法と部分切除術で中長期の疼痛・機能に大差がないと複数のRCTで示されています。
  不安を整理し、荷重下の膝制御と段階的筋力強化を組み立てる視点が軸になります。

## [kb-nslbp-startback-stratified] 非特異的腰痛の層別化（STarT Back）と恐怖回避への対応
- source_id: kb-nslbp-startback-stratified
- origin: knowledge_base
- domain: 運動器・腰部
- topic: 非特異的腰痛のSTarT Back層別化と活動維持指導
- keywords: 非特異的腰痛, STarT Back, 恐怖回避思考, 画像所見不一致, 層別化ケア, 活動維持
- reference: Hill et al., Lancet 2011 (STarT Back trial); Brinjikji et al., AJNR 2015
- summary: 無症状者でもMRI上の椎間板膨隆や変性は加齢とともに高頻度に見られるため、画像所見だけで腰痛の原因を断定しない姿勢が重要。またSTarT Back等のツールで心理社会的因子（恐怖回避・破局的思考）が高いハイリスク群を早期に見極め、過度の安静指導を避けて可能な範囲の活動維持と段階的曝露を支援することが慢性化予防に有効。
- post: |
  腰痛患者さんの「MRIで椎間板が傷んでいるから動けない」という不安、そのままにしていませんか？
  無症状者でも加齢性の椎間板変性は高頻度に見られます。
  STarT Back等で恐怖回避思考を早期に拾い、過度の安静を避けて「安全に動ける範囲の活動維持」を一緒に設計するのが慢性化予防の要です。

## [kb-lumbar-radiculopathy-slr] 腰椎神経根症におけるSLRとCrossed SLRの解釈の違い
- source_id: kb-lumbar-radiculopathy-slr
- origin: knowledge_base
- domain: 運動器・腰部
- topic: 腰椎椎間板ヘルニア評価におけるSLRとCrossed SLRの感度・特異度
- keywords: 腰椎椎間板ヘルニア, 神経根症, SLR, Crossed SLR, 感度, 特異度
- reference: Devillé et al., Spine 2000; van der Windt et al., Cochrane Database Syst Rev 2010
- summary: 腰椎椎間板ヘルニアによる神経根障害の理学所見では、SLR（下肢伸展挙上テスト）は感度が比較的高く除外に役立つ一方、特異度は低め（ハムストリングス張力や腰痛でも陽性化）。対して健側挙上で患側下肢痛が再現されるCrossed SLRは感度は低いが特異度が非常に高く、陽性時は椎間板ヘルニアによる神経根圧迫を強く示唆する。
- post: |
  腰椎神経根症の評価で、SLRとCrossed SLRの役割を混同していませんか？
  SLRは感度が高くスクリーニング向きですが特異度は低め。
  一方、健側挙上で患側下肢痛が出るCrossed SLRは特異度が非常に高く陽性時の確度が高いです。
  単独所見で決めず筋力・感覚・反射と統合して読む視点が重要になります。

## [kb-cervical-radiculopathy-wainner] 頚椎神経根症の評価クラスター（Wainnerクラスター）
- source_id: kb-cervical-radiculopathy-wainner
- origin: knowledge_base
- domain: 運動器・頚部
- topic: 頚椎神経根症におけるWainnerの4項目テストクラスター活用
- keywords: 頚椎神経根症, Wainnerクラスター, Spurlingテスト, ULTT-A, 頚椎牽引テスト, 頚椎回旋
- reference: Wainner et al., Spine 2003 (Reliability and diagnostic accuracy of the clinical examination for cervical radiculopathy)
- summary: 頚椎神経根症は単一の整形外科的テストでは診断精度に限界がある。Wainnerらの4項目クラスター（①ULTT-A陽性、②患側頚椎回旋<60°、③牽引テストで症状軽減、④Spurlingテスト陽性）のうち3項目以上陽性で陽性尤度比が大きく上昇し、臨床推論の確度を高められる。神経症状悪化時は速やかに医師へ連携する。
- post: |
  上肢のしびれ・放散痛に対し、Spurlingテスト単独で頚椎神経根症を判断していませんか？
  ①ULTT-A陽性 ②患側回旋<60° ③牽引で軽減 ④Spurling陽性の「Wainner4項目」のうち3項目以上そろうと臨床推論の精度が大きく高まります。
  単発検査よりクラスターで統合すると臨床推論で迷いにくくなります。

## [kb-hip-fai-movement-mod] 股関節インピンジメント（FAI）に対する動作修正と筋協調アプローチ
- source_id: kb-hip-fai-movement-mod
- origin: knowledge_base
- domain: 運動器・股関節
- topic: 股関節インピンジメント（FAI）の挟み込み回避と日常動作修正
- keywords: 股関節インピンジメント, FAI, FADIR, 鼠径部痛, 動作修正, 深屈曲内旋
- reference: Griffin et al., Br J Sports Med 2016 (Warwick Agreement on femoroacetabular impingement syndrome)
- summary: 股関節インピンジメント症候群（FAI）では、骨形態（Cam/Pincer）そのものは徒手で変えられないが、深屈曲＋内転＋内旋位（FADIR肢位）の反復を避ける動作修正と、股関節外旋筋・深層安定化筋・体幹制御の強化により症状軽減が期待できる。しゃがみ込み時の足部開排や椅子高さ調整、強引な深屈曲ストレッチ回避が実用的。
- post: |
  鼠径部痛（FAI）に対し、「硬いから」と股関節の深屈曲・内旋ストレッチを繰り返していませんか？
  骨形態の挟み込みを煽り、かえって滑膜や関節唇の痛みを強めることがあります。
  FADIR肢位を避ける椅子高さやしゃがみ方（軽度外旋位）の修正と、股外旋・体幹制御の強化から組む流れが有効です。

## [kb-cts-splint-assessment] 手根管症候群（CTS）の初期評価と手関節中間位スプリント
- source_id: kb-cts-splint-assessment
- origin: knowledge_base
- domain: 運動器・手部
- topic: 手根管症候群における誘発テストの限界と夜間中間位固定
- keywords: 手根管症候群, CTS, Phalenテスト, Tinel徴候, 中間位スプリント, 母指球筋萎縮
- reference: Erickson et al., JOSPT CPG 2019 (Hand Pain and Sensory Deficits: Carpal Tunnel Syndrome)
- summary: 手根管症候群（CTS）ではPhalenテストやTinel徴候単独での診断精度には限界があり、正中神経領域（母指〜環指橈側）の夜間しびれ・振戦軽減徴候（flick sign）・2点識別覚・母指球筋萎縮（OKサインや外転筋力低下）を統合して評価する。軽〜中等度の保存療法では手関節中間位を保つ夜間スプリントがJOSPTガイドラインで強く推奨されている。
- post: |
  手根管症候群（CTS）の評価、PhalenやTinelの陽性・陰性だけで終わらせていませんか？
  夜間しびれや手を振ると楽になる訴え、そして母指球筋の萎縮・外転筋力低下の確認が重症度把握の要です。
  軽〜中等度では手関節中間位を保つ夜間スプリントがガイドラインでも強く推奨されています。

## [kb-mtss-bone-stress-diff] シンスプリント（MTSS）と脛骨疲労骨折の鑑別・負荷管理
- source_id: kb-mtss-bone-stress-diff
- origin: knowledge_base
- domain: 運動器・下腿
- topic: シンスプリント（MTSS）と脛骨疲労骨折の圧痛範囲・ホップ痛鑑別
- keywords: シンスプリント, MTSS, 脛骨疲労骨折, 骨ストレス障害, ホップテスト, 負荷管理
- reference: Winters et al., Br J Sports Med 2018 (Diagnosis and management of medial tibial stress syndrome)
- summary: 脛骨内側ストレス症候群（MTSS：シンスプリント）は脛骨後内側縁に5cm以上のびまん性圧痛を呈するのが特徴。一方、圧痛が5cm未満の1点に限局する場合や、安静時痛・夜間痛、片脚ホップテストでの鋭い局所痛がある場合は脛骨疲労骨折（骨ストレス障害）を疑い、運動継続を避けて速やかに整形外科医へ精査を依頼する。
- post: |
  ランナーの脛内側痛を「いつものシンスプリント」と決めつけていませんか？
  MTSSは5cm以上のびまん性圧痛が特徴ですが、1点に限局する強い骨圧痛・夜間痛・片脚ホップでの鋭痛があれば脛骨疲労骨折を疑うべきサインです。
  圧痛の広さと直近の練習量急増を押さえておくと鑑別で迷いにくくなります。

## [kb-osgood-load-biomechanics] オスグッド・シュラッター病の負荷管理と下肢連動評価
- source_id: kb-osgood-load-biomechanics
- origin: knowledge_base
- domain: 運動器・膝関節
- topic: オスグッド・シュラッター病における疼痛モニタリングと足関節・骨盤連動評価
- keywords: オスグッド, オスグッド・シュラッター病, 脛骨粗面, 成長期スポーツ障害, 足関節背屈, 負荷調整
- reference: Rathleff et al., Orthop J Sports Med 2020 (Activity Modification and Knee Strengthening for Osgood-Schlatter Disease)
- summary: オスグッド・シュラッター病は脛骨粗面への牽引ストレス過多による成長期障害で、大腿四頭筋の硬さだけでなく、足関節背屈制限や骨盤後傾・体幹後傾による着地時の膝前方剪断力増大が関与する。完全休止一辺倒ではなく、疼痛スケール（NRS 0〜2程度以内）を基準にした段階的活動調整と股・足関節を含めた下肢連動の修正が有効と報告されている。
- post: |
  オスグッド病に対し「大腿四頭筋のストレッチと完全休止」だけで対応していませんか？
  足関節背屈制限や骨盤後傾があると着地時の膝伸展モーメントが増大し脛骨粗面への牽引が強まります。
  痛みの許容範囲（軽度以内）を基準にした段階的な活動量調整と、足首・股関節を含めた動作修正が実践的です。
