"""Consume reviewed exchange filings; classification alone never permits orders."""
import hashlib,json
from datetime import date,datetime,timedelta
from urllib.parse import urlsplit
from pathlib import Path
from .hk_policy import HK
from .policy import SafetyError


def reviewed_class(root,code,metadata,now):
    root=Path(root).resolve()
    if now.tzinfo is None:raise SafetyError('Aware classification clock required')
    records=json.loads((root/'classification_review.json').read_text())['records']
    r=records[code];today=now.astimezone(HK).date()
    previous_month=today.replace(day=1)-timedelta(days=1)
    reviewed=datetime.fromisoformat(r['reviewed_at'])
    source=urlsplit(r['source'])
    file=Path(r['document_path']).resolve()
    if (r.get('code')!=code or metadata.get('code')!=code
        or not r.get('isin') or r['isin']!=metadata.get('isin')
        or r.get('kind')!='COMMON' or r.get('listed_on_exchange') is not True
        or r.get('status')!='source_reviewed_not_integrated'
        or r.get('period_end')!=previous_month.isoformat()
        or not previous_month<date.fromisoformat(r['submitted'])<=today
        or reviewed.tzinfo is None or reviewed>now
        or source.scheme!='https' or source.hostname not in ('www.hkexnews.hk','www1.hkexnews.hk')
        or not source.path.startswith('/listedco/listconews/sehk/') or not source.path.endswith('.pdf')
        or file.parent!=root/'classification_sources'
        or hashlib.sha256(file.read_bytes()).hexdigest()!=r.get('source_document_sha256')):
        raise SafetyError('HK share-class source identity or freshness failed')
    return dict(kind='COMMON',share_class_reviewed=True,classification_source=r['source'],
                classification_period_end=r['period_end'],classification_sha256=r['source_document_sha256'],
                orders_enabled=False)
