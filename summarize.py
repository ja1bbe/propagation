# -*- coding: utf-8 -*-
"""
直近4週間の抜き取りから、「相手のエンティティ（または相手のグリッド大区画）× バンド × 時刻（UTC）」ごとに、
日本とのあいだで報告があった抜き取りの割合を集計し、data/summary/paths.json に書き出す。
宇宙天気ナウキャストのページが、相手を指定したときの「実績」の表示に使う。

数え方
  ・抜き取り1回（8分間）を1つの単位とし、その回に1件以上の報告があれば「開いていた」と数える。
    件数ではなく回数で数えるのは、熱心な局が1つあるだけで件数が大きく振れるのを避けるため。
  ・時刻は抜き取りの中央の時刻（UTC）で分ける。
  ・a：送受信どちらかの報告があった割合、r：日本の局が相手を受信、t：日本の局の信号が相手側で受信。
  ・n：その時刻の抜き取りの回数（割合の分母）。
  ・割合は24時間分を24文字で表す。1文字が1時間（0時〜23時UTC）で、0＝報告なし、1＝15%未満（1回でも報告があれば1）、2＝約20%…9＝約90%、A＝100%。

エンティティは PSK Reporter の ADIF 番号を、ナウキャストが使う cty.json の並び順の番号に対応させる（adif_cty.json）。

  python summarize.py --days 28
"""
import argparse, glob, gzip, io, json, os
from datetime import datetime, timedelta, timezone
import csv

BASE = os.environ.get('RESEARCH_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'adif_cty.json')
OUT = os.path.join(BASE, 'summary', 'paths.json')
BANDS = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m', '6m']

ap = argparse.ArgumentParser()
ap.add_argument('--days', type=int, default=28)
a = ap.parse_args()

now = datetime.now(timezone.utc)
since = now - timedelta(days=a.days)
adif = {int(k): v for k, v in json.load(open(MAP, encoding='utf-8')).items()}

# 抜き取りの一覧（第2版のみ）
samples = {}
with open(os.path.join(BASE, 'samples_v2.csv'), newline='', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        t = datetime.strptime(r['start_utc'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        if t >= since:
            samples[r['sample_id']] = int(r['hour_utc'][11:13])
n_hour = [0] * 24
for h in samples.values():
    n_hour[h] += 1

# 抜き取りごとに、どの相手・バンド・向きで報告があったかを集める
seen = {}   # (kind, key, band) -> [set(sid) for any, rx, tx]
def mark(kind, key, band, d, sid):
    s = seen.setdefault((kind, key, band), (set(), set(), set()))
    s[0].add(sid); (s[1] if d == 'RX' else s[2]).add(sid)

for path in glob.glob(os.path.join(BASE, 'pskr', '*', '*', '*', '????.csv.gz')):
    sid = None
    with gzip.open(path, 'rt', encoding='utf-8', newline='') as f:
        for r in csv.DictReader(f):
            sid = r['sample_id']
            if sid not in samples or r['band'] not in BANDS:
                continue
            try:
                code = int(float(r['dx_dxcc']))
            except ValueError:
                code = None
            if code in adif:
                mark('e', str(adif[code]), r['band'], r['dir'], sid)
            fld = (r['dx_field'] or '').upper()
            if len(fld) == 2:
                mark('f', fld, r['band'], r['dir'], sid)

LV = '0123456789A'   # 割合を10%刻みの1文字で表す（0＝0%、5＝50%、A＝100%）。ファイルを小さく保つため
def pct(sids):
    c = [0] * 24
    for s in sids:
        c[samples[s]] += 1
    # 1回でも報告があれば最低でも「1」にする（まれに開く時間帯を消さないため）
    return ''.join((LV[max(1, round(10 * c[h] / n_hour[h]))] if c[h] else '0') if n_hour[h] else '0' for h in range(24))

out = {'generated': now.strftime('%Y-%m-%d %H:%MZ'), 'since': since.strftime('%Y-%m-%d'),
       'days': a.days, 'samples': len(samples), 'n': n_hour,
       'note': '20分ごとに8分間（1時間あたり24分）PSK Reporter の配信を受信。日本の局が関わる国際間の報告。',
       'e': {}, 'f': {}}
for (kind, key, band), (sa, sr, st) in seen.items():
    if len(sa) < 2:          # 1回だけの報告は偶然の可能性が高いので載せない
        continue
    out[kind].setdefault(key, {})[band] = {'a': pct(sa), 'r': pct(sr), 't': pct(st)}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
print(f'抜き取り {len(samples)}回（{since:%Y-%m-%d}以降）、エンティティ {len(out["e"])}、大区画 {len(out["f"])}、{os.path.getsize(OUT) / 1024:.0f} KB')
