"""Auditable headline risk overlay. Text is data, never executable instructions.
No directional prediction, trust probability, or claim of exhaustive news coverage.
"""
import hashlib
import json
import re
import os
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

FEEDS = {
    'fed_policy': 'https://www.federalreserve.gov/feeds/press_monetary.xml',
    'fed_speech': 'https://www.federalreserve.gov/feeds/speeches_and_testimony.xml',
}
DEFAULT_PATH = 'data/trading/news/latest.json'
POLL_SECONDS = 120
WIRES = {'reuters', '路透社', '路透', 'associated press', 'bloomberg', '彭博', '彭博社', 'dow jones', '道琼斯', 'mt newswires'}
FILINGS = {'美股sec公告', 'sec'}
MAJOR = re.compile(r'earnings|guidance|bankrupt|merger|acquisition|trading halt|fraud|investigation|recall|8-k|10-k|10-q|财报|业绩|指引|破产|收购|并购|停牌|欺诈|调查|召回', re.I)
OPINION = re.compile(r'forecast|expects?|predict|analyst|rating|outlook|target price|预计|预测|分析师|评级|展望|目标价', re.I)
UNCERTAIN = re.compile(r'rumou?r|unconfirmed|denies?|not confirmed|传闻|未经证实|否认|辟谣', re.I)
POLICY = re.compile(r'fomc|interest rate|monetary policy|inflation|economic outlook|利率|货币政策|通胀', re.I)


def stamp(value):
    """Never infer year/timezone from moomoo's ambiguous display dates."""
    try:
        s=str(value).strip()
        if re.fullmatch(r'\d{10}(?:\.\d+)?', s):
            return datetime.fromtimestamp(float(s), timezone.utc)
        if re.fullmatch(r'\d{13}', s):
            return datetime.fromtimestamp(int(s)/1000, timezone.utc)
        try: d=datetime.fromisoformat(s.replace('Z','+00:00'))
        except ValueError: d=parsedate_to_datetime(s)
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,TypeError,OverflowError): return None


def url(value):
    try:
        u=urlsplit(str(value))
        if u.scheme!='https' or not u.hostname or u.username or u.password:return ''
        return urlunsplit((u.scheme,u.netloc,u.path,'',''))
    except ValueError:return ''


def security(value):
    s=str(value).upper()
    if re.fullmatch(r'[A-Z0-9.-]+\.US',s):s='US.'+s[:-3]
    return s if re.fullmatch(r'US\.[A-Z0-9.-]+',s) else None


def event(row, feed, now):
    title=str(row.get('title',''))[:2000];link=url(row.get('url',''))
    published=stamp(row.get('publish_time'))
    source=str(row.get('source','')).strip()
    host=urlsplit(link).hostname
    tier='unverified'
    if feed in FEEDS and host=='www.federalreserve.gov':tier='primary'
    elif feed=='moomoo' and host in {'news.futunn.com','www.moomoo.com'}:
        if source.casefold() in WIRES:tier='attributed_wire'
        elif source.casefold() in FILINGS and row.get('news_sub_type')=='NOTICE':tier='attributed_filing'
    related=row.get('related_securities',[])
    related=related if isinstance(related,list) else []
    codes=sorted({security(s) for s in related if security(s)})
    action='observe';reason='Unverified source or unclassified headline';ttl=0
    if tier!='unverified':
        if UNCERTAIN.search(title):reason='Disputed/unconfirmed claim: manual verification'
        elif feed=='fed_policy':action='block';ttl=3600;reason='Official monetary policy release: 60-minute entry cooling period'
        elif feed=='fed_speech' and POLICY.search(title):action='reduce';ttl=3600;reason='Official policy speech: halve new-entry cap for 60 minutes'
        elif MAJOR.search(title):reason='Corporate event observed; blanket 24-hour cooling disabled'
        elif OPINION.search(title) or row.get('news_sub_type')=='RATING':action='reduce';ttl=14400;reason='Attributed opinion: halve new-entry cap for 4 hours'
    if not published or published>now:
        action='observe';reason='Publication time missing/ambiguous/future; manual verification';ttl=0
    identity=link or (source+'|'+title+'|'+str(row.get('publish_time')))
    return dict(id=hashlib.sha256(identity.encode()).hexdigest()[:24],title=title,url=link,source=source,
        feed=feed,tier=tier,codes=codes,scope='macro' if feed in FEEDS else 'security',
        published_at=published.isoformat() if published else None,raw_time=str(row.get('publish_time','')),
        observed_at=now.isoformat(),action=action,reason=reason,ttl_seconds=ttl)


