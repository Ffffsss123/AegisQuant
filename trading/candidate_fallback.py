"""Bounded ranked alternatives. Individual exclusions never reset global controls."""
from .policy import SafetyError

LIMIT=10

def ranked(scan,s,day,limit):
    seen=set();out=[]
    for c in scan.get('candidates',[]):
        code=c['code']
        if code in seen:continue
        seen.add(code)
        if s['positions'].get(code,{}).get('qty') or s['baseline'].get(code,0):continue
        if day+':'+code in s.get('candidate_checks',{}):continue
        out.append(c)
        if len(out)>=limit:break
    return out if limit>0 else []

def enable_for_session(db,scan,day,previous,now):
    """Explicit migration of an idle session; preserve earlier skips and daily claims."""
    import json
    from datetime import datetime
    from .moomoo import NY
    from .swing_engine import SwingEngine
    s=db.load()
    if s.get('candidate_fallback_enabled'):return s
    if s.get('env')!='SIMULATE' or s.get('purpose')!='strategy_simulation':raise SafetyError('Simulation strategy required')
    if s.get('paused') or s.get('stopped') or s.get('orders') or s.get('queue') or any(p['qty'] for p in s['positions'].values()) or any(s['baseline'].values()):raise SafetyError('Idle unpaused account required for fallback migration')
    if not SwingEngine.temporary_entry_active(s,now) or s.get('queue_day')!=day or day not in s['processed']:raise SafetyError('Explicit current-session window required')
    if scan.get('date')!=previous or not scan.get('complete') or scan.get('version')!=s.get('strategy_version'):raise SafetyError('Complete matching strategy scan required')
    seen=set()
    for ts,body in db.db.execute("SELECT ts,body FROM events WHERE kind='skipped'"):
        if datetime.fromisoformat(ts).astimezone(NY).date().isoformat()!=day:continue
        row=json.loads(body)
        if row.get('code'):seen.add(row['code'])
        if row.get('reason') not in ('Spread too wide','Budget or news filter','Quote wait exceeded 120 seconds'):raise SafetyError('Prior non-individual failure requires inspection')
    checks=s.setdefault('candidate_checks',{})
    for code in seen:checks[day+':'+code]=dict(code=code,time=now.isoformat(),result={'status':'previously_skipped'})
    pool=ranked(scan,dict(s,candidate_checks={}),day,LIMIT)
    s['queue']=[dict(code=c['code'],side='BUY',kind='STOCK') for c in pool if c['code'] not in seen]
    s.update(candidate_fallback_enabled=True,candidate_fallback_limit=LIMIT)
    db.save(s,'candidate_fallback_enabled',dict(session=day,limit=LIMIT,prior_skips=sorted(seen),remaining=[i['code'] for i in s['queue']]))
    return s
