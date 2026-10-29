"""Narrow corporate-event exits; public text cannot set quantities, accounts or switches."""
import hashlib,re
from .news_identity import specific_alias
from html.parser import HTMLParser
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.parse import urlsplit
from .news import stamp,coverage_age_limit,UNCERTAIN

VERSION='verified_corporate_adverse_v1'
RULES={
 'bankruptcy':r'(?:files?|filed|filing)\s+(?:for\s+)?(?:chapter\s+(?:11|7)(?:\s+bankruptcy)?|bankruptcy)(?:\b|$)',
 'debt_default':r'(?:defaults?|defaulted)\s+on\s+(?:its\s+)?(?:debt|bond|loan)(?:s?\b)',
 'guidance_cut':r'(?:cuts?|cut|slashes?|slashed|lowers?|lowered)\s+(?:its\s+)?(?:full[ -]year\s+|annual\s+|quarterly\s+)?(?:earnings|profit|revenue)\s+(?:guidance|forecast|outlook)\b',
}
HEDGED=re.compile(r'\b(?:may|might|could|would|if|denies?|denied|not|avoids?|averts?|rumou?rs?|considering|plans? to|expected to|reportedly)\b|\?',re.I)
class PageTitle(HTMLParser):
 def __init__(self):super().__init__();self.inside=False;self.parts=[]
 def handle_starttag(self,tag,attrs):
  if tag=='title':self.inside=True
 def handle_endtag(self,tag):
  if tag=='title':self.inside=False
 def handle_data(self,data):
  if self.inside:self.parts.append(data)
def normalized(s):return re.sub(r'\W+',' ',s.casefold()).strip()
def original_title(address,hosts):
 class NoRedirect(HTTPRedirectHandler):
  def redirect_request(self,*a,**kw):return None
 u=urlsplit(address)
 if u.scheme!='https' or u.hostname not in hosts or u.username or u.password:raise ValueError('Untrusted article address')
 with build_opener(NoRedirect).open(Request(address,headers={'User-Agent':'LocalQuantNews/1.0'}),timeout=5) as response:
  if urlsplit(response.url).hostname not in hosts:raise ValueError('Unexpected article host')
  raw=response.read(1_000_001)
 if len(raw)>1_000_000:raise ValueError('Oversized article')
 p=PageTitle();p.feed(raw.decode('utf-8',errors='replace'))
 return ''.join(p.parts),hashlib.sha256(raw).hexdigest()
def annotate(e,spec,entities,now,fetch=original_title):
 if e.get('tier') not in ('primary','newsroom') or e.get('scope')!='security':return e
 t=stamp(e.get('published_at'))
 if not t or not 0<=(now-t).total_seconds()<=1800:return e
 title=e['title'].strip()
 if HEDGED.search(title) or UNCERTAIN.search(title):return e
 matches=[]
 for code in e.get('codes',[]):
  aliases=[*entities.get(code,[]),'$'+code.removeprefix('US.')]
  for alias in aliases:
   if not specific_alias(alias,provider=e.get('provider'),code=code) or len(alias)<3:continue
   subject=re.match(r'^'+re.escape(alias)+r"(?:['’]s)?\s+",title,re.I)
   if not subject:continue
   tail=title[subject.end():]
   for kind,pattern in RULES.items():
    if re.match(pattern,tail,re.I):matches.append((code,kind))
 if len(set(matches))!=1:return e
 code,kind=matches[0]
 try:
  fetched,digest=fetch(e['url'],spec.get('article_hosts',[]))
  if not normalized(fetched).startswith(normalized(title)):return e
 except Exception:return e
 e['emergency_exit']=dict(version=VERSION,code=code,direction='negative',kind=kind,original_verified=True,verified_at=now.isoformat(),article_sha256=digest,basis='configured publisher RSS and matching original page title; event report, not a price forecast')
 e.update(action='block',ttl_seconds=86400,reason='Verified original publisher report of adverse corporate event; block new entries and request long-position exit')
 return e

def exit_evidence(snapshot,code,now):
 matches=[]
 collected=stamp(snapshot.get('collected_at'))
 if not collected or not 0<=(now-collected).total_seconds()<=coverage_age_limit(snapshot):return matches
 for e in snapshot.get('events',[]):
  a=e.get('emergency_exit',{});h=snapshot.get('health',{}).get(e.get('provider'),{})
  times=[stamp(e.get('published_at')),stamp(e.get('observed_at')),stamp(a.get('verified_at')),stamp(h.get('checked_at'))]
  if a.get('version')!=VERSION or a.get('code')!=code or a.get('original_verified') is not True or a.get('kind') not in RULES:continue
  if e.get('tier') not in ('primary','newsroom') or e.get('scope')!='security' or code not in e.get('codes',[]) or not h.get('ok') or not all(times):continue
  published,seen,verified,checked=times
  if not 0<=(now-published).total_seconds()<=1800 or seen>now or verified>now or not 0<=(now-checked).total_seconds()<=coverage_age_limit(snapshot):continue
  if a.get('direction')=='negative':matches.append(dict(event_id=e['id'],code=code,kind=a['kind'],url=e['url'],published_at=e['published_at']))
 return matches

def issuer_aliases(codes,root):
 """Use exchange directory company names, not guessed sector relationships."""
 import csv
 from pathlib import Path
 wanted=set(codes);result={}
 for path in sorted((Path(root)/'directory').glob('*.txt')):
  try:
   with path.open() as source:
    for row in csv.DictReader(source,delimiter='|'):
     symbol=row.get('Symbol') or row.get('ACT Symbol')
     code='US.'+symbol if symbol else None
     if code not in wanted:continue
     name=row.get('Security Name','').strip()
     name=re.split(r'\s+-\s+|\s+(?:Class\s+[A-Z]|Common\s+(?:Stock|Shares)|Ordinary\s+Shares|American\s+Depositary)\b',name,maxsplit=1,flags=re.I)[0].strip()
     short=re.sub(r'(?:,?\s+(?:Inc\.?|Incorporated|Corporation|Corp\.?|plc|Ltd\.?|Limited))+$','',name,flags=re.I).strip()
     result[code]=[x for x in {name,short} if len(x)>=3]
  except (OSError,UnicodeError):continue
 return result
