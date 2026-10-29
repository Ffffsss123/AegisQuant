"""External information providers. No broker SDK imports or account access."""
import json
import os
import re
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,urlopen
from xml.etree import ElementTree as ET
from .news import FEEDS,event,stamp,security,MAJOR,UNCERTAIN
from .news_identity import specific_alias

DEFAULT_CONFIG='trading/external_news.example.json'
TOPICS={
    'tariffs':(r'tariff|trade war|关税|贸易战','贸易成本、进口链和报复措施可能影响利润，方向取决于行业与政策细节'),
    'rates':(r'interest rate|fomc|monetary policy|inflation|利率|通胀','利率预期与折现率可能影响估值，需要核对实际决定与市场预期'),
    'geopolitics':(r'sanction|military|war\b|missile|制裁|战争|军事','供应链、能源供应与风险偏好可能变化，不能由标题确定股价方向'),
    'energy':(r'\boil\b|opec|petroleum|石油|原油','能源供需可能影响生产商与用能行业，受益与受损方向可能相反'),
    'chips':(r'semiconductor|export control|chip ban|半导体|芯片|出口管制','可能影响芯片销售、供应链或出口市场，需要核对受限制主体'),
}

def request_json(endpoint,params,token):
    req=Request('https://api.x.com/2/'+endpoint+'?'+urlencode(params),headers={'Authorization':'Bearer '+token})
    # No redirects carrying the credential to other hosts.
    from urllib.request import build_opener,HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with build_opener(NoRedirect).open(req,timeout=10) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000:raise ValueError('Oversized X response')
    result=json.loads(raw)
    if result.get('errors'):raise ValueError('X returned partial/error response')
    return result


def rss(spec):
    from .news_backoff import fetch_with_backoff
    return fetch_with_backoff(spec,_rss)

def _rss(spec):
    address=spec['url'];host=urlsplit(address).hostname
    if urlsplit(address).scheme!='https':raise ValueError('HTTPS feed required')
    with urlopen(Request(address,headers={'User-Agent':'LocalQuantNews/1.0'}),timeout=8) as response:
        if urlsplit(response.url).hostname!=host:raise ValueError('Unexpected feed redirect')
        raw=response.read(2_000_001)
    if len(raw)>2_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Unsafe XML')
    root=ET.fromstring(raw)
    if root.tag!='rss' or root.find('channel') is None:raise ValueError('Invalid RSS')
    return parse_items(root,spec)


def parse_items(root,spec):
    rows=[]
    for x in root.findall('./channel/item'):
        publisher=x.find('source')
        publisher_name=publisher.text if publisher is not None else ''
        publisher_url=publisher.get('url','') if publisher is not None else ''
        if spec.get('publisher_hosts'):
            if urlsplit(publisher_url).hostname not in spec['publisher_hosts'] or publisher_name!=spec['publisher_name']:continue
        rows.append(dict(title=x.findtext('title',''),summary=re.sub('<[^>]+>',' ',x.findtext('description',''))[:4000],
            url=x.findtext('link',''),publish_time=x.findtext('pubDate',''),source=spec['name'],related_securities=[],
            publisher_name=publisher_name,publisher_url=publisher_url))
    return rows



def x_posts(config,now,api=request_json):
    if not config.get('enabled'):raise ValueError('X is not enabled; developer access and usage budget required')
    token=os.environ.get(config.get('token_env','X_BEARER_TOKEN'))
    if not token:raise ValueError('X credential not configured in local environment')
    handles=config.get('accounts',[])
    if not handles or len(handles)>10 or any(not re.fullmatch('[A-Za-z0-9_]{1,15}',h) for h in handles):raise ValueError('Invalid X account watchlist')
    users=api('users/by',{'usernames':','.join(handles)},token)
    authors={u['id']:u['username'] for u in users.get('data',[]) if u['username'].lower() in {h.lower() for h in handles}}
    if len(authors)!=len(handles):raise ValueError('X account identities not resolved')
    query='('+' OR '.join('from:'+h for h in handles)+') -is:retweet'
    data=api('tweets/search/recent',{'query':query,'max_results':100,'tweet.fields':'created_at,author_id,referenced_tweets',
        'start_time':(now-timedelta(hours=24)).isoformat().replace('+00:00','Z')},token)
    if data.get('meta',{}).get('next_token'):raise ValueError('X search truncated: narrow watchlist or use a licensed streaming service')
    rows=[]
    for p in data.get('data',[]):
        if p.get('author_id') not in authors:raise ValueError('Unexpected X author')
        rows.append(dict(title=p['text'],summary='',url='https://x.com/'+authors[p['author_id']]+'/status/'+p['id'],
            source=authors[p['author_id']],author_id=p['author_id'],publish_time=p.get('created_at'),related_securities=[],
            references=p.get('referenced_tweets',[])))
    return rows


