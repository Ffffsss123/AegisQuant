"""HK issuer-name provenance for configured-feed monitoring, not exhaustive news."""
import hashlib,json,re,subprocess
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit
from .hk_policy import HK

SOURCE='https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx'

def reviewed_full_name(root,code,row,review,now):
    """Keep a source-checked issuer name without extending share-class admission."""
    if not isinstance(review,dict):return None
    name=review.get('issuer_name_en');short=row.get('name_en')
    if (not isinstance(name,str) or not isinstance(short,str) or len(name.strip())<8
        or name.casefold()==short.casefold() or not name.casefold().startswith(short.casefold())
        or review.get('issuer_name_page')!=1 or review.get('code')!=code
        or review.get('isin')!=row.get('isin') or review.get('kind')!='COMMON'
        or review.get('listed_on_exchange') is not True
        or review.get('status')!='source_reviewed_not_integrated'):
        return None
    try:
        checked=datetime.fromisoformat(review['reviewed_at'])
        source=urlsplit(review['source'])
        document=Path(review['document_path']).resolve()
        if (checked.tzinfo is None or checked>now or source.scheme!='https'
            or source.hostname not in ('www.hkexnews.hk','www1.hkexnews.hk')
            or not source.path.startswith('/listedco/listconews/sehk/')
            or not source.path.endswith('.pdf') or document.parent!=Path(root).resolve()/'classification_sources'
            or hashlib.sha256(document.read_bytes()).hexdigest()!=review['source_document_sha256']):
            return None
        text=subprocess.run(['pdftotext','-f','1','-l','1',str(document),'-'],capture_output=True,
            text=True,encoding='utf-8',errors='replace',timeout=3,check=True).stdout
        match=re.search(r'Name of Issuer:\s*(.*?)\s*Date Submitted:',text,re.S)
        issuer=' '.join(match.group(1).split()) if match else ''
        if issuer.casefold()!=name.strip().casefold() and not issuer.casefold().startswith(name.strip().casefold()+' ('):
            return None
    except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError):return None
    return name.strip()

def identities(root,codes,now):
    wanted=[c for c in codes if isinstance(c,str) and re.fullmatch(r'HK\.[0-9]{5}',c)]
    if not wanted:return {}
    try:
        base=Path(root);catalog=json.loads((base/'catalog.json').read_text())
        basic=json.loads((base/'basic.json').read_text())
        stamp=datetime.fromisoformat(basic['queried_at'])
        if (now.tzinfo is None or stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=86400
            or catalog['source']!=SOURCE or catalog['source_date']!=now.astimezone(HK).date().isoformat()
            or hashlib.sha256((base/'ListOfSecurities.xlsx').read_bytes()).hexdigest()!=catalog['sha256']):return {}
        broker={r['code']:r for r in basic['rows']};result={}
        try:reviews=json.loads((base/'classification_review.json').read_text()).get('records',{})
        except (OSError,ValueError,TypeError):reviews={}
        for code in wanted:
            row=catalog.get('rows',{}).get(code,{});b=broker.get(code,{})
            if (row.get('code')!=code or row.get('currency')!='HKD' or b.get('delisting') is not False
                or row.get('name_zh')!=b.get('name') or row.get('lot_size')!=b.get('lot_size')):continue
            names=[row.get('name_en'),row.get('name_zh')]
            if any(not isinstance(n,str) or not n.strip() for n in names):continue
            aliases=[n for n in names if len(n.strip())>=3]
            full=reviewed_full_name(base,code,row,reviews.get(code),now)
            if full:aliases.append(full)
            if not aliases:continue
            result[code]=dict(code=code,aliases=aliases,source=SOURCE,source_date=catalog['source_date'],
                document_sha256=catalog['sha256'],status='hkex_moomoo_identity_matched')
        return result
    except (OSError,ValueError,TypeError,KeyError):return {}


def valid_identity(record,code,now):
    return (isinstance(record,dict) and record.get('status')=='hkex_moomoo_identity_matched'
            and record.get('code')==code and record.get('source')==SOURCE
            and record.get('source_date')==now.astimezone(HK).date().isoformat()
            and isinstance(record.get('document_sha256'),str)
            and bool(re.fullmatch('[0-9a-f]{64}',record['document_sha256']))
            and isinstance(record.get('aliases'),list) and bool(record['aliases'])
            and all(isinstance(a,str) and len(a.strip())>=3 for a in record['aliases']))