def feed_rows(name):
    req=Request(FEEDS[name],headers={'User-Agent':'LocalQuantNews/1.0'})
    with urlopen(req,timeout=8) as response:
        if urlsplit(response.url).hostname!='www.federalreserve.gov':raise ValueError('Unexpected feed redirect')
        raw=response.read(2_000_001)
    if len(raw)>2_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Unsafe/oversized XML')
    root=ET.fromstring(raw)
    if root.tag!='rss' or root.find('channel') is None:raise ValueError('Invalid RSS feed')
    return [dict(title=x.findtext('title',''),url=x.findtext('link',''),publish_time=x.findtext('pubDate',''),
                 source='Federal Reserve',related_securities=[]) for x in root.findall('./channel/item')]


def collect(broker,codes,fetch=feed_rows,clock=lambda:datetime.now(timezone.utc)):
    events={};health={}
    def add(key,fn,feed):
        started=clock()
        try:
            rows=fn();unresolved=0
            for row in rows:
                e=event(row,feed,clock());events[e['id']]=e
                if feed=='moomoo' and key in e['codes'] and e['tier']!='unverified' and (not stamp(e['published_at']) or stamp(e['published_at'])>clock()):unresolved+=1
            # Time is request start, never extended by a slow response.
            health[key]=dict(ok=not(feed=='moomoo' and (len(rows)>=100 or unresolved)),checked_at=started.isoformat(),count=len(rows),
                            truncated=feed=='moomoo' and len(rows)>=100,unresolved_times=unresolved)
        except Exception as exc:health[key]=dict(ok=False,checked_at=started.isoformat(),error=str(exc)[:400])
    for name in FEEDS:add(name,lambda n=name:fetch(n),name)
    for code in sorted(set(codes)):
        if security(code)!=code:raise ValueError('Invalid US code')
        add(code,lambda c=code:broker.call('news',broker.q.get_search_news,c[3:],max_count=100).to_dict('records'),'moomoo')
    return dict(version=2,collected_at=clock().isoformat(),health=health,events=list(events.values()),
        coverage='Bounded keyword search and Fed feeds; not an exhaustive news or scheduled-event calendar')


