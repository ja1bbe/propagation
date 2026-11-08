# propagation

JA1BBE による、宇宙天気と HF 伝搬の関係を調べるための予備実験のデータ収集です。

- 毎時1回、PSK Reporter のリアルタイム配信を8分間受け取り（抜き取り）、
  日本の局が受信した報告（RX）と、日本の局が海外で受信された報告（TX）を集計します。
- あわせて NOAA SWPC の宇宙天気（Kp、太陽風、プロトン、X線）を1時間ごとの値にまとめます。
- 毎日1回、GFZ の地磁気・太陽指数（Kp、ap、Ap、F10.7）と、国際静穏日・擾乱日を取り込みます。
- 個々の報告や相手局のコールサインは保存せず、件数・局数・SNR の集計値だけを保存します。

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

## 出典と利用条件
- PSK Reporter（Philip Gladstone, N1DQ）とその MQTT 配信
- NOAA Space Weather Prediction Center
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