def analyze(row,spec,now,entities):
    e=event(row,spec['id'] if spec['id'] in FEEDS else 'external',now)
    text=e['title']+' '+str(row.get('summary',''))
    host=urlsplit(e['url']).hostname
    trusted=host in spec.get('article_hosts',[]) and spec.get('tier') in ('primary','newsroom','speaker')
    e.update(tier=spec['tier'] if trusted else 'unverified',provider=spec['id'],summary=str(row.get('summary',''))[:4000],
        author_id=row.get('author_id'),references=row.get('references',[]))
    # Exact aliases/cashtags only. Sector hypotheses never manufacture ticker membership.
    codes=set(e['codes'])
    for code,aliases in entities.items():
        if not isinstance(code,str) or not (security(code)==code or re.fullmatch(r'HK\.[0-9]{5}',code)):continue
        if any(specific_alias(a,provider=spec['id'],code=code) and re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)',text,re.I) for a in aliases):codes.add(code)
    codes.update('US.'+x for x in re.findall(r'\$([A-Z]{1,5})(?![A-Z])',text))
    e['codes']=sorted(codes)
    topics=[k for k,(pattern,_) in TOPICS.items() if re.search(pattern,text,re.I)]
    macro=any(k in topics for k in ('tariffs','rates','geopolitics'))
    e['scope']='macro' if spec['id'] in FEEDS or macro else 'security'
    e['analysis']=dict(topics=topics,hypotheses=[TOPICS[k][1] for k in topics],
        direction='undetermined',claim_status='authored_statement' if spec.get('tier')=='speaker' else 'reported_not_independently_verified',
        corroboration='not_established',model='deterministic headline/summary rules v1',
        next_checks=['核对原文、发布日期、政策是否生效及是否撤回','寻找独立来源；转载不算独立证实','结合价格、成交量和原有策略验证，不能把发言当作收益预测'])
    if spec['id'] not in FEEDS:
        e.update(action='observe',ttl_seconds=0,reason='External event requires verification')
        if trusted and not UNCERTAIN.search(text) and (topics or MAJOR.search(text)):
            if spec['tier']=='speaker':e.update(action='reduce',ttl_seconds=3600,reason='Attributed public statement: halve new entry cap; claim/direction unverified')
            elif macro:e.update(action='block',ttl_seconds=3600,reason='External major-event risk flag: entry cooling period; direction unverified')
            else:e.update(action='observe',ttl_seconds=0,reason='Corporate event observed; blanket 24-hour cooling disabled')
    if not trusted or not stamp(e['published_at']) or stamp(e['published_at'])>now:
        e.update(action='observe',ttl_seconds=0,reason='Unverified provenance/publication time')
    if spec.get('tier')=='discovery':
        attributed=(host in spec.get('article_hosts',[]) and row.get('publisher_name')==spec.get('publisher_name')
                    and urlsplit(row.get('publisher_url','')).hostname in spec.get('publisher_hosts',[]))
        e.update(tier='attributed_index' if attributed else 'unverified',action='observe',ttl_seconds=0,
            reason='Reuters headline indexed by Google News; original article not verified; research only',
            publisher_name=row.get('publisher_name'),publisher_url=row.get('publisher_url'),original_verified=False,
            publication_time_basis='aggregator supplied; original publication time unverified')
        e['analysis']['claim_status']='indexed_headline_not_original_verified'
    return e


