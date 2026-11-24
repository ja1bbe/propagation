# -*- coding: utf-8 -*-
"""
抜き取りのたびに、「いま」のまとめを data/summary/now.json に書き出す。
宇宙天気の通知メール（ja1bbe-iono の sw_alert.py）と、宇宙天気ナウキャストのページが読む。

  recent ：直近3時間に、日本の局が受信できたエンティティ（バンドごと）
           ・all：そのバンドで受信できたすべてのエンティティ（cty.json の番号）
           ・日本の局2局以上（または2つ以上のコールエリア）が受信したもの。1局だけの報告は、抜き取り3回以上で、
             160m・80m・60m では短経路か長経路の半分以上が夜のときだけ
           ・160m・80m・60m は、日本側の太陽高度が +5° 以上（はっきり昼）の受信を、局数にかかわらず載せない
             （読み違いや、ありえない受信の報告を除くため。日の出・日没の前後のグレイラインの受信は残る）
           ・ただし、同じエンティティ×バンドを日本の局が DXクラスターでスポットしていて、抜き取りの前後20分に入っていれば、
             1局1回の受信でも採る（spot の印を付ける）
           ・「最近の日本の記録ではまれ」なものを先に、残りを遠い順に、各バンド最大8つ
  outlook：これから3時間の見通し（過去の記録の部分だけ。宇宙天気による補正は、読む側が最新の値で行う）
           ・直近28日間の集計（paths.json）で、これからの3時間のいずれかに受信の割合が3割以上、
             かつ受信できた日が3日以上のエンティティ
           ・まれなものを先に、各バンド最大8つ
  score  ：見通しの採点。毎時最初の抜き取りで出した見通しを3時間後に、実際の受信と突き合わせる。
           比較のため、「直近3時間に受信できたものは、次の3時間にも受信できる」という単純な見込み（持続）も
           同じように採点する。日ごとの合計を data/summary/outlook_score.json に残す。

「まれ」：直近28日間の集計で、そのエンティティ×バンドの受信の割合（24時間分）の合計が小さいもの
         （1時間ごとの割合を 0〜10 で表した値の合計が 4 以下、または記録なし）。
         DXCC として珍しいという意味ではなく、「最近の日本の記録ではまれ」という意味。

  python make_now.py
"""
import csv, glob, gzip, json, math, os, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get('RESEARCH_DIR') or os.path.join(ROOT, 'data')
SUMDIR = os.path.join(BASE, 'summary')
PATHS = os.path.join(SUMDIR, 'paths.json')
OUT = os.path.join(SUMDIR, 'now.json')
HIST = os.path.join(SUMDIR, 'outlook_hist.json')
SCORE = os.path.join(SUMDIR, 'outlook_score.json')
MAP = os.path.join(ROOT, 'adif_cty.json')
CTY_URL = 'https://raw.githubusercontent.com/ja1bbe/ja1bbe-iono/main/docs/cty.json'
BANDS = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m', '6m']
JA_POS = (35.7, 139.7)
TOP = 8
RARE_MAX = 4          # 受信の割合の合計（0〜10 × 24時間）がこれ以下なら「まれ」
OUT_RATE = 3          # 見通し：受信の割合 3割以上
OUT_DAYS = 3          # 見通し：受信できた日 3日以上
SCORE_DAYS = 60       # 採点の記録を残す日数

now = datetime.now(timezone.utc)

def rdig(c):
    return 10 if c == 'A' else int(c) if c.isdigit() else 0

