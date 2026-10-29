"""Reproducible HKEX directory + SDK identity refresh, never share-class approval."""
import hashlib,io,re,zipfile
from datetime import datetime
from xml.etree import ElementTree as ET
from .hk_news import SOURCE
from .hk_policy import HK
from .policy import SafetyError

NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

def workbook_rows(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist())>80_000_000:raise SafetyError('HK directory unexpectedly large')
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(t.text or '' for t in e.iter(NS+'t')) for e in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rows=[]
        for e in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).iter(NS+'row'):
            row={}
            for c in e.findall(NS+'c'):
                if c.find(NS+'f') is not None:raise SafetyError('Unexpected formula in HK directory')
                v=c.find(NS+'v');value=v.text if v is not None else None
                if c.get('t')=='s':value=strings[int(value)]
                elif c.get('t')=='inlineStr':value=''.join(t.text or '' for t in c.iter(NS+'t'))
                row[re.sub(r'[0-9]+','',c.attrib['r'])]=value
            rows.append(row)
        return rows

def build(raw,basic,now):
    rows=workbook_rows(raw)
    if now.tzinfo is None:raise SafetyError('Aware directory clock required')
    found=[re.fullmatch(r'Updated as at (\d{2}/\d{2}/\d{4})',str(r.get('A',''))) for r in rows[:5]]
    dates=[datetime.strptime(m[1],'%d/%m/%Y').date() for m in found if m]
    if len(dates)!=1 or dates[0]!=now.astimezone(HK).date():raise SafetyError('HK directory is not dated today')
    stamp=datetime.fromisoformat(basic['queried_at'])
    if stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=86400:raise SafetyError('Stale HK broker identity data')
    broker={r['code']:r for r in basic['rows']}
    if len(broker)!=len(basic['rows']):raise SafetyError('Duplicate SDK codes')
    result={};conflicts=[]
    for r in rows:
        if r.get('C')!='Equity' or r.get('D') not in ('Equity Securities (Main Board)','Equity Securities (GEM)') or r.get('Q')!='HKD':continue
        if not re.fullmatch('[0-9]{5}',str(r.get('A',''))):raise SafetyError('Invalid directory code')
        code='HK.'+r['A'];b=broker.get(code)
        if not b or b.get('delisting') is not False:continue
        lot=int(r['E'].replace(',',''))
        if lot<=0 or lot!=b.get('lot_size'):conflicts.append(code);continue
        if code in result:raise SafetyError('Duplicate HK directory code')
        if not r.get('B') or not b.get('name') or not r.get('F'):raise SafetyError('Incomplete HK identity')
        result[code]=dict(code=code,name_en=r['B'],name_zh=b['name'],aliases=[r['B'],b['name']],
            category=r['C'],subcategory=r['D'],lot_size=lot,isin=r['F'],currency='HKD',
            spread_table=(r.get('P') or '').strip(),stamp_duty=r.get('H'),exchange=b.get('exchange_type'),
            listing_date=b.get('listing_date'),classification_source='HKEX full securities list matched with moomoo basicinfo',
            share_class_reviewed=False,news_coverage_verified=False,orders_enabled=False)
    if not result:raise SafetyError('Empty matched HK catalog')
    return dict(source=SOURCE,source_date=dates[0].isoformat(),retrieved_at=now.isoformat(),
        sha256=hashlib.sha256(raw).hexdigest(),count=len(result),basic_count=len(broker),lot_conflicts=conflicts,rows=result)

def main():
    import fcntl,os
    from pathlib import Path
    from datetime import timezone
    from urllib.request import urlopen,Request
    from .rolling_scan import atomic
    import moomoo as sdk
    root=Path(__file__).resolve().parents[1]/'data/trading/hk_market';root.mkdir(parents=True,exist_ok=True)
    with (root/'catalog_refresh.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with urlopen(Request(SOURCE,headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as response:
            if response.url!=SOURCE:raise SafetyError('Unexpected HK directory redirect')
            raw=response.read(5_000_001)
        if len(raw)>5_000_000:raise SafetyError('HK directory download too large')
        q=sdk.OpenQuoteContext(host='127.0.0.1',port=11111)
        try:
            queried=datetime.now(timezone.utc).isoformat()
            ret,frame=q.get_stock_basicinfo(sdk.Market.HK,sdk.SecurityType.STOCK)
            if ret!=sdk.RET_OK:raise SafetyError('HK basic identity query failed')
            basic=dict(queried_at=queried,source='moomoo_sdk_get_stock_basicinfo_HK_STOCK',rows=frame.to_dict('records'))
            result=build(raw,basic,datetime.now(timezone.utc))
            # Publish catalog last; any interrupted intermediate combination fails
            # existing hash/name validation rather than blessing mismatched data.
            atomic(root/'basic.json',basic)
            temp=root/('ListOfSecurities.'+str(os.getpid())+'.tmp');temp.write_bytes(raw)
            temp.replace(root/'ListOfSecurities.xlsx')
            atomic(root/'catalog.json',result)
            print({'source_date':result['source_date'],'count':result['count'],'orders_enabled':False})
        finally:q.close()

if __name__=='__main__':main()
