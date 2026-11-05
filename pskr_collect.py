# -*- coding: utf-8 -*-
"""
PSK Reporter（日本の送受信）と宇宙天気を集めて、1時間ごとにまとめて保存する（研究用）

集めるもの
  1) PSK Reporter のリアルタイム配信（MQTT）から、
       RX: 日本の局が受信した報告（受信局の国 = 日本）
       TX: 日本の局が送信し、海外で受信された報告（送信局の国 = 日本）
     を受け取り、1時間ごとに
       向き・バンド・モード・日本側の地域（コールエリア）・相手のDXCC・相手のグリッドの大区画
     の組ごとに、報告数・相手局の数・日本側の局の数・SNRの中央値と上位10%を数える。
     あわせて、バンドごとに「その1時間に報告した日本の受信局の数」（正規化の基準）も数える。
  2) NOAA SWPC から宇宙天気（Kp、太陽風の Bz・Bt・速度・密度、プロトン、X線）を15分ごとに取得し、
     1時間ごとの値にまとめる。

保存先（research の下。報告の集計は1時間1ファイルの圧縮CSV、宇宙天気は1か月1ファイルのCSV）
  research/pskr/YYYY/MM/DD/HH.csv.gz     … 報告の集計
  research/pskr_rx/YYYY/MM/DD/HH.csv.gz  … バンドごとの日本の受信局の数
  research/spacewx/YYYY-MM.csv           … 宇宙天気（1時間ごと。後から届いた値で埋め直す）

使い方
  python -m pip install paho-mqtt
  python pskr_collect.py          （止めるときは Ctrl+C。まとめ途中の1時間分も保存してから終わります）

  24時間動かし続ける必要があるので、常時電源の入っているPCで動かしてください。
  PSK Reporter・NOAA のデータは非営利の研究に使い、取得の頻度は控えめにしています。
"""
import csv, gzip, io, json, os, signal, statistics, threading, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

BASE = os.environ.get('RESEARCH_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research')
JAPAN = '339'                      # ADIF の国番号（日本）
BROKER, PORT = 'mqtt.pskreporter.info', 1883
TOPICS = [f'pskr/filter/v2/+/+/+/+/+/+/+/{JAPAN}',      # 受信局が日本（RX）
          f'pskr/filter/v2/+/+/+/+/+/+/{JAPAN}/+']      # 送信局が日本（TX）

SWPC = 'https://services.swpc.noaa.gov'
SW_URLS = {
    'kp': SWPC + '/products/noaa-planetary-k-index.json',
    'mag': SWPC + '/json/rtsw/rtsw_mag_1m.json',          # リアルタイム太陽風（磁場）
    'plasma': SWPC + '/json/rtsw/rtsw_wind_1m.json',      # リアルタイム太陽風（速度・密度）
    'proton': SWPC + '/json/goes/primary/integral-protons-1-day.json',
    'xray': SWPC + '/json/goes/primary/xrays-1-day.json',
}
SW_INTERVAL = 15 * 60
PSKR_FIELDS = ['hour_utc', 'dir', 'band', 'mode', 'ja_area', 'dx_dxcc', 'dx_field',
               'n_reports', 'n_dx', 'n_ja', 'snr_med', 'snr_p90']
RX_FIELDS = ['hour_utc', 'band', 'n_ja_rx']
SW_FIELDS = ['hour_utc', 'kp', 'bz_mean', 'bz_min', 'bt_mean', 'speed_mean', 'density_mean',
             'proton10_max', 'xray_max']


def log(msg):
    print(time.strftime('%H:%M:%S'), msg, flush=True)


def ja_area(call):
    """日本の局のコールエリア（JA1BBE → 1、7J1ABC → 1、JD1 は 'JD1' として別扱い）"""
    c = (call or '').upper().split('/')[0]
    if c.startswith('JD1'):
        return 'JD1'
    for ch in c[1:4]:
        if ch.isdigit():
            return ch
    return '?'


