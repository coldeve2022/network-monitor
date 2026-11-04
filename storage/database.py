"""SQLite 存储：分钟级/小时级两级聚合表 + 事件表，带过期清理。"""
import os
import json
import sqlite3
import threading
import time

import config
from storage.writer import ConnectionView
from storage.migrations import backup_before_upgrade, SCHEMA_VERSION
from core.statistics import accumulate, merge_stats, summarize

SCHEMA = """
CREATE TABLE IF NOT EXISTS ping_minute(
  ts INTEGER, target TEXT, avg REAL, min REAL, max REAL, loss REAL, jitter REAL, std REAL,
  PRIMARY KEY(ts, target));
CREATE TABLE IF NOT EXISTS ping_hour(
  ts INTEGER, target TEXT, avg REAL, min REAL, max REAL, loss REAL, jitter REAL, std REAL,
  PRIMARY KEY(ts, target));
CREATE TABLE IF NOT EXISTS tcp_minute(ts INTEGER, name TEXT, ms REAL, PRIMARY KEY(ts, name));
CREATE TABLE IF NOT EXISTS tcp_hour(ts INTEGER, name TEXT, ms REAL, PRIMARY KEY(ts, name));
CREATE TABLE IF NOT EXISTS dns_minute(ts INTEGER, server TEXT, ms REAL, PRIMARY KEY(ts, server));
CREATE TABLE IF NOT EXISTS dns_hour(ts INTEGER, server TEXT, ms REAL, PRIMARY KEY(ts, server));
CREATE TABLE IF NOT EXISTS traffic_minute(
  ts INTEGER, rx_bps REAL, tx_bps REAL, rx_peak REAL, tx_peak REAL, PRIMARY KEY(ts));
CREATE TABLE IF NOT EXISTS traffic_hour(
  ts INTEGER, rx_bps REAL, tx_bps REAL, rx_peak REAL, tx_peak REAL, PRIMARY KEY(ts));
CREATE TABLE IF NOT EXISTS minute_stats(
  ts INTEGER, metric TEXT, target TEXT, stats TEXT NOT NULL,
  PRIMARY KEY(ts, metric, target));
CREATE TABLE IF NOT EXISTS hour_stats(
  ts INTEGER, metric TEXT, target TEXT, stats TEXT NOT NULL, PRIMARY KEY(ts,metric,target));
CREATE TABLE IF NOT EXISTS legacy_archive(ts INTEGER,metric TEXT,target TEXT,source TEXT,payload TEXT,PRIMARY KEY(ts,metric,target,source));
CREATE TABLE IF NOT EXISTS ai_usage(day INTEGER PRIMARY KEY,calls INTEGER,last_at REAL);
CREATE TABLE IF NOT EXISTS raw_samples(session TEXT, sequence INTEGER, ts REAL, metric TEXT, target TEXT, payload TEXT NOT NULL, PRIMARY KEY(session,sequence));
CREATE TABLE IF NOT EXISTS raw_sessions(session TEXT PRIMARY KEY, started REAL);
CREATE INDEX IF NOT EXISTS idx_raw_ts ON raw_samples(ts);
CREATE TABLE IF NOT EXISTS dirty_hours(ts INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, type TEXT, message TEXT);
CREATE INDEX IF NOT EXISTS idx_ping_minute_ts ON ping_minute(ts);
CREATE INDEX IF NOT EXISTS idx_ping_hour_ts ON ping_hour(ts);
CREATE INDEX IF NOT EXISTS idx_tcp_minute_ts ON tcp_minute(ts);
CREATE INDEX IF NOT EXISTS idx_dns_minute_ts ON dns_minute(ts);
CREATE INDEX IF NOT EXISTS idx_traffic_minute_ts ON traffic_minute(ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


class Database:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._writer = None
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn = ConnectionView(self)
        try:self.pre_upgrade_backup = backup_before_upgrade(self._conn,path)
        except Exception:self._conn.close();raise
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.executescript(SCHEMA)
        self._conn.execute('PRAGMA user_version='+str(SCHEMA_VERSION))
        self._conn.commit()
        self._lock = threading.RLock()
        self._closed = False
        self.recover_raw_minutes()
        for metric in ('ping','tcp','dns','traffic'):
            self._conn.execute(f"INSERT OR IGNORE INTO dirty_hours SELECT DISTINCT CAST(ts/3600 AS INTEGER)*3600 FROM {metric}_minute")
        self._conn.commit()
        self.recover_pending_hours(time.time())

    def flush_minute(self, ts, buckets, stats=None):
        self._drain_external()
        """写一个完整分钟的所有指标聚合。"""
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO dirty_hours VALUES (?)",(ts//3600*3600,))
            for target, a in buckets.get("ping", {}).items():
                self._conn.execute(
                    "INSERT OR REPLACE INTO ping_minute VALUES (?,?,?,?,?,?,?,?)",
                    (ts, target, a["avg"], a["min"], a["max"], a["loss"],
                     a["jitter"], a["std"]))
            for name, a in buckets.get("tcp", {}).items():
                self._conn.execute("INSERT OR REPLACE INTO tcp_minute VALUES (?,?,?)",
                                  (ts, name, a["ms"]))
            for server, a in buckets.get("dns", {}).items():
                self._conn.execute("INSERT OR REPLACE INTO dns_minute VALUES (?,?,?)",
                                  (ts, server, a["ms"]))
            for _k, a in buckets.get("traffic", {}).items():
                self._conn.execute(
                    "INSERT OR REPLACE INTO traffic_minute VALUES (?,?,?,?,?)",
                    (ts, a["rx_bps"], a["tx_bps"], a["rx_peak"], a["tx_peak"]))
            if stats is None:
                # An explicit aggregate replacement has no trustworthy counts.
                # Invalidate only the replaced rows, leaving other targets intact.
                for metric, targets in buckets.items():
                    for target in targets:
                        self._conn.execute(
                            "DELETE FROM minute_stats WHERE ts=? AND metric=? AND target=?",
                            (ts, metric, "all" if metric == "traffic" else target))
            else:
                for (metric, target), values in stats.items():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?)",
                        (ts, metric, target, json.dumps(values)))

    def load_minute_stats(self, ts):
        self._drain_external()
        """Resume exact new-version counts; old rows are one aggregate observation.

        Legacy rows contain no sample counts, so treating each existing aggregate
        as one observation preserves its values but gives approximate weighting.
        The legacy_approximate marker is persisted with the resumed statistics.
        """
        with self._lock:
            stats = {(metric, target): json.loads(values)
                     for metric, target, values in self._conn.execute(
                         "SELECT metric, target, stats FROM minute_stats WHERE ts=?", (ts,))}
            for target, avg, mn, mx, loss, jitter, std in self._conn.execute(
                    "SELECT target, avg, min, max, loss, jitter, std FROM ping_minute WHERE ts=?", (ts,)):
                stats.setdefault(("ping", target), {
                    "count": 1, "ok": int(avg is not None), "avg": avg or 0.0,
                    "min": mn, "max": mx, "loss_sum": loss or 0.0,
                    "jitter": jitter or 0.0, "jitter_count": 1 if jitter is not None else 0,
                    "loss_count": 1 if loss is not None else 0,
                    "sum_sq": ((avg or 0.0) ** 2 + (std or 0.0) ** 2) if avg is not None else 0.0,
                    "legacy_approximate": True})
            for metric, key in (("tcp", "name"), ("dns", "server")):
                for target, ms in self._conn.execute(
                        f"SELECT {key}, ms FROM {metric}_minute WHERE ts=?", (ts,)):
                    stats.setdefault((metric, target), {
                        "count": 1, "ok": int(ms is not None), "ms": ms or 0.0,
                        "legacy_approximate": True})
            row = self._conn.execute(
                "SELECT rx_bps, tx_bps, rx_peak, tx_peak FROM traffic_minute WHERE ts=?", (ts,)).fetchone()
            if row is not None:
                stats.setdefault(("traffic", "all"), {
                    "count": 1, "ok": 0, "rx_bps": row[0], "tx_bps": row[1],
                    "rx_peak": row[2], "tx_peak": row[3], "legacy_approximate": True})
            for (metric, _target), values in stats.items():
                if metric=='traffic' and 'duration' not in values:
                    values['duration']=values['count']
                    values['rx_bytes']=values.get('rx_bps',0)
                    values['tx_bytes']=values.get('tx_bps',0)
                    values['legacy_approximate']=True
                if metric == 'ping':
                    values.setdefault('jitter_count',values['count'])
                    values.setdefault('loss_count',values['count'])
                if metric == "ping" and "sum_sq" not in values:
                    # Compatibility with early sufficient-stat snapshots that
                    # stored a sum of rolling deviations rather than squared RTTs.
                    ok = values["ok"]
                    avg = values.get("avg", 0.0) / ok if ok else 0.0
                    std = values.get("std", 0.0) / values["count"] if values["count"] else 0.0
                    values["sum_sq"] = ok * (avg * avg + std * std)
                    values["legacy_approximate"] = True
            return stats

    def recover_raw_minutes(self):
        known_sessions={row[0] for row in self._conn.execute('SELECT session FROM raw_sessions')}
        rows=self._conn.execute('SELECT payload FROM raw_samples WHERE ts>=? ORDER BY session,sequence',(time.time()-3600,)).fetchall()
        preferred={}
        for row in rows:
            item=json.loads(row[0]);physical=(item['session_id'],item['type'],item.get('sample_id') or (item['target_id'],item['ts']))
            previous=preferred.get(physical)
            if previous is None or item.get('kind')=='base':preferred[physical]=item
        buckets={};changed=set()
        for item in sorted(preferred.values(),key=lambda i:(i['session_id'],i['sequence'])):
            metric=item['type'];minute=int(item['ts']//60)*60
            keyfield={'ping':'host','tcp':'name','dns':'server'}.get(metric)
            target=item.get('storage_key',item.get(keyfield,'all')) if keyfield else 'all'
            bucket=buckets.setdefault(minute,self.load_minute_stats(minute))
            identity=(metric,target);stats=bucket.setdefault(identity,{'count':0,'ok':0})
            # An old aggregate without raw watermarks cannot safely be re-counted.
            if stats.get('count') and not stats.get('watermarks') and item['session_id'] not in known_sessions:continue
            if item['sequence']<=stats.get('watermarks',{}).get(item['session_id'],0):continue
            accumulate(stats,item);changed.add(minute)
        for minute in changed:
            aggregate={'ping':{},'tcp':{},'dns':{},'traffic':{}}
            for (metric,target),stats in buckets[minute].items():
                if not stats.get('count'):continue
                value=summarize(stats,metric)
                if metric=='ping':aggregate[metric][target]={key:value[key] for key in ('avg','min','max','loss','jitter','std')}
                elif metric in ('tcp','dns'):aggregate[metric][target]={'ms':value['ms']}
                else:aggregate[metric][target]=value
            self.flush_minute(minute,aggregate,stats=buckets[minute])

    def aggregate_hour(self, hour_ts):
        self._drain_external()
        with self._lock, self._conn:
            minutes = {r[0] for r in self._conn.execute("SELECT DISTINCT ts FROM minute_stats WHERE ts>=? AND ts<?",(hour_ts,hour_ts+3600))}
            for metric in ('ping','tcp','dns','traffic'):
                minutes.update(r[0] for r in self._conn.execute(f"SELECT DISTINCT ts FROM {metric}_minute WHERE ts>=? AND ts<?",(hour_ts,hour_ts+3600)))
            parts = {}
            for minute in sorted(minutes):
                for identity, stats in self.load_minute_stats(minute).items():
                    parts.setdefault(identity, []).append(stats)
            for (metric,target), values in parts.items():
                stats=merge_stats(values); a=summarize(stats,metric)
                keycol={'ping':'target','tcp':'name','dns':'server'}.get(metric)
                where='ts=?'+(' AND '+keycol+'=?' if keycol else '')
                parameters=(hour_ts,target) if keycol else (hour_ts,)
                existing=self._conn.execute(f'SELECT * FROM {metric}_hour WHERE '+where,parameters).fetchone()
                precise=self._conn.execute('SELECT 1 FROM hour_stats WHERE ts=? AND metric=? AND target=?',(hour_ts,metric,target)).fetchone()
                safe_floor=(int((time.time()-config.MINUTE_RETENTION_HOURS*3600)//3600)+1)*3600
                if existing and not precise and hour_ts<safe_floor:
                    self._conn.execute('INSERT OR IGNORE INTO legacy_archive VALUES (?,?,?,?,?)',(hour_ts,metric,target,'hour',json.dumps(existing)))
                    args=(hour_ts,hour_ts+3600,target) if keycol else (hour_ts,hour_ts+3600)
                    condition='ts>=? AND ts<?'+(' AND '+keycol+'=?' if keycol else '')
                    retained=self._conn.execute(f'SELECT * FROM {metric}_minute WHERE '+condition,args).fetchall()
                    for row in retained:self._conn.execute('INSERT OR IGNORE INTO legacy_archive VALUES (?,?,?,?,?)',(row[0],metric,target,'minute',json.dumps(row)))
                    witnessed_missing=metric=='ping' and ((a['max'] is not None and (existing[4] is None or a['max']>existing[4])) or (a['min'] is not None and (existing[3] is None or a['min']<existing[3])))
                    if not witnessed_missing:continue
                    stats['legacy_approximate']=True
                self._conn.execute("INSERT OR REPLACE INTO hour_stats VALUES (?,?,?,?)",(hour_ts,metric,target,json.dumps(stats)))
                if metric=='ping':
                    self._conn.execute("INSERT OR REPLACE INTO ping_hour VALUES (?,?,?,?,?,?,?,?)",(hour_ts,target,a['avg'],a['min'],a['max'],a['loss'],a['jitter'],a['std']))
                elif metric in ('tcp','dns'):
                    self._conn.execute(f"INSERT OR REPLACE INTO {metric}_hour VALUES (?,?,?)",(hour_ts,target,a['ms']))
                else:
                    self._conn.execute("INSERT OR REPLACE INTO traffic_hour VALUES (?,?,?,?,?)",(hour_ts,a['rx_bps'],a['tx_bps'],a['rx_peak'],a['tx_peak']))
            self._conn.execute("DELETE FROM dirty_hours WHERE ts=?",(hour_ts,))

    def recover_pending_hours(self, now):
        self._drain_external()
        with self._lock:
            hours=[r[0] for r in self._conn.execute("SELECT ts FROM dirty_hours WHERE ts<?",(int(now//3600)*3600,))]
            for hour in hours:self.aggregate_hour(hour)
        return len(hours)

    def query_stats(self, metric, hours, target=None, end_before=None):
        self._drain_external()
        rows=[row for row in self.query_minute(metric,hours,target) if row[0]<=time.time() and (end_before is None or row[0]<end_before)]
        cutoff=time.time()-hours*3600
        boundary=(int((time.time()-config.MINUTE_RETENTION_HOURS*3600)//3600)+1)*3600
        with self._lock:
            where='metric=? AND ts>=?';args=[metric,cutoff]
            if target:where+=' AND target=?';args.append(target)
            minute={(ts,t):json.loads(v) for ts,t,v in self._conn.execute('SELECT ts,target,stats FROM minute_stats WHERE '+where,args)}
            hourly={(ts,t):json.loads(v) for ts,t,v in self._conn.execute('SELECT ts,target,stats FROM hour_stats WHERE '+where,args)}
            keycol={'ping':'target','tcp':'name','dns':'server'}.get(metric)
            hour_keys={(r[0],r[1] if keycol else 'all') for r in self._conn.execute(f'SELECT * FROM {metric}_hour WHERE ts>=?',(cutoff,))}
            result=[]
            for row in rows:
                ts=row[0];identity=row[1] if metric!='traffic' else 'all'
                key=(ts,identity)
                prefer_hour=hours>config.MINUTE_RETENTION_HOURS and ts<boundary and key in hour_keys
                stats=hourly.get(key) if prefer_hour else minute.get(key)
                if stats is None and metric=='ping':
                    _,_,avg,mn,mx,loss,jitter,std=row
                    stats={'count':1,'ok':int(avg is not None),'avg':avg or 0,'sum_sq':(avg or 0)**2+(std or 0)**2,'min':mn,'max':mx,'loss_sum':loss or 0,'loss_count':1 if loss is not None else 0,'jitter':jitter or 0,'jitter_count':1 if jitter is not None else 0,'legacy_approximate':True}
                elif stats is None and metric in ('tcp','dns'):
                    stats={'count':1,'ok':int(row[2] is not None),'ms':row[2] or 0,'legacy_approximate':True}
                if stats is not None:result.append(stats)
            return merge_stats(result)

    def recovery_evidence(self,hours=720):
        self._drain_external()
        with self._lock:
            return [{'ts':ts,'metric':metric,'target':target,'source':source,'row':json.loads(payload)} for ts,metric,target,source,payload in self._conn.execute('SELECT ts,metric,target,source,payload FROM legacy_archive WHERE ts>=? ORDER BY ts LIMIT 10000',(time.time()-hours*3600,))]

    def register_session(self,session,started):
        with self._lock,self._conn:self._conn.execute('INSERT OR IGNORE INTO raw_sessions VALUES (?,?)',(session,started))

    def reserve_ai_call(self, now, limit, cooldown):
        self._drain_external()
        day=int(time.strftime('%Y%m%d',time.localtime(now)))
        with self._lock,self._conn:
            row=self._conn.execute('SELECT calls,last_at FROM ai_usage WHERE day=?',(day,)).fetchone()
            count,last=row if row else (0,0)
            if count>=limit or now-last<cooldown:return False
            self._conn.execute('INSERT OR REPLACE INTO ai_usage VALUES (?,?,?)',(day,count+1,now))
            return True

    def save_raw_batch(self, items):
        if not items:return
        with self._lock, self._conn:
            self._conn.executemany("INSERT OR IGNORE INTO raw_samples VALUES (?,?,?,?,?,?)",[(i['session_id'],i['sequence'],i['ts'],i['type'],i['target_id'],json.dumps(i,ensure_ascii=False)) for i in items])
            latest=max(i['ts'] for i in items)
            if latest-getattr(self,'_raw_cleanup_at',0)>=60:
                self._conn.execute("DELETE FROM raw_samples WHERE ts<?",(latest-3600,))
                self._conn.execute("DELETE FROM raw_samples WHERE rowid IN (SELECT rowid FROM raw_samples ORDER BY ts DESC LIMIT -1 OFFSET 250000)")
                self._raw_cleanup_at=latest

    def replay(self, session, after, limit, since):
        self._drain_external()
        with self._lock:
            rows=self._conn.execute("SELECT payload FROM raw_samples WHERE session=? AND sequence>? AND ts>=? ORDER BY sequence LIMIT ?",(session,after,since,limit+1)).fetchall()
        return [json.loads(r[0]) for r in rows[:limit]],len(rows)>limit

    def add_event(self, ts, etype, message):
        self._drain_external()
        with self._lock:
            self._conn.execute("INSERT INTO events(ts, type, message) VALUES (?,?,?)",
                              (ts, etype, message))
            self._conn.commit()

    def cleanup(self, now):
        self._drain_external()
        self.recover_pending_hours(now)
        with self._lock:
            min_cutoff = now - config.MINUTE_RETENTION_HOURS * 3600
            hour_cutoff = now - config.HOUR_RETENTION_DAYS * 86400
            ev_cutoff = now - config.EVENT_RETENTION_DAYS * 86400
            for t in ("ping_minute", "tcp_minute", "dns_minute", "traffic_minute"):
                self._conn.execute(f"DELETE FROM {t} WHERE ts < ?", (min_cutoff,))
            self._conn.execute("DELETE FROM minute_stats WHERE ts < ?", (int(min_cutoff//3600)*3600,))
            self._conn.execute("DELETE FROM hour_stats WHERE ts < ?", (hour_cutoff,))
            self._conn.execute("DELETE FROM legacy_archive WHERE ts < ?", (hour_cutoff,))
            for t in ("ping_hour", "tcp_hour", "dns_hour", "traffic_hour"):
                self._conn.execute(f"DELETE FROM {t} WHERE ts < ?", (hour_cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (ev_cutoff,))
            self._conn.commit()

    def _history_sql(self, metric, hours, target=None):
        table = {"ping": "ping_minute", "tcp": "tcp_minute",
                 "dns": "dns_minute", "traffic": "traffic_minute"}[metric]
        cols = {"ping": "target, avg, min, max, loss, jitter, std",
                "tcp": "name, ms",
                "dns": "server, ms",
                "traffic": "rx_bps, tx_bps, rx_peak, tx_peak"}[metric]
        now = time.time()
        cutoff = now - hours * 3600
        args = [cutoff,now]
        where = "ts>=? AND ts<=?"
        if target:
            keycol = {"ping": "target", "tcp": "name", "dns": "server"}.get(metric)
            if keycol:
                where = f"{keycol}=? AND ts>=? AND ts<=?"
                args.insert(0, target)
        sql = f"SELECT ts, {cols} FROM {table} WHERE {where} ORDER BY ts"
        if hours <= config.MINUTE_RETENTION_HOURS:
            return sql,args
        # Use complete hour buckets for expired minute data. Align the
        # boundary upward so a partially expired hour is never double counted.
        boundary = (int((now - config.MINUTE_RETENTION_HOURS * 3600) // 3600) + 1) * 3600
        hour_table = table.replace("_minute", "_hour")
        keycol = {"ping": "target", "tcp": "name", "dns": "server"}.get(metric)
        identity = f"AND h.{keycol}=m.{keycol}" if keycol else ""
        missing_hour = (f"NOT EXISTS (SELECT 1 FROM {hour_table} h "
                        f"WHERE h.ts=CAST(m.ts/3600 AS INTEGER)*3600 {identity})")
        sql = (f"SELECT ts, {cols} FROM {hour_table} WHERE {where} AND ts<? "
               f"UNION ALL SELECT ts, {cols} FROM {table} m WHERE {where} "
               f"AND (ts>=? OR {missing_hour}) ORDER BY ts")
        return sql,args + [boundary] + args + [boundary]

    def history_payload(self,metric,hours,target=None):
        self._drain_external()
        rows=self.query_minute(metric,hours,target)
        cutoff=time.time()-hours*3600
        boundary=(int((time.time()-config.MINUTE_RETENTION_HOURS*3600)//3600)+1)*3600
        keycol={'ping':'target','tcp':'name','dns':'server'}.get(metric)
        with self._lock:
            minute={(ts,t):json.loads(value) for ts,t,value in self._conn.execute('SELECT ts,target,stats FROM minute_stats WHERE metric=? AND ts>=?',(metric,cutoff))}
            hourly={(ts,t):json.loads(value) for ts,t,value in self._conn.execute('SELECT ts,target,stats FROM hour_stats WHERE metric=? AND ts>=?',(metric,cutoff))}
            hour_keys={(row[0],row[1] if keycol else 'all') for row in self._conn.execute(f'SELECT * FROM {metric}_hour WHERE ts>=?',(cutoff,))}
            metadata=[]
            for row in rows:
                key=(row[0],row[1] if keycol else 'all')
                is_hour=hours>config.MINUTE_RETENTION_HOURS and row[0]<boundary and key in hour_keys
                stats=(hourly if is_hour else minute).get(key)
                metadata.append({'resolution':'hour' if is_hour else 'minute','sample_count':stats.get('count') if stats else None,'success_count':stats.get('ok') if stats else None,'approximate':bool(not stats or stats.get('legacy_approximate')),'window_start':row[0],'window_end':row[0]+(3600 if is_hour else 60)})
        return {'metric':metric,'rows':rows,'metadata':metadata,'as_of':time.time(),'requested_hours':hours}

    def query_minute(self,metric,hours,target=None):
        self._drain_external()
        sql,args=self._history_sql(metric,hours,target)
        with self._lock:return self._conn.execute(sql,args).fetchall()

    def iter_history(self,metric,hours,target=None):
        sql,args=self._history_sql(metric,hours,target)
        connection=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        try:
            cursor=connection.execute(sql,args)
            while True:
                rows=cursor.fetchmany(512)
                if not rows:break
                yield from rows
        finally:connection.close()

    def recent_events(self, limit=100):
        self._drain_external()
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, type, message FROM events ORDER BY ts DESC LIMIT ?",
                (limit,)).fetchall()
        return list(reversed(rows))

    def size_mb(self):
        try:
            return round((os.path.getsize(self.path)+(os.path.getsize(str(self.path)+'-wal') if os.path.exists(str(self.path)+'-wal') else 0)) / 1024 / 1024, 2)
        except OSError:
            return 0.0

    def _drain_external(self):
        if self._writer is not None and not self._writer.is_owner():self._writer.drain()

    def _close_actual(self):
        with self._lock:
            if not self._closed:self._conn.close();self._closed=True

    def close(self):
        if self._writer is not None and not self._writer.is_owner():return self._writer.close()
        self._close_actual()
        return True