def collect_external(codes,config_path=DEFAULT_CONFIG,clock=lambda:datetime.now(timezone.utc),fetch=rss,api=request_json,additional_entities=None,hk_identities=None):
    config=json.loads(Path(config_path).read_text());health={};events={}
    # HK symbols may be observed alongside US symbols, but observation alone
    # must not authorize HK entries before issuer coverage is validated.
    if not codes or any(not isinstance(c,str) or not (security(c)==c or re.fullmatch(r'HK\.[0-9]{5}',c)) for c in codes):
        raise ValueError('Explicit US or canonical HK watchlist required')
    entities=dict(config.get('entities',{}))
    for code,aliases in (additional_entities or {}).items():entities[code]=sorted(set(entities.get(code,[])+aliases))
    from .hk_news import valid_identity
    verified_hk={c:r for c,r in (hk_identities or {}).items() if valid_identity(r,c,clock())}
    for code,row in verified_hk.items():entities[code]=list(row['aliases'])
    hk_sources=[s['id'] for s in config['feeds'] if 'HK' in s.get('markets',[]) and s.get('tier') in ('primary','newsroom')]
    verified_attempts=0
    required=[]
    for spec in config['feeds']+[dict(id='x',name='X',tier='speaker',article_hosts=['x.com'],required=config['x'].get('required',True))]:
        key=spec['id'];started=clock()
        if spec.get('required',True):required.append(key)
        if key=='x' and not config['x'].get('enabled'):
            health[key]=dict(ok=False,checked_at=started.isoformat(),status='disabled_by_configuration');continue
        try:
            rows=x_posts(config['x'],started,api) if key=='x' else fetch(spec)
            invalid=0
            for row in rows:
                # Cheap literal prefilter before exact boundary matching; discovery can
                # cover the directory without compiling thousands of regexes per article.
                text=(str(row.get('title',''))+' '+str(row.get('summary',''))).casefold()
                relevant={c:a for c,a in entities.items() if any(x.casefold() in text for x in a)}
                e=analyze(row,spec,clock(),relevant)
                from .news_emergency import annotate
                if verified_attempts<4:
                    def bounded_fetch(address,hosts):
                        nonlocal verified_attempts
                        verified_attempts+=1
                        from .news_emergency import original_title
                        return original_title(address,hosts)
                    e=annotate(e,spec,entities,clock(),fetch=bounded_fetch)
                    if verified_attempts<4:
                        from .news_opportunity import annotate as annotate_opportunity
                        e=annotate_opportunity(e,spec,entities,clock(),fetch=bounded_fetch)
                events[e['id']]=e
                if e['tier']!='unverified' and (not stamp(e['published_at']) or stamp(e['published_at'])>clock()):invalid+=1
            health[key]=dict(ok=not invalid,checked_at=started.isoformat(),count=len(rows),unresolved_times=invalid)
        except Exception as exc:
            # Do not expose provider request headers, URLs or exception bodies containing credentials.
            health[key]=dict(ok=False,checked_at=started.isoformat(),error=type(exc).__name__,status='unavailable_or_unconfigured',retry_at=getattr(exc,'retry_at',None))
    ready=bool(required) and all(health[k]['ok'] for k in required)
    discovered={c for e in events.values() for c in e.get('codes',[]) if c in entities}
    for c in sorted(set(codes)|discovered):
        health[c]=dict(ok=ready,checked_at=clock().isoformat(),coverage='configured external sources only; not exhaustive')
        if c.startswith('HK.'):
            covered=c in verified_hk and bool(hk_sources) and all(health.get(k,{}).get('ok') is True for k in hk_sources)
            health[c].update(ok=bool(ready and covered),
                status='configured_hk_sources_monitored' if covered else 'hk_issuer_coverage_not_validated',
                source_ids=hk_sources,issuer_identity=verified_hk.get(c),
                coverage='Verified issuer aliases and configured public HK feeds only; not exhaustive announcements or breaking-news coverage')
    return dict(version=3,collected_at=clock().isoformat(),required_sources=required,health=health,events=list(events.values()),
        coverage='External providers only; X is not connected until access is configured; Truth Social direct connector not implemented')