# ---------------------------------------------------------------- PSK Reporter の集計
class Agg:
    def __init__(self):
        self.lock = threading.Lock()
        self.hour = None
        self._reset()
        self.total = 0

    def _reset(self):
        self.cells = defaultdict(lambda: {'n': 0, 'dx': set(), 'ja': set(), 'snr': []})
        self.rx_ja = defaultdict(set)

    def add(self, m):
        try:
            t = int(m.get('t') or time.time())
            hour = datetime.fromtimestamp(t - t % 3600, timezone.utc).strftime('%Y-%m-%d %H:00')
            band = (m.get('b') or '').lower()
            mode = (m.get('md') or '').upper()
            sa, ra = str(m.get('sa', '')), str(m.get('ra', ''))
            if ra == JAPAN and sa != JAPAN:
                d, ja, dx, dxc, dxl = 'RX', m.get('rc'), m.get('sc'), sa, m.get('sl')
            elif sa == JAPAN and ra != JAPAN:
                d, ja, dx, dxc, dxl = 'TX', m.get('sc'), m.get('rc'), ra, m.get('rl')
            else:
                return                                   # 国内どうしは対象外
            snr = m.get('rp')
        except (TypeError, ValueError):
            return
        with self.lock:
            if self.hour and hour > self.hour:
                self._flush_locked()
            if self.hour is None or hour > self.hour:
                self.hour = hour
            if hour < self.hour:
                return                                   # 遅れて届いた前の時間の報告は捨てる
            key = (d, band, mode, ja_area(ja), dxc, (dxl or '')[:2].upper())
            c = self.cells[key]
            c['n'] += 1
            c['dx'].add((dx or '').upper())
            c['ja'].add((ja or '').upper())
            if isinstance(snr, (int, float)):
                c['snr'].append(snr)
            if d == 'RX':
                self.rx_ja[band].add((ja or '').upper())
            self.total += 1

    def flush(self):
        with self.lock:
            self._flush_locked()

    def _flush_locked(self):
        if not self.hour or not self.cells:
            return
        y, mo, dd, hh = self.hour[0:4], self.hour[5:7], self.hour[8:10], self.hour[11:13]
        rows = []
        for (d, band, mode, area, dxc, dxf), c in self.cells.items():
            s = sorted(c['snr'])
            rows.append({'hour_utc': self.hour, 'dir': d, 'band': band, 'mode': mode, 'ja_area': area,
                         'dx_dxcc': dxc, 'dx_field': dxf, 'n_reports': c['n'], 'n_dx': len(c['dx']),
                         'n_ja': len(c['ja']), 'snr_med': statistics.median(s) if s else '',
                         'snr_p90': s[int(0.9 * (len(s) - 1))] if s else ''})
        _append_gz(os.path.join(BASE, 'pskr', y, mo, dd, f'{hh}.csv.gz'), PSKR_FIELDS, rows)
        _append_gz(os.path.join(BASE, 'pskr_rx', y, mo, dd, f'{hh}.csv.gz'), RX_FIELDS,
                [{'hour_utc': self.hour, 'band': b, 'n_ja_rx': len(v)} for b, v in self.rx_ja.items()])
        log(f'[PSKR] {self.hour} UTC の集計を保存（報告 {sum(c["n"] for c in self.cells.values())}件、{len(rows)}組）')
        self._reset()


def _append(path, fields, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def _append_gz(path, fields, rows):
    """圧縮CSVに書く（同じ時のファイルが既にあれば、圧縮の塊を後ろに足す。gzipとして正しく読める）"""
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    if new:
        w.writeheader()
    w.writerows(rows)
    with gzip.open(path, 'at', encoding='utf-8', newline='') as f:
        f.write(buf.getvalue())


def _upsert(path, fields, rows, key='hour_utc'):
    """月ごとのCSVを、時をキーに書き換える（空欄は新しい値で埋め、値は新しいもので更新）"""
    old = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                old[r[key]] = r
    changed = 0
    for r in rows:
        cur = old.get(r[key], {k: '' for k in fields})
        new = {k: (r.get(k) if r.get(k) not in ('', None) else cur.get(k, '')) for k in fields}
        if new != cur:
            old[r[key]] = new
            changed += 1
    if not changed:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(old[k] for k in sorted(old))
    os.replace(tmp, path)
    return changed


def run_mqtt(agg, stop):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log('[PSKR] paho-mqtt がありません。python -m pip install paho-mqtt を実行してください')
        stop.set()
        return

    def on_connect(cl, userdata, flags, rc, props=None):
        log(f'[PSKR] 接続しました（{rc}）')
        for t in TOPICS:
            cl.subscribe(t)

    def on_message(cl, userdata, msg):
        try:
            agg.add(json.loads(msg.payload))
        except ValueError:
            pass

    try:
        cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='', clean_session=True)
    except AttributeError:                                   # paho-mqtt 1.x
        cl = mqtt.Client()
    cl.on_connect, cl.on_message = on_connect, on_message
    cl.reconnect_delay_set(min_delay=5, max_delay=300)
    while not stop.is_set():
        try:
            cl.connect(BROKER, PORT, keepalive=60)
            cl.loop_start()
            while not stop.is_set():
                time.sleep(1)
            cl.loop_stop()
            cl.disconnect()
        except Exception as e:
            log(f'[PSKR] 接続できませんでした: {e}（60秒後に再試行）')
            stop.wait(60)


