# -*- coding: utf-8 -*-
"""
GFZ（ドイツ地球科学研究センター）の地磁気・太陽指数を取り込む

  毎日   : 直近30日の Kp・ap・Ap・F10.7（速報と確定の混合）を取り込み、年ごとのCSVを更新する
  月1回  : 1932年からの全期間を取り込み直して、確定値への置き換えを反映する
           あわせて国際静穏日・擾乱日（Q-days・D-days）の年ごとのファイルを保存する
  初回   : 保存済みのデータがなければ、全期間と静穏日・擾乱日を取り込む

保存先（data の下）
  gfz/kp/YYYY.csv           … 1日1行。日付、Bartels自転番号と日目、Kp 8個、ap 8個、Ap、F10.7（観測値・調整値）、確定の印
  gfz/qd/QD_daysYYYY.txt    … 国際静穏日・擾乱日（GFZ配布のまま）

黒点数（SN）は利用条件が CC BY-NC 4.0 で他と異なるため保存しない。
太陽活動の指標には F10.7 の観測値（F10.7obs）を使う（GFZ の説明でも電離層研究に推奨）。

  python gfz_collect.py              # 自動（初回と毎月3日は全期間、それ以外は直近30日）
  python gfz_collect.py --mode full  # 全期間と静穏日・擾乱日を取り込み直す
"""
import argparse, csv, io, os, re, sys, time, urllib.request
from datetime import datetime, timezone

BASE = os.environ.get('RESEARCH_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
KP_DIR = os.path.join(BASE, 'gfz', 'kp')
QD_DIR = os.path.join(BASE, 'gfz', 'qd')

URL_NOWCAST = 'https://kp.gfz.de/fileadmin/files_for_gfz_cms/Kp_ap_Ap_SN_F107_nowcast.txt'
URL_FULL = 'https://kp.gfz.de/fileadmin/files_for_gfz_cms/Kp_ap_Ap_SN_F107_since_1932.txt'
URL_QD = 'https://datapub.gfz.de/download/10.5880.Kp.0001/Quiet_Disturbed_Days/'
UA = 'ja1bbe-propagation/1.0 (amateur radio research; https://github.com/ja1bbe/propagation)'

# ファイル側の列名 → 保存する列名（SN は保存しない）
COLS = [('YYYY', None), ('MM', None), ('DD', None), ('Bsr', 'bsr'), ('dB', 'bsr_day')] \
     + [(f'Kp{i}', f'kp{i}') for i in range(1, 9)] + [(f'ap{i}', f'ap{i}') for i in range(1, 9)] \
     + [('Ap', 'Ap'), ('F10.7obs', 'f107obs'), ('F10.7adj', 'f107adj'), ('D', 'D')]
INT_COLS = {'bsr', 'bsr_day', 'Ap', 'D'} | {f'ap{i}' for i in range(1, 9)}
FIELDS = ['date'] + [s for _, s in COLS if s]


def log(msg):
    print(datetime.now(timezone.utc).strftime('%H:%M:%S'), msg, flush=True)


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:
            log(f'取得に失敗（{i + 1}回目）: {url} … {e}')
            time.sleep(5 * (i + 1))
    raise RuntimeError(f'取得できませんでした: {url}')


def parse_kp(text):
    """見出しの列名で位置を決めて読む。列名が想定と違えば止める（黙って誤読しないため）"""
    names = None
    rows = []
    for line in text.splitlines():
        if line.startswith('#'):
            if line.startswith('#YYY'):
                names = line[1:].split()
                names[0] = 'YYYY'
            continue
        if not line.strip():
            continue
        if names is None:
            raise ValueError('列の見出し（#YYY で始まる行）が見つかりません。ファイルの形式が変わった可能性があります')
        v = line.split()
        if len(v) != len(names):
            raise ValueError(f'列の数が見出しと合いません（{len(v)} と {len(names)}）: {line[:60]}')
        rec = dict(zip(names, v))
        missing = [n for n, _ in COLS if n not in rec]
        if missing:
            raise ValueError(f'想定した列がありません: {missing}')
        row = {'date': f"{rec['YYYY']}-{rec['MM']}-{rec['DD']}"}
        for n, s in COLS:
            if s:
                row[s] = str(int(rec[n])) if s in INT_COLS else rec[n]
        rows.append(row)
    if not rows:
        raise ValueError('データの行がありません')
    return rows


def read_year(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline='', encoding='utf-8') as f:
        return {r['date']: r for r in csv.DictReader(f)}


def write_year(path, rows_by_date):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator='\n')
    w.writeheader()
    for d in sorted(rows_by_date):
        w.writerow(rows_by_date[d])
    new = buf.getvalue()
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            if f.read() == new:
                return False
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(new)
    return True


def store(rows, replace_years):
    """rows を年ごとのCSVに反映する。replace_years=True なら、その年の内容を丸ごと置き換える"""
    by_year = {}
    for r in rows:
        by_year.setdefault(r['date'][:4], []).append(r)
    changed = 0
    for y, rs in sorted(by_year.items()):
        path = os.path.join(KP_DIR, f'{y}.csv')
        cur = read_year(path)
        if replace_years:
            # 全期間ファイルの最終日より後の行（直近の取り込み分）だけ残し、それ以外は置き換える
            last = max(r['date'] for r in rs)
            cur = {d: r for d, r in cur.items() if d > last}
        for r in rs:
            cur[r['date']] = r
        if write_year(path, cur):
            changed += 1
    return changed, len(by_year)


def update_qd():
    html = fetch(URL_QD).decode('utf-8', 'replace')
    names = sorted(set(re.findall(r'href="(QD_days\d{4}\.txt)"', html)))
    if not names:
        raise ValueError('静穏日・擾乱日のファイルが一覧に見つかりません。ページの形式が変わった可能性があります')
    os.makedirs(QD_DIR, exist_ok=True)
    changed = 0
    for n in names:
        data = fetch(URL_QD + n)
        path = os.path.join(QD_DIR, n)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                if f.read() == data:
                    continue
        with open(path, 'wb') as f:
            f.write(data)
        changed += 1
        time.sleep(0.3)                      # 相手のサーバーに負担をかけないよう間をあける
    log(f'静穏日・擾乱日: {len(names)}年分のうち {changed}件を更新')


ap = argparse.ArgumentParser()
ap.add_argument('--mode', choices=['auto', 'daily', 'full'], default='auto')
a = ap.parse_args()

mode = a.mode
if mode == 'auto':
    first = not (os.path.isdir(KP_DIR) and os.listdir(KP_DIR))
    mode = 'full' if first or datetime.now(timezone.utc).day == 3 else 'daily'

try:
    if mode == 'full':
        rows = parse_kp(fetch(URL_FULL).decode('utf-8', 'replace'))
        c, n = store(rows, replace_years=True)
        log(f'全期間: {rows[0]["date"]}〜{rows[-1]["date"]}（{len(rows)}日分）、{n}年のうち {c}年のファイルを更新')
        update_qd()
    else:
        rows = parse_kp(fetch(URL_NOWCAST).decode('utf-8', 'replace'))
        c, n = store(rows, replace_years=False)
        log(f'直近: {rows[0]["date"]}〜{rows[-1]["date"]}（{len(rows)}日分）、{c}年分のファイルを更新')
except Exception as e:
    log(f'失敗しました: {e}')
    sys.exit(1)
