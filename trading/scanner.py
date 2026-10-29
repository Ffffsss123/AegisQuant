import csv
import io
import json
import re
import time
import urllib.request
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from .policy import SafetyError,candidates,eligible

DIRECTORIES={
 'NASDAQ':'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
 'OTHER':'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt'}

def classification(directory_rows,basic,reviewed,today):
    universe={}
    exchange_map={'Q':'NASDAQ','N':'NYSE','A':'AMEX','P':'ARCA','Z':'BATS','V':'IEX'}
    for row in directory_rows:
        symbol=row.get('Symbol') or row.get('NASDAQ Symbol')
        code='US.'+symbol if symbol else ''
        if code not in basic or row.get('Test Issue')!='N':continue
        r=basic[code];name=row.get('Security Name','');kind='UNKNOWN'
        common=bool(re.search(r'\b(common stock|common shares|ordinary shares)\b',name,re.I))
        forbidden=bool(re.search(r'\b(preferred|preference|warrant|rights|units|depositary|debenture|notes)\b',name,re.I))
        if row.get('ETF')=='N' and common and not forbidden:kind='COMMON'
        if row.get('ETF')=='Y':
            entry=reviewed.get(code,{})
            try:
                age=(today-date.fromisoformat(entry['reviewed_on'])).days
                if entry.get('kind')=='ETF_NORMAL' and entry.get('source','').startswith('https://') and 0<=age<=30:
                    kind='ETF_NORMAL'
            except (KeyError,ValueError):pass
        if row.get('Financial Status','N')!='N':kind='UNKNOWN'
        universe[code]=dict(kind=kind,verified=kind!='UNKNOWN',exchange=row['_exchange'],
            suspended=r.get('suspension'),delisted=r.get('delisting'),lot=r.get('lot_size'),name=name,
            classification_source='Nasdaq Symbol Directory; ETFs require issuer review')
    return universe


def fetch_directories(cache):
    cache.mkdir(parents=True,exist_ok=True)
    rows=[]
    for name,url in DIRECTORIES.items():
        req=urllib.request.Request(url,headers={'User-Agent':'QuantResearch/1.0'})
        raw=urllib.request.urlopen(req,timeout=30).read().decode('utf-8-sig')
        if 'File Creation Time' not in raw:raise SafetyError('Invalid symbol directory')
        (cache/(name+'.txt')).write_text(raw)
        for row in csv.DictReader(io.StringIO(raw),delimiter='|'):
            if not row.get('Security Name'):continue
            row['_exchange']='NASDAQ' if name=='NASDAQ' else {'N':'NYSE','A':'AMEX','P':'ARCA','Z':'BATS','V':'IEX'}.get(row.get('Exchange'),'UNKNOWN')
            rows.append(row)
    return rows


def scan(broker,path,now,held_codes=(),max_requests=None):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    expected=broker.completed_session(now)
    # Reuse the audited, completed rolling scan for this session. Do not replace
    # it with a quota-limited historical scan or lose its explicit exclusions.
    latest=path/'scan.json'
    if latest.exists():
        try:
            result=json.loads(latest.read_text())
            if (result.get('source')=='moomoo_subscribed_daily' and result.get('date')==expected
                and result.get('complete') and all(c in result.get('signals',{}) for c in held_codes)):
                return result
        except (OSError,ValueError):pass
    from .rolling_scan import RollingScan
    import fcntl
    with (path/'rolling_scan.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SafetyError('Whole-market scanner already running')
        collector=RollingScan(broker,path,path.parent/'preparation_scan.json',now,held_codes)
        try:return collector.run()
        except Exception as exc:
            collector.errors['_run']=str(exc);collector.save('needs_attention');raise