# ---------------------------------------------------------------- 宇宙天気
def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'JA1BBE research collector'})
    ctx = None
    try:
        import ssl, truststore
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        return json.loads(r.read().decode('utf-8'))


def _rows(data):
    """NOAA の JSON（先頭が見出しの表、または辞書のリスト）を辞書のリストにそろえる"""
    if data and isinstance(data[0], list):
        head = data[0]
        return [dict(zip(head, r)) for r in data[1:]]
    return data or []


def _hour(ts):
    ts = str(ts).replace('T', ' ').replace('Z', '')[:16]
    return ts[:13] + ':00'


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def spacewx_hours(raw):
    """各データを1時間ごとにまとめる → {時: {項目: 値}}"""
    out = defaultdict(dict)

    def put(rows, val_key, name, how, filt=None):
        g = defaultdict(list)
        for r in rows:
            if filt and not filt(r):
                continue
            v = _num(r.get(val_key))
            if v is not None and r.get('time_tag'):
                g[_hour(r['time_tag'])].append(v)
        for h, vs in g.items():
            out[h][name] = round(how(vs), 3 if name != 'xray_max' else 9)

    put(_rows(raw.get('kp')), 'Kp', 'kp', max)
    put(_rows(raw.get('kp')), 'kp', 'kp', max)                       # 小文字の見出しの版にも対応
    active = lambda rows: [r for r in rows if isinstance(r, dict) and r.get('active', True) is not False]
    mag = active(_rows(raw.get('mag')))
    put(mag, 'bz_gsm', 'bz_mean', statistics.mean)
    put(mag, 'bz_gsm', 'bz_min', min)
    put(mag, 'bt', 'bt_mean', statistics.mean)
    pl = active(_rows(raw.get('plasma')))
    put(pl, 'proton_speed', 'speed_mean', statistics.mean)
    put(pl, 'proton_density', 'density_mean', statistics.mean)
    put(pl, 'speed', 'speed_mean', statistics.mean)           # 旧形式の見出しにも対応
    put(pl, 'density', 'density_mean', statistics.mean)
    put(_rows(raw.get('proton')), 'flux', 'proton10_max', max, lambda r: r.get('energy') == '>=10 MeV')
    put(_rows(raw.get('xray')), 'flux', 'xray_max', max, lambda r: r.get('energy') == '0.1-0.8nm')
    # Kp は3時間ごとの値なので、その3時間の各時に同じ値を入れる
    kp_hours = sorted(h for h in out if 'kp' in out[h])
    for h in kp_hours:
        base = datetime.strptime(h, '%Y-%m-%d %H:00')
        for k in (1, 2):
            hh = datetime.fromtimestamp(base.replace(tzinfo=timezone.utc).timestamp() + 3600 * k,
                                        timezone.utc).strftime('%Y-%m-%d %H:00')
            out[hh].setdefault('kp', out[h]['kp'])
    return out


class SpaceWx:
    def fetch_once(self):
        try:
            raw = {}
            for k, u in SW_URLS.items():
                try:
                    raw[k] = _get(u)
                except Exception as e:
                    log(f'[宇宙天気] {k} を取得できませんでした: {e}')
            hours = spacewx_hours(raw)
            now_hour = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:00')
            by_month = defaultdict(list)
            for h in sorted(hours):
                if h < now_hour:                                   # 終わった時だけ
                    by_month[h[:7]].append({'hour_utc': h, **{k: hours[h].get(k, '') for k in SW_FIELDS[1:]}})
            for m, rows in by_month.items():
                n = _upsert(os.path.join(BASE, 'spacewx', f'{m}.csv'), SW_FIELDS, rows)
                if n:
                    log(f'[宇宙天気] {m}: {n}時間分を保存・更新')
        except Exception as e:
            log(f'[宇宙天気] 処理中にエラー: {e}')

    def run(self, stop):
        while not stop.is_set():
            self.fetch_once()
            stop.wait(SW_INTERVAL)


def main():
    stop = threading.Event()
    agg = Agg()
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    threading.Thread(target=run_mqtt, args=(agg, stop), daemon=True).start()
    threading.Thread(target=SpaceWx().run, args=(stop,), daemon=True).start()
    log(f'収集を始めました。保存先: {BASE}（止めるときは Ctrl+C）')
    last = time.time()
    while not stop.is_set():
        stop.wait(5)
        with agg.lock:
            h = agg.hour
        if h:                                                # 時が変わったのに報告が来ないときも保存する
            cur = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:00')
            if cur > h and time.time() - last > 120:
                agg.flush()
                last = time.time()
        if time.time() - last > 1800:
            log(f'[PSKR] 受信した報告 累計 {agg.total}件')
            last = time.time()
    agg.flush()
    log('終了しました')


if __name__ == '__main__':
    main()