def save(snapshot,path):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    # Search windows moving forward must not erase an existing cooling period.
    try:previous=json.loads(p.read_text())
    except (OSError,ValueError):previous={}
    now=stamp(snapshot['collected_at']);merged={e['id']:e for e in snapshot['events']}
    for e in previous.get('events',[]):
        t=stamp(e.get('published_at'))
        if t and now and 0<=(now-t).total_seconds()<e.get('ttl_seconds',0):
            fresh=merged.get(e['id'])
            if fresh is None or {'observe':0,'reduce':1,'block':2}[e['action']]>{'observe':0,'reduce':1,'block':2}[fresh['action']]:merged[e['id']]=e
    snapshot=dict(snapshot,events=list(merged.values()))
    body=json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False)
    fd,tmp=tempfile.mkstemp(dir=p.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w') as f:f.write(body);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
        # Keep immutable observation-time snapshots for later research (no hindsight overwrite).
        digest=hashlib.sha256(body.encode()).hexdigest()[:20]
        archive=p.parent/'archive';archive.mkdir(exist_ok=True)
        target=archive/(digest+'.json')
        if not target.exists():target.write_text(body)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def coverage_age_limit(snapshot):
    # Five-minute polling permits only one minute of collection latency; stale failures still block.
    return {120:180,300:360}.get(snapshot.get('poll_interval_seconds'),300)


def assess(snapshot,code,now):
    reasons=[];ids=[];multiplier=1.;global_block=False
    if snapshot.get('version') not in (2,3):
        reasons.append('News cache schema requires refresh');multiplier=0.;global_block=True
    required=snapshot.get('required_sources',[]) if snapshot.get('version')==3 else list(FEEDS)
    if not required:reasons.append('External news sources unconfigured');multiplier=0.;global_block=True
    for key in [*required,code]:
        h=snapshot.get('health',{}).get(key,{})
        t=stamp(h.get('checked_at'))
        if not h.get('ok') or not t or not 0<=(now-t).total_seconds()<=coverage_age_limit(snapshot):
            reasons.append('News coverage unavailable/stale/truncated or publication time unresolved: '+key);multiplier=0.
            if key in required:global_block=True
    for e in snapshot.get('events',[]):
        if e.get('scope')!='macro' and code not in e.get('codes',[]):continue
        t=stamp(e.get('published_at'));seen=stamp(e.get('observed_at'))
        if not t or not seen or seen>now or not 0<=(now-t).total_seconds()<e.get('ttl_seconds',0):continue
        # User revoked only the blanket corporate 24h rule. Preserve cached
        # evidence, but do not enforce that obsolete rule after code reload.
        if (e.get('scope')=='security' and e.get('action')=='block'
                and e.get('ttl_seconds')==86400 and e.get('reason') in (
                    'Potential major corporate event: 24-hour entry cooling period; headline flag, not confirmed direction',
                    'External major-event risk flag: entry cooling period; direction unverified')):
            continue
        m={'block':0.,'reduce':.5}.get(e.get('action'),1.)
        if e.get('scope')=='macro' and m==0:global_block=True
        if m<1:
            multiplier=min(multiplier,m);reasons.append(e['reason']);ids.append(e['id'])
    from .news_emergency import exit_evidence
    emergency=exit_evidence(snapshot,code,now)
    from .news_opportunity import entry_evidence
    opportunity=entry_evidence(snapshot,code,now)
    if emergency:
        multiplier=0.
        reasons.append('Verified adverse corporate news: new entry blocked')
        ids.extend(e['event_id'] for e in emergency)
    return dict(code=code,multiplier=multiplier,global_block=global_block,reasons=reasons,event_ids=sorted(set(ids)),exit_long=bool(emergency),exit_short=False,exit_evidence=emergency,entry_evidence=opportunity,
                checked_at=now.isoformat(),policy_version=2)


class NewsGuard:
    def __init__(self,path=DEFAULT_PATH):self.path=Path(path)
    def __call__(self,code,now):
        try:return assess(json.loads(self.path.read_text()),code,now)
        except (OSError,ValueError,TypeError,KeyError):
            return dict(code=code,multiplier=0.,global_block=True,reasons=['News cache unavailable/corrupt'],event_ids=[],checked_at=now.isoformat(),policy_version=2)


def refresh_live(codes,path=DEFAULT_PATH,port=11111,source_config='trading/external_news.example.json'):
    from .external_news import collect_external
    entities={}
    catalogs=sorted(Path('data/trading/market').glob('catalog_*.json'))
    if catalogs:
        try:
            basic=json.loads(catalogs[-1].read_text()).get('basic',{})
            entities={c:[basic[c]['name']] for c in basic if security(c)==c and isinstance(basic[c].get('name'),str) and len(basic[c]['name'])>=3}
        except (OSError,ValueError,KeyError):pass
    from .news_emergency import issuer_aliases
    for code,aliases in issuer_aliases(set(codes)|set(entities),'data/trading/market').items():entities[code]=sorted(set(entities.get(code,[])+aliases))
    from .hk_news import identities
    hk_codes=set(codes)
    try:hk_codes.update(json.loads(Path('data/trading/hk_market/catalog.json').read_text()).get('rows',{}))
    except (OSError,ValueError,TypeError):pass
    hk=identities('data/trading/hk_market',hk_codes,datetime.now(timezone.utc))
    snapshot=collect_external(codes,source_config,additional_entities=entities,hk_identities=hk)
    snapshot['poll_interval_seconds']=POLL_SECONDS
    save(snapshot,path)
    return snapshot


class NewsWorker:
    """Separate process: slow news never blocks fills, reconciliation or 60s cancellation."""
    def __init__(self,path,port,source_config='trading/external_news.example.json'):self.source_config=source_config;self.path=path;self.port=port;self.process=None;self.started=0;self.last=0
    def poll(self,codes):
        import subprocess,sys,time
        now=time.monotonic()
        if self.process:
            if self.process.poll() is None:
                if now-self.started>90:self.process.kill();self.process.wait()
                else:return
            self.process=None
        if now-self.last<POLL_SECONDS or not codes:return
        self.last=self.started=now
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'trade.py'),
            '--port',str(self.port),'news','--sources',str(Path(self.source_config).resolve()),'--output',str(Path(self.path).resolve()),'--codes',*codes],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def close(self):
        if self.process and self.process.poll() is None:
            self.process.kill();self.process.wait()
