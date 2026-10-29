"""News-led discovery independent of daily candidate membership; no orders."""
from .news import assess


def opportunity_pool(snapshot, now):
    codes=sorted({c for e in snapshot.get('events',[]) for c in e.get('codes',[])
                  if isinstance(c,str) and c.startswith(('US.','HK.'))})
    opportunities=[]
    for code in codes:
        decision=assess(snapshot,code,now)
        if decision['entry_evidence']:
            opportunities.append(dict(code=code,market=code.split('.')[0],
                evidence=decision['entry_evidence'],news_multiplier=decision['multiplier'],
                news_blocked=decision['global_block'] or decision['multiplier']<=0,
                status='requires_market_strategy_and_account_validation'))
    return dict(version=1,generated_at=now.isoformat(),source_collected_at=snapshot.get('collected_at'),
                candidate_membership_required=False,orders_enabled=False,opportunities=opportunities)


def extend_us_scan(scan,pool):
    """Promote already-validated daily signals, never fabricate missing history."""
    from .swing_model import VERSION,SELECTION_PROFILE
    if scan.get('version')!=VERSION or scan.get('complete') is not True:return scan
    result=dict(scan,candidates=list(scan.get('candidates',[])),news_pending=[])
    present={r['code'] for r in result['candidates']}
    for row in pool['opportunities']:
        code=row['code']
        if row['market']!='US' or row['news_blocked'] or code in present:continue
        signal=scan.get('signals',{}).get(code)
        if not signal or code not in scan.get('entry_verified_codes',[]):
            result['news_pending'].append(dict(code=code,reason='verified daily history and classification required'));continue
        if not signal.get('buy') or not signal.get('liquid'):continue
        if scan.get('selection_profile')==SELECTION_PROFILE and not signal.get('volatile_trend'):continue
        result['candidates'].append(dict(signal,code=code,news_entry_evidence=row['evidence'],selection_origin='verified_news'))
        present.add(code)
    priority={r['code'] for r in pool['opportunities'] if not r['news_blocked']}
    result['candidates'].sort(key=lambda r:r['code'] not in priority)
    return result
