"""Conservative positive corporate-news evidence; never a price prediction."""
import re
from .news_identity import specific_alias
from datetime import timedelta
from .news import stamp,coverage_age_limit,UNCERTAIN
from .news_emergency import HEDGED,normalized,original_title
VERSION='verified_guidance_raise_v2'
PUBLICATION_MAX_AGE=48*3600
VERIFICATION_MAX_AGE=1800
RULES={'guidance_raise':r'(?:raises?|raised|increases?|increased)\s+(?:its\s+)?(?:full[ -]year\s+|annual\s+|quarterly\s+)?(?:earnings|profit|revenue)\s+(?:guidance|forecast|outlook)\b'}
def annotate(e,spec,entities,now,fetch=original_title):
 if e.get('tier') not in ('primary','newsroom') or e.get('scope')!='security':return e
 t=stamp(e.get('published_at'))
 if not t or not 0<=(now-t).total_seconds()<=PUBLICATION_MAX_AGE:return e
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
 e['entry_opportunity']=dict(version=VERSION,code=code,direction='positive',kind=kind,original_verified=True,verified_at=now.isoformat(),article_sha256=digest,basis='configured publisher RSS and matching original page title; event report, not a price forecast')
 return e

def entry_evidence(snapshot,code,now):
 matches=[]
 collected=stamp(snapshot.get('collected_at'))
 if not collected or not 0<=(now-collected).total_seconds()<=coverage_age_limit(snapshot):return matches
 for e in snapshot.get('events',[]):
  a=e.get('entry_opportunity',{});h=snapshot.get('health',{}).get(e.get('provider'),{})
  times=[stamp(e.get('published_at')),stamp(e.get('observed_at')),stamp(a.get('verified_at')),stamp(h.get('checked_at'))]
  if a.get('version')!=VERSION or a.get('code')!=code or a.get('original_verified') is not True or a.get('kind') not in RULES:continue
  if e.get('tier') not in ('primary','newsroom') or e.get('scope')!='security' or code not in e.get('codes',[]) or not h.get('ok') or not all(times):continue
  published,seen,verified,checked=times
  if not 0<=(now-published).total_seconds()<=PUBLICATION_MAX_AGE or seen>now or not 0<=(now-verified).total_seconds()<=VERIFICATION_MAX_AGE or not 0<=(now-checked).total_seconds()<=coverage_age_limit(snapshot):continue
  if a.get('direction')=='positive':matches.append(dict(event_id=e['id'],code=code,kind=a['kind'],url=e['url'],published_at=e['published_at'],verified_at=a['verified_at'],verification_expires_at=(verified+timedelta(seconds=VERIFICATION_MAX_AGE)).isoformat()))
 return matches
