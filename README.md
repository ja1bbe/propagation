# JA propagation research (pilot)

JA1BBE による、宇宙天気と HF 伝搬の関係を調べるための予備実験のデータ収集です。

- 毎時1回（UTC の12分ごろ）、PSK Reporter のリアルタイム配信を8分間受け取り、
  日本の局が受信した報告（RX）と、日本の局が海外で受信された報告（TX）を1時間ごとに集計します。
- あわせて NOAA SWPC の宇宙天気（Kp、太陽風、プロトン、X線）を1時間ごとの値にまとめます。
- 個々の報告や相手局のコールサインは保存せず、件数・局数・SNR の集計値だけを保存します。

## データ（data/）
| ファイル | 内容 |
|---|---|
| pskr/YYYY/MM/DD/HH.csv.gz | 時・向き・バンド・モード・日本側のコールエリア・相手の DXCC・相手のグリッド大区画ごとの報告数、局数、SNR |
| pskr_rx/YYYY/MM/DD/HH.csv.gz | バンドごとの、報告した日本の受信局の数（正規化の基準） |
| samples.csv | 各時の抜き取りの開始時刻と秒数 |
| spacewx/YYYY-MM.csv | 宇宙天気の1時間ごとの値（後から届いた値で埋め直す） |

## 出典
- PSK Reporter（Philip Gladstone, N1DQ）とその MQTT 配信
- NOAA Space Weather Prediction Center

非営利の研究目的で利用しています。
