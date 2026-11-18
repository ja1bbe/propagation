# propagation

JA1BBE による、宇宙天気と HF 伝搬の関係を調べるための予備実験のデータ収集です。

- 20分ごとに、PSK Reporter のリアルタイム配信を8分間受け取り（抜き取り）、
  日本の局が受信した報告（RX）と、日本の局が海外で受信された報告（TX）を集計します。
- 同じ機会に、DX Summit の最新のスポットから日本の局がスポットしたものだけを記録します。
  コメントに LP（長経路）・SP（短経路）・skew（ずらした方位）の記載があれば、その印も残します。
- あわせて NOAA SWPC の宇宙天気（Kp、太陽風、プロトン、X線）を1時間ごとの値にまとめます。
- 毎日1回、GFZ の地磁気・太陽指数（Kp、ap、Ap、F10.7）と、国際静穏日・擾乱日を取り込みます。
- 毎日1回、直近28日間の記録から「実績」の集計（data/summary/paths.json）を作ります。
  宇宙天気ナウキャスト（https://ja1bbe.github.io/ja1bbe-iono/m.html）が、相手を指定したときの表示に使います。
- 個々の報告やスポットのコールサインは保存せず、件数・局数・SNR などの集計値と、相手のエンティティだけを保存します。

## 仕組み
| ファイル | 役割 |
|---|---|
| sample_collect.py | 抜き取り（8分間）の受信と集計。部品は pskr_collect.py |
| cluster_collect.py | DX Summit の最新500件から、日本の局のスポットを記録 |
| gfz_collect.py | GFZ の Kp・ap・Ap・F10.7、国際静穏日・擾乱日の取り込み |
| summarize.py | 実績の集計（data/summary/paths.json）。集計の項目の意味はファイルの先頭に記載 |
| adif_cty.json | PSK Reporter の ADIF 番号から、ナウキャストの cty.json の番号への対応 |
| .github/workflows/collect.yml | 抜き取りと DXクラスターの記録 |
| .github/workflows/gfz.yml | GFZ の取り込みと実績の集計（毎日） |

GitHub Actions の定時起動は混雑時に大きく間引かれるため、主に外部のタイマー（cron-job.org）から
workflow_dispatch で起動しています。ワークフローに書いてある定時は予備です。

## 実績の数え方（要点）
- 抜き取り1回を1つの単位とし、その回に1件以上の報告があれば「開いていた」と数えます。
  件数で数えないのは、熱心な局が1つあるだけで値が大きく振れるのを避けるためです。
- 日本の局の受信を中心にし、送信（日本の局の信号が海外で受信された報告）は参考にとどめます。
  送信の届き方は出力とアンテナで大きく変わるためです。
- ローバンドでは、受信できた時刻の昼夜から、短経路・長経路のどちらで届いたかを推定しています（検証中）。

## 予測と運用の記録
- predictions.md：予測の事前記録です。結果を見る前に予測と判定基準を書き、外れも消さずに残します。
  コミット日時が、結果を見る前に予測したことの裏付けになります。
- observations.md：実際の交信や受信の結果の記録です。うまくいかなかった例も含め、書き足すだけにします。

## データ（data/）
| ファイル | 内容 |
|---|---|
| pskr/YYYY/MM/DD/HHMM.csv.gz | 抜き取り1回分（HHMM は開始時刻 UTC）の、向き・バンド・モード・日本側のコールエリア・相手の DXCC・相手のグリッド大区画ごとの報告数、局数、SNR |
| pskr_rx/YYYY/MM/DD/HHMM.csv.gz | 同じ抜き取りでの、バンドごとの日本の受信局の数（正規化の基準） |
| samples_v2.csv | 抜き取りの開始・中央・終了時刻、受け取った秒数、報告数 |
| pskr/…/HH.csv.gz、pskr_rx/…/HH.csv.gz、samples.csv | 第1版（2026-11-05〜06）の形式。時の境目をまたぐ問題があるため参考扱い |
| spacewx/YYYY-MM.csv | NOAA SWPC の宇宙天気の1時間ごとの値（後から届いた値で埋め直す） |
| gfz/kp/YYYY.csv | GFZ の Kp・ap（3時間ごと8個）、Ap、F10.7（観測値・調整値）、Bartels 自転番号と日目、確定の印（1日1行） |
| gfz/qd/QD_daysYYYY.txt | 国際静穏日・擾乱日（GFZ 配布のまま） |
| cluster/YYYY-MM.csv | DXクラスターの日本の局のスポット。時刻（分まで）、バンド、相手のエンティティ、スポットした局のコールエリア、LP・SP・SKEW の印、モード（推定） |
| cluster/seen.txt | 同じスポットを二重に数えないための印（内容から作った短いハッシュ。直近2日分） |
| summary/paths.json | 実績の集計（直近28日間。相手のエンティティ・グリッド大区画 × バンド × 時刻） |

## 出典と利用条件
- PSK Reporter（Philip Gladstone, N1DQ）とその MQTT 配信
- NOAA Space Weather Prediction Center
- DX Summit（DXクラスターのスポット）
- Kp、ap、Ap、国際静穏日・擾乱日：GFZ Helmholtz Centre for Geosciences（CC BY 4.0）
  - Matzka, J., Stolle, C., Yamazaki, Y., Bronkalla, O. and Morschhauser, A., 2021. The geomagnetic Kp index
    and derived indices of geomagnetic activity. Space Weather, https://doi.org/10.1029/2020SW002641
  - Matzka, J., Bronkalla, O., Tornow, K., Elger, K. and Stolle, C., 2021. Geomagnetic Kp index. V. 1.0.
    GFZ Data Services, https://doi.org/10.5880/Kp.0001
- F10.7：Dominion Radio Astrophysical Observatory and Natural Resources Canada（GFZ の配布ファイルから取得）
  - Tapping, K.F., 2013. The 10.7 cm solar radio flux (F10.7). Space Weather, 11, 394-406,
    https://doi.org/10.1002/swe.20064
- 黒点数（SN、WDC-SILSO）は利用条件が CC BY-NC 4.0 で異なるため、保存していません。

非営利の研究目的で利用しています。
