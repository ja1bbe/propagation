# -*- coding: utf-8 -*-
"""
GitHub Actions 用の「抜き取り」収集（予備実験）  第2版 2026-11-06

毎時1回起動し、PSK Reporter の配信を --minutes 分だけ受け取って集計し、
宇宙天気（NOAA SWPC）の終わった時の値とあわせて data/ に保存する。

第2版の変更点（第1版のデータはそのまま残す）
  ・1回の抜き取りを1つの単位として保存する。報告時刻で時間別に振り分けないので、
    抜き取りが時の境目をまたいでも、件数が2つの時間に分かれたり、
    遅れて届いた報告が捨てられたりしない。
  ・受け取った時間（最初の報告が届いてから止めるまでの秒数）を記録し、
    「1分あたりの報告数」にそろえられるようにする。
  ・どの時のデータとして扱うかは、抜き取りの中央の時刻（UTC）で決める。
    同じ時に2回抜き取った場合も、別々の行として残る。

保存先（data の下）
  pskr/YYYY/MM/DD/HHMM.csv.gz     … 抜き取り1回分の集計（HHMM は開始時刻。第1版は HH.csv.gz）
  pskr_rx/YYYY/MM/DD/HHMM.csv.gz  … 同じ抜き取りでの、バンドごとの日本の受信局の数
  samples_v2.csv                  … 抜き取りの開始・中央・終了時刻、受け取った秒数、報告数
  spacewx/YYYY-MM.csv             … 宇宙天気（第1版と同じ）

  python sample_collect.py --minutes 8
"""
import argparse, os, statistics, threading, time
from collections import defaultdict
from datetime import datetime, timezone

