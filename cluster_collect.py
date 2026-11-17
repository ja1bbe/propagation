# -*- coding: utf-8 -*-
"""
DX Summit の最新のスポットから、日本の局がスポットしたものだけを取り出して記録する。
コメントに「LP（ロングパス）」「SP（ショートパス）」「skew（ずれた方向）」の記載があれば、その印も残す。
SP/LP の助言の答え合わせ（人が実際にアンテナを向けて確かめた情報）に使う。

保存するもの（コールサインは保存しない）
  data/cluster/YYYY-MM.csv … time_utc（分まで）、band、ent（cty.json の並び順の番号）、ja_area（スポットした局のコールエリア）、
                              path（LP / SP / SKEW / 空）、mode（CW / FT8 / SSB など。コメントや周波数から推定、不明は空）
  data/cluster/seen.txt     … 同じスポットを二重に数えないための印（スポットの内容から作った短いハッシュ。直近2日分）

  python cluster_collect.py
"""
import csv, hashlib, json, os, re, sys, urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.environ.get('RESEARCH_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
OUT = os.path.join(BASE, 'cluster')
URL = os.environ.get('DXSUMMIT_URL', 'https://www.dxsummit.fi/api/v1/spots?limit=500')
CTY_URL = 'https://raw.githubusercontent.com/ja1bbe/ja1bbe-iono/main/docs/cty.json'
UA = 'ja1bbe-propagation/1.0 (amateur radio research; https://github.com/ja1bbe/propagation)'
UTC = timezone.utc

BANDS = [(1800, 2000, '160m'), (3500, 3800, '80m'), (5300, 5410, '60m'), (7000, 7300, '40m'), (10100, 10150, '30m'),
         (14000, 14350, '20m'), (18068, 18168, '17m'), (21000, 21450, '15m'), (24890, 24990, '12m'), (28000, 29700, '10m'),
         (50000, 54000, '6m')]
# LP・SP・skew の記載（大文字にしたコメントに対して。SPLIT などを拾わないよう語の切れ目を見る）
PAT = [('LP', re.compile(r'(?<![A-Z0-9])(L/?P|LONG ?PATH)(?![A-Z0-9])')),
       ('SP', re.compile(r'(?<![A-Z0-9])(S/?P|SHORT ?PATH)(?![A-Z0-9])')),
       ('SKEW', re.compile(r'SKEW'))]
FT8_DIALS = [1840, 3573, 7074, 10136, 14074, 18100, 21074, 24915, 28074, 50313]


def log(m):
    print(datetime.now(UTC).strftime('%H:%M:%S'), m, flush=True)


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def pick(d, *keys):
    for k in keys:
        if d.get(k) not in (None, ''):
            return d[k]
    return None


def to_khz(f):
    try:
        x = float(f)
    except (TypeError, ValueError):
        return None
    return x * 1000 if x < 100 else x / 1000 if x > 1_000_000 else x


def parse_time(v):
    if v in (None, ''):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, UTC)
    s = str(v).strip().replace('Z', '+00:00')
    for f in (None, '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S'):
        try:
            t = datetime.fromisoformat(s) if f is None else datetime.strptime(s, f)
            return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC)
        except ValueError:
            pass
    return None


class Cty:
    """cty.json（ページと同じエンティティの表）でコールサインのエンティティを判定する"""
    SUF = {'P', 'M', 'MM', 'AM', 'QRP', 'QRPP', 'A', 'LH', 'B', 'J', 'R', 'T', 'X', 'N'}

    def __init__(self, d):
        self.ent, self.pre, self.ex = d['ent'], d['prefix'], d.get('exact', {})

    def find(self, call):
        call = (call or '').upper().strip()
        if not call:
            return None
        if call in self.ex:
            return self.ex[call]
        parts = [p for p in call.split('/') if p and p not in self.SUF]
        if not parts:
            return None
        # 「VP2V/K1ABC」「K1ABC/VP9」のような形は、短いほう（プレフィックスらしいほう）を優先する
        cand = sorted(parts, key=len) if len(parts) > 1 else parts
        for p in cand:
            for k in range(len(p), 0, -1):
                if p[:k] in self.pre:
                    return self.pre[p[:k]]
        return None


def band_of(khz):
    for lo, hi, b in BANDS:
        if lo <= khz <= hi:
            return b
    return None


def mode_of(khz, comment):
    c = comment.upper()
    for m in ('FT8', 'FT4', 'CW', 'SSB', 'RTTY'):
        if m in c:
            return m
    return 'FT8' if any(0 <= khz - d <= 3.0 for d in FT8_DIALS) else ''


def main():
    os.makedirs(OUT, exist_ok=True)
    cty = Cty(json.loads(get(CTY_URL)))
    japan = cty.find('JA1AA')
    data = json.loads(get(URL))
    if isinstance(data, dict):
        data = pick(data, 'spots', 'data', 'items') or []

    seen_path = os.path.join(OUT, 'seen.txt')
    keep_after = datetime.now(UTC) - timedelta(days=2)
    seen = {}
    if os.path.exists(seen_path):
        for line in open(seen_path, encoding='utf-8'):
            h, _, t = line.strip().partition(' ')
            try:
                if datetime.fromisoformat(t) >= keep_after:
                    seen[h] = t
            except ValueError:
                pass

    rows, total, ja = [], 0, 0
    for d in data:
        dx, de = pick(d, 'dx_call', 'dxcall', 'dx', 'callsign'), pick(d, 'de_call', 'decall', 'spotter', 'de')
        khz, t = to_khz(pick(d, 'frequency', 'freq', 'qrg')), parse_time(pick(d, 'time', 'timestamp', 'spot_time', 'datetime'))
        com = str(pick(d, 'info', 'comment', 'comments') or '')
        if not (dx and de and khz and t):
            continue
        total += 1
        if cty.find(de) != japan:          # 日本の局がスポットしたものだけ
            continue
        ja += 1
        band, ent = band_of(khz), cty.find(dx)
        if not band or ent is None or ent == japan:
            continue
        h = hashlib.sha1(f'{t:%Y%m%d%H%M}|{dx}|{de}|{khz:.1f}'.encode()).hexdigest()[:16]
        if h in seen:
            continue
        seen[h] = t.isoformat()
        cu = com.upper()
        path = next((name for name, p in PAT if p.search(cu)), '')
        m = re.search(r'\d', de.split('/')[0])
        rows.append({'time_utc': t.strftime('%Y-%m-%d %H:%M'), 'band': band, 'ent': ent,
                     'ja_area': m.group(0) if m else '', 'path': path, 'mode': mode_of(khz, com)})

    by_month = {}
    for r in rows:
        by_month.setdefault(r['time_utc'][:7], []).append(r)
    for mo, rs in by_month.items():
        p = os.path.join(OUT, f'{mo}.csv')
        new = not os.path.exists(p)
        with open(p, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['time_utc', 'band', 'ent', 'ja_area', 'path', 'mode'])
            if new:
                w.writeheader()
            w.writerows(sorted(rs, key=lambda r: r['time_utc']))
    with open(seen_path, 'w', encoding='utf-8') as f:
        for h, t in seen.items():
            f.write(f'{h} {t}\n')
    log(f'DX Summit：スポット {total}件のうち日本の局 {ja}件、新しく記録 {len(rows)}件（経路の記載あり {sum(1 for r in rows if r["path"])}件）')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:      # クラスターの取得に失敗しても、伝搬データの収集は止めない
        log(f'DX Summit の取得に失敗しました（収集は続けます）：{e}')
        sys.exit(0)
