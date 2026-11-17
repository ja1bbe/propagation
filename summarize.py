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
  ・wr／wd（エンティティのみ）：各時刻の前後1時間で、受信できた回の割合（1文字＝5%刻み、0〜k＝0〜100%）と、
    受信できた日数（1文字、0〜z＝0〜35日）。ページの「いまお勧めのバンド」の判定に使う。
  ・w：受信できた回（o）と、同じ時刻の抜き取り全体（b）の宇宙天気の中央値 [回数, Kp, 太陽風の速さ km/s, SFI]。
  ・割合は24時間分を24文字で表す。1文字が1時間（0時〜23時UTC）で、0＝報告なし、1＝15%未満（1回でも報告があれば1）、2＝約20%…9＝約90%、A＝100%。

エンティティは PSK Reporter の ADIF 番号を、ナウキャストが使う cty.json の並び順の番号に対応させる（adif_cty.json）。

  python summarize.py --days 28
"""
import argparse, glob, gzip, io, json, os
from datetime import datetime, timedelta, timezone
import csv
from statistics import median

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

# 宇宙天気（抜き取りの時刻ごとに対応付ける）
#   Kp：GFZ の3時間値、SFI：GFZ の F10.7（観測値、その日の値）、太陽風の速さ：NOAA の1時間平均
GFZ = {}
for path in glob.glob(os.path.join(BASE, 'gfz', 'kp', '*.csv')):
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            GFZ[r['date']] = r
SPEED = {}
for path in glob.glob(os.path.join(BASE, 'spacewx', '*.csv')):
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                SPEED[r['hour_utc']] = float(r['speed_mean'])
            except (TypeError, ValueError):
                pass
def fnum(x, bad=-1):
    try:
        v = float(x)
        return None if v <= bad else v
    except (TypeError, ValueError):
        return None
SW = {}   # 抜き取り → (Kp, 太陽風の速さ, SFI)

# 抜き取りの一覧（第2版のみ）
SDATE = {}
samples = {}
with open(os.path.join(BASE, 'samples_v2.csv'), newline='', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        t = datetime.strptime(r['start_utc'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        if t >= since:
            samples[r['sample_id']] = int(r['hour_utc'][11:13])
            SDATE[r['sample_id']] = r['hour_utc'][:10]
            g = GFZ.get(r['hour_utc'][:10], {})
            h = int(r['hour_utc'][11:13])
            SW[r['sample_id']] = (fnum(g.get(f'kp{h // 3 + 1}')), SPEED.get(r['hour_utc']), fnum(g.get('f107obs')))
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

B36 = '0123456789abcdefghijklmnopqrstuvwxyz'
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
by_hour = {}
for s, h in samples.items():
    by_hour.setdefault(h, []).append(s)
def sw_med(sids):
    # 抜き取りの集合 → [回数, Kp の中央値, 太陽風の速さの中央値, SFI の中央値]（値がないものは除いて計算）
    out = [len(sids)]
    for i, nd in ((0, 1), (1, 0), (2, 0)):
        v = [SW[s][i] for s in sids if SW.get(s) and SW[s][i] is not None]
        out.append((round(median(v), 1) if nd else round(median(v))) if v else None)
    return out
for (kind, key, band), (sa, sr, st) in seen.items():
    if len(sa) < 2:          # 1回だけの報告は偶然の可能性が高いので載せない
        continue
    item = {'a': pct(sa), 'r': pct(sr), 't': pct(st)}
    if kind == 'e' and sr:
        # 「いまお勧めのバンド」の判定用：各時刻の前後1時間（3時間）で、受信できた回の割合（5%刻み）と、受信できた日数
        hr = {}
        for s_ in sr:
            hr.setdefault(samples[s_], []).append(s_)
        wr, wd = [], []
        for h in range(24):
            win = [(h + d) % 24 for d in (-1, 0, 1)]
            got = [s_ for w in win for s_ in hr.get(w, [])]
            tot = sum(n_hour[w] for w in win)
            wr.append(B36[min(20, round(20 * len(got) / tot))] if tot else '0')
            wd.append(B36[min(35, len({SDATE[s_] for s_ in got}))])
        item['wr'] = ''.join(wr); item['wd'] = ''.join(wd)
    if len(sr) >= 5:         # 中央値に意味が出るよう、受信できた回が5回以上のときだけ
        # 受信できた回の宇宙天気と、同じ時刻（受信の報告があった UTC の時）の抜き取り全体の宇宙天気
        hours = {samples[s] for s in sr}
        base = [s for h in hours for s in by_hour.get(h, [])]
        item['w'] = {'o': sw_med(sr), 'b': sw_med(base)}
    out[kind].setdefault(key, {})[band] = item

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
print(f'抜き取り {len(samples)}回（{since:%Y-%m-%d}以降）、エンティティ {len(out["e"])}、大区画 {len(out["f"])}、{os.path.getsize(OUT) / 1024:.0f} KB')
