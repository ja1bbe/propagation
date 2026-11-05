# -*- coding: utf-8 -*-
"""
GitHub Actions 用の「抜き取り」収集（予備実験）

毎時1回起動し、PSK Reporter の配信を --minutes 分だけ受け取って集計し、
宇宙天気（NOAA SWPC）の終わった時の値とあわせて data/ に保存する。
抜き取りの時間は data/samples.csv に記録する（後で「1分あたりの報告数」にそろえるため）。

  python sample_collect.py --minutes 8
"""
import argparse, os, threading, time
from datetime import datetime, timezone

os.environ.setdefault('RESEARCH_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
import pskr_collect as P

ap = argparse.ArgumentParser()
ap.add_argument('--minutes', type=float, default=8)
a = ap.parse_args()

stop = threading.Event()
agg = P.Agg()
t0 = time.time()
th = threading.Thread(target=P.run_mqtt, args=(agg, stop), daemon=True)
th.start()
stop.wait(a.minutes * 60)
stop.set()
th.join(timeout=15)
hour = agg.hour
agg.flush()
sec = round(time.time() - t0)
P._append(os.path.join(P.BASE, 'samples.csv'), ['hour_utc', 'start_utc', 'seconds', 'reports'],
          [{'hour_utc': hour or datetime.fromtimestamp(t0, timezone.utc).strftime('%Y-%m-%d %H:00'),
            'start_utc': datetime.fromtimestamp(t0, timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            'seconds': sec, 'reports': agg.total}])
P.log(f'抜き取り {sec}秒、報告 {agg.total}件')

# 宇宙天気（1回だけ取得して、まだ保存していない終わった時を書き足す）
P.SpaceWx().fetch_once()