os.environ.setdefault('RESEARCH_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
import pskr_collect as P

SAMPLE_FIELDS = ['sample_id', 'hour_utc', 'dir', 'band', 'mode', 'ja_area', 'dx_dxcc', 'dx_field',
                 'n_reports', 'n_dx', 'n_ja', 'snr_med', 'snr_p90']
SAMPLE_RX_FIELDS = ['sample_id', 'hour_utc', 'band', 'n_ja_rx']
LOG_FIELDS = ['sample_id', 'hour_utc', 'start_utc', 'mid_utc', 'end_utc', 'listen_seconds',
              'reports', 'report_t_min_utc', 'report_t_max_utc', 'version']
VERSION = 2


def utc(ts, fmt='%Y-%m-%d %H:%M:%S'):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(fmt)


class SampleAgg:
    """抜き取り1回分をまとめて数える（報告時刻による時間の振り分けはしない）"""

    def __init__(self):
        self.lock = threading.Lock()
        self.cells = defaultdict(lambda: {'n': 0, 'dx': set(), 'ja': set(), 'snr': []})
        self.rx_ja = defaultdict(set)
        self.total = 0
        self.first_arrival = None        # 最初の報告が届いた時刻（受け取りの開始とみなす）
        self.t_min = self.t_max = None   # 報告に付いている時刻の範囲（確認用）
        self.closed = False

    def add(self, m):
        now = time.time()
        try:
            t = int(m.get('t') or now)
            band = (m.get('b') or '').lower()
            mode = (m.get('md') or '').upper()
            sa, ra = str(m.get('sa', '')), str(m.get('ra', ''))
            if ra == P.JAPAN and sa != P.JAPAN:
                d, ja, dx, dxc, dxl = 'RX', m.get('rc'), m.get('sc'), sa, m.get('sl')
            elif sa == P.JAPAN and ra != P.JAPAN:
                d, ja, dx, dxc, dxl = 'TX', m.get('sc'), m.get('rc'), ra, m.get('rl')
            else:
                return                                   # 国内どうしは対象外
            snr = m.get('rp')
        except (TypeError, ValueError):
            return
        with self.lock:
            if self.closed:
                return                                   # 止めた後に届いた報告は数えない
            if self.first_arrival is None:
                self.first_arrival = now
            self.t_min = t if self.t_min is None else min(self.t_min, t)
            self.t_max = t if self.t_max is None else max(self.t_max, t)
            c = self.cells[(d, band, mode, P.ja_area(ja), dxc, (dxl or '')[:2].upper())]
            c['n'] += 1
            c['dx'].add((dx or '').upper())
            c['ja'].add((ja or '').upper())
            if isinstance(snr, (int, float)):
                c['snr'].append(snr)
            if d == 'RX':
                self.rx_ja[band].add((ja or '').upper())
            self.total += 1

    def close(self):
        with self.lock:
            self.closed = True


ap = argparse.ArgumentParser()
ap.add_argument('--minutes', type=float, default=8, help='受け取る時間（最初の報告が届いてから数える）')
ap.add_argument('--wait', type=float, default=3, help='接続して最初の報告が届くまで待つ上限（分）')
a = ap.parse_args()

stop = threading.Event()
agg = SampleAgg()
t_launch = time.time()
th = threading.Thread(target=P.run_mqtt, args=(agg, stop), daemon=True)
th.start()

# 最初の報告が届くまで待つ（接続にかかる時間を受け取り時間に含めないため）
deadline = t_launch + a.wait * 60
while agg.first_arrival is None and time.time() < deadline and not stop.is_set():
    time.sleep(0.5)

if agg.first_arrival is None:
    stop.set()
    th.join(timeout=15)
    P.log(f'{a.wait:g}分待っても報告が届かなかったので、今回の抜き取りは記録しません')
else:
    t_start = agg.first_arrival
    stop.wait(max(0, t_start + a.minutes * 60 - time.time()))
    agg.close()
    t_end = time.time()
    stop.set()
    th.join(timeout=15)

    t_mid = (t_start + t_end) / 2
    sid = utc(t_start, '%Y%m%d%H%M')                 # 抜き取りの名前（開始時刻）
    hour = utc(t_mid, '%Y-%m-%d %H:00')              # どの時として扱うか（中央の時刻）
    y, mo, dd, hm = utc(t_start, '%Y'), utc(t_start, '%m'), utc(t_start, '%d'), utc(t_start, '%H%M')

    rows = []
    for (d, band, mode, area, dxc, dxf), c in agg.cells.items():
        s = sorted(c['snr'])
        rows.append({'sample_id': sid, 'hour_utc': hour, 'dir': d, 'band': band, 'mode': mode,
                     'ja_area': area, 'dx_dxcc': dxc, 'dx_field': dxf, 'n_reports': c['n'],
                     'n_dx': len(c['dx']), 'n_ja': len(c['ja']),
                     'snr_med': statistics.median(s) if s else '',
                     'snr_p90': s[int(0.9 * (len(s) - 1))] if s else ''})
    P._append_gz(os.path.join(P.BASE, 'pskr', y, mo, dd, f'{hm}.csv.gz'), SAMPLE_FIELDS, rows)
    P._append_gz(os.path.join(P.BASE, 'pskr_rx', y, mo, dd, f'{hm}.csv.gz'), SAMPLE_RX_FIELDS,
                 [{'sample_id': sid, 'hour_utc': hour, 'band': b, 'n_ja_rx': len(v)}
                  for b, v in agg.rx_ja.items()])

    sec = round(t_end - t_start)
    P._append(os.path.join(P.BASE, 'samples_v2.csv'), LOG_FIELDS,
              [{'sample_id': sid, 'hour_utc': hour, 'start_utc': utc(t_start), 'mid_utc': utc(t_mid),
                'end_utc': utc(t_end), 'listen_seconds': sec, 'reports': agg.total,
                'report_t_min_utc': utc(agg.t_min) if agg.t_min else '',
                'report_t_max_utc': utc(agg.t_max) if agg.t_max else '', 'version': VERSION}])
    P.log(f'抜き取り {sid}（{hour} UTC として記録）: {sec}秒、報告 {agg.total}件、{len(rows)}組')

# 宇宙天気（1回だけ取得して、まだ保存していない終わった時を書き足す）
P.SpaceWx().fetch_once()