def dist_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    c = math.sin(la1) * math.sin(la2) + math.cos(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return 6371.0 * math.acos(max(-1.0, min(1.0, c)))

def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

adif = {int(k): v for k, v in load_json(MAP, {}).items()}
try:
    CTY = json.loads(urllib.request.urlopen(urllib.request.Request(CTY_URL, headers={'User-Agent': 'ja1bbe-propagation/1.0'}), timeout=30).read())
except Exception as e:
    print('cty.json を読めませんでした：', e)
    CTY = None
SUM = load_json(PATHS, None)
JAPAN = None
if CTY:
    for i, e in enumerate(CTY['ent']):
        if e[0] == 'Japan':
            JAPAN = i

def ent_info(i):
    if not CTY or i is None or i >= len(CTY['ent']):
        return None, None
    e = CTY['ent'][i]
    return e[0], round(dist_km(JA_POS, (e[1], e[2])))

def rarity(i, band):
    """直近28日間の受信の割合の合計（0〜240）。記録がなければ 0"""
    if not SUM:
        return 0
    d = SUM.get('e', {}).get(str(i), {}).get(band)
    if not d or 'r' not in d:
        return 0
    return sum(rdig(c) for c in d['r'])

# ---- 抜き取りの記録を読む（時刻の範囲を指定）
def sample_time(sid):
    try:
        return datetime.strptime(sid[:12], '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
    except ValueError:
        return None

LOW = ('160m', '80m', '60m')
_DAY = {}
def day_sample(st):
    if st not in _DAY:
        _DAY[st] = sun_elev(JA_POS[0], JA_POS[1], st + timedelta(minutes=4)) >= 5
    return _DAY[st]

def read_rx(t0, t1):
    """t0〜t1 に始まった抜き取りの受信（RX）の記録。{band: {cty番号: {s:抜き取りの集合, ja:最大の日本の局数, snr:最良}}} と抜き取りの数"""
    out = {b: {} for b in BANDS}
    sids = set()
    day = t0.date()
    while day <= t1.date():
        for path in sorted(glob.glob(os.path.join(BASE, 'pskr', f'{day:%Y}', f'{day:%m}', f'{day:%d}', '*.csv.gz'))):
            name = os.path.basename(path)[:4]
            try:
                ft = datetime(day.year, day.month, day.day, int(name[:2]), int(name[2:]), tzinfo=timezone.utc)
            except ValueError:
                continue
            if ft < t0 - timedelta(minutes=20) or ft > t1 + timedelta(minutes=20):
                continue
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                for r in csv.DictReader(f):
                    st = sample_time(r.get('sample_id', ''))
                    if not st or st < t0 or st >= t1:
                        continue
                    sids.add(r['sample_id'])
                    if r.get('dir') != 'RX' or r.get('band') not in out:
                        continue
                    # ローバンドは、日本側がはっきり昼（太陽高度 +5° 以上）の抜き取りの報告を、局数にかかわらず使わない
                    if r['band'] in LOW and day_sample(st):
                        continue
                    try:
                        i = adif.get(int(r['dx_dxcc']))
                    except (TypeError, ValueError):
                        continue
                    if i is None or i == JAPAN:
                        continue
                    x = out[r['band']].setdefault(i, {'s': set(), 'ja': 0, 'snr': -99.0, 'areas': set(), 'last': st, 'times': set()})
                    x['s'].add(r['sample_id'])
                    x['times'].add(st)
                    x['areas'].add(r.get('ja_area'))
                    if st > x['last']:
                        x['last'] = st
                    try:
                        x['ja'] = max(x['ja'], int(r.get('n_ja') or 0))
                    except ValueError:
                        pass
                    try:
                        x['snr'] = max(x['snr'], float(r.get('snr_p90') or -99))
                    except ValueError:
                        pass
        day += timedelta(days=1)
    return out, len(sids)

# ---- 太陽の高さと経路の暗さ（1局だけの報告を、物理の条件で確かめるため）
R = math.pi / 180
def sun_elev(la, lo, t):
    d = (t - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400
    g = (357.529 + 0.98560028 * d) * R; q = 280.459 + 0.98564736 * d
    L = (q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) * R; e = (23.439 - 0.00000036 * d) * R
    ra = math.atan2(math.cos(e) * math.sin(L), math.cos(L)); dec = math.asin(math.sin(e) * math.sin(L))
    gm = (18.697374558 + 24.06570982441908 * d) % 24; ha = (gm * 15 + lo) * R - ra
    return math.asin(math.sin(la * R) * math.sin(dec) + math.cos(la * R) * math.cos(dec) * math.cos(ha)) / R
def dark_frac(b, t, long_path=False, n=60):
    # 日本（JA_POS）から相手の代表位置までの短経路・長経路のうち、太陽高度 −6° 以下の割合
    la1, lo1, la2, lo2 = JA_POS[0] * R, JA_POS[1] * R, b[0] * R, b[1] * R
    p1 = (math.cos(la1) * math.cos(lo1), math.cos(la1) * math.sin(lo1), math.sin(la1))
    p2 = (math.cos(la2) * math.cos(lo2), math.cos(la2) * math.sin(lo2), math.sin(la2))
    dot = max(-1.0, min(1.0, sum(u * v for u, v in zip(p1, p2)))); ang = math.acos(dot)
    w = [v - dot * u for u, v in zip(p1, p2)]; nw = math.sqrt(sum(c * c for c in w)) or 1.0; w = [c / nw for c in w]
    span = -(2 * math.pi - ang) if long_path else ang
    dk = 0
    for k in range(n + 1):
        a_ = span * k / n
        p = [math.cos(a_) * u + math.sin(a_) * v for u, v in zip(p1, w)]
        if sun_elev(math.asin(p[2]) / R, math.atan2(p[1], p[0]) / R, t) < -6:
            dk += 1
    return dk / (n + 1)

# ---- DXクラスターの日本の局のスポット（cluster_collect.py の記録。ent は cty.json の番号）
_SPOTS = None
def spots():
    global _SPOTS
    if _SPOTS is None:
        _SPOTS = {}
        for path in sorted(glob.glob(os.path.join(BASE, 'cluster', '*.csv')))[-2:]:
            try:
                with open(path, encoding='utf-8') as f:
                    for r in csv.DictReader(f):
                        try:
                            t = datetime.strptime(r['time_utc'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
                            _SPOTS.setdefault((r['band'], int(r['ent'])), []).append(t)
                        except (KeyError, ValueError):
                            continue
            except OSError:
                continue
    return _SPOTS

def spotted(x, band, ent):
    # 同じエンティティ×バンドを、日本の局が DXクラスターでスポットしていて、抜き取りの前後20分に入っていれば True
    if band is None or ent is None:
        return False
    ts = spots().get((band, ent))
    if not ts:
        return False
    for st in x.get('times', ()):
        lo, hi = st - timedelta(minutes=20), st + timedelta(minutes=28)
        if any(lo <= t <= hi for t in ts):
            return True
    return False

def credible(x, band=None, ent=None):
    # DXクラスターのスポットと PSK の受信が同じ時間帯にそろったものは、1局1回でも採る（人の確認と機械の受信が互いを裏付けるため）
    if spotted(x, band, ent):
        x['spot'] = True
        return True
    # 日本の局2局以上、または2つ以上のコールエリアから報告されたものは載せる
    if x['ja'] >= 2 or len(x['areas']) >= 2:
        return True
    # 1局だけの報告：抜き取り3回以上。ローバンドでは、短経路か長経路の半分以上が夜であることも条件にする
    if len(x['s']) < 3:
        return False
    if band in LOW and CTY and ent is not None and ent < len(CTY['ent']):
        e = CTY['ent'][ent]
        return max(dark_frac((e[1], e[2]), x['last']), dark_frac((e[1], e[2]), x['last'], True)) >= 0.5
    return True

# ---- 直近3時間
t_from = now - timedelta(hours=3)
rx, nsamp = read_rx(t_from, now)
recent = {}
for b in BANDS:
    cand = []
    for i, x in rx[b].items():
        if not credible(x, b, i):
            continue
        name, km = ent_info(i)
        if name is None:
            continue
        rs = rarity(i, b)
        cand.append({'e': i, 'name': name, 'km': km, 's': len(x['s']), 'ja': x['ja'],
                     'snr': None if x['snr'] <= -99 else round(x['snr']), 'rare': rs <= RARE_MAX, 'rs': rs,
                     'spot': bool(x.get('spot'))})
    if not cand:
        continue
    cand.sort(key=lambda c: (not c['rare'], c['rs'] if c['rare'] else 0, -c['km']))
    recent[b] = {'n': len(cand), 'top': [{k: v for k, v in c.items() if k != 'rs'} for c in cand[:TOP]],
                 'all': sorted(c['e'] for c in cand)}   # 英語版で、訪れた局の国が受信されていたかを調べるため

# ---- これから3時間の見通し（過去の記録の部分）
h0 = (now.hour + (1 if now.minute >= 30 else 0)) % 24
hours = [(h0 + k) % 24 for k in range(3)]
outlook = {}
if SUM:
    for b in BANDS:
        cand = []
        for k, ed in SUM.get('e', {}).items():
            d = ed.get(b)
            if not d or 'r' not in d:
                continue
            i = int(k)
            if i == JAPAN:
                continue
            rate = max(rdig(d['r'][h]) for h in hours)
            days = max(int(d['wd'][h], 36) if 'wd' in d else 0 for h in hours)
            if rate < OUT_RATE or days < OUT_DAYS:
                continue
            name, km = ent_info(i)
            if name is None:
                continue
            rs = rarity(i, b)
            cand.append({'e': i, 'name': name, 'km': km, 'rate': rate * 10, 'days': days, 'rare': rs <= 24, 'rs': rs})
        if not cand:
            continue
        # 見通しで受信されやすいものは、まれ（合計4以下）にはまずならないので、合計の小さい順（比較的珍しいもの）に並べる
        cand.sort(key=lambda c: (c['rs'], -c['km']))
        outlook[b] = {'n': len(cand), 'top': [{k: v for k, v in c.items() if k not in ('rs', 'rare')} for c in cand[:TOP]]}

# ---- 採点：3時間前までに出した見通しを、その後の3時間の実際の受信と突き合わせる
hist = load_json(HIST, [])
score = load_json(SCORE, {})
keep = []
for h in hist:
    t_issue = datetime.fromisoformat(h['t'])
    if now < t_issue + timedelta(hours=3, minutes=10):
        keep.append(h)
        continue
    act, n_after = read_rx(t_issue, t_issue + timedelta(hours=3))
    if n_after < 5:                      # 抜き取りが少なすぎる窓は採点しない
        continue
    day = t_issue.strftime('%Y-%m-%d')
    sd = score.setdefault(day, {})
    for b in BANDS:
        heard = {i for i, x in act[b].items() if credible(x, b, i)}
        o = h.get('o', {}).get(b, [])
        p = h.get('p', {}).get(b, [])
        if not o and not p:
            continue
        s = sd.setdefault(b, [0, 0, 0, 0])   # [見通しの数, 当たり, 持続の数, 当たり]
        s[0] += len(o); s[1] += sum(1 for i in o if i in heard)
        s[2] += len(p); s[3] += sum(1 for i in p if i in heard)
# 毎時最初の抜き取りで、新しい見通しを記録する
if now.minute < 20:
    keep.append({'t': now.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                 'o': {b: [c['e'] for c in v['top']] for b, v in outlook.items()},
                 'p': {b: [c['e'] for c in v['top']] for b, v in recent.items()}})
keep = [h for h in keep if datetime.fromisoformat(h['t']) > now - timedelta(days=1)]
cut = (now - timedelta(days=SCORE_DAYS)).strftime('%Y-%m-%d')
score = {d: v for d, v in sorted(score.items()) if d >= cut}

# 直近7日間の採点の合計
week = {}
cut7 = (now - timedelta(days=7)).strftime('%Y-%m-%d')
for d, v in score.items():
    if d < cut7:
        continue
    for b, s in v.items():
        w = week.setdefault(b, [0, 0, 0, 0])
        for k in range(4):
            w[k] += s[k]

out = {
    'generated': now.strftime('%Y-%m-%d %H:%MZ'),
    'note': '日本の局が受信できた相手（PSK Reporter、20分ごとに8分間の抜き取り）。日本全体の記録で、個々の局で聞こえるとは限らない。',
    'recent': {'from': t_from.strftime('%H:%MZ'), 'to': now.strftime('%H:%MZ'), 'samples': nsamp, 'bands': recent},
    'outlook': {'hours': hours, 'days': SUM.get('days') if SUM else None, 'bands': outlook},
    'score7': week,
}
os.makedirs(SUMDIR, exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
with open(HIST, 'w', encoding='utf-8') as f:
    json.dump(keep, f, separators=(',', ':'))
with open(SCORE, 'w', encoding='utf-8') as f:
    json.dump(score, f, separators=(',', ':'))
print(f"直近3時間：抜き取り{nsamp}回、{sum(v['n'] for v in recent.values())} エンティティ×バンド。見通し：{len(outlook)}バンド。採点の窓：{len(keep)}")
