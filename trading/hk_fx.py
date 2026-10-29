"""Dated informational USD/HKD references for simulation accounting, not FX trades."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET
from .policy import SafetyError

ECB = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
NS = '{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}'


def validate_reference(record, now):
    from .hk_policy import amount
    try:
        if now.tzinfo is None or record.get('verified') is not True or not record.get('source'):
            raise ValueError()
        rate = amount(record['hkd_per_usd'])
        if rate <= 0:
            raise ValueError()
        if record.get('time_precision') == 'date':
            # No invented timestamp: accept today's or yesterday's dated ECB
            # reference only. Weekends/holidays receive no freshness extension.
            day = date.fromisoformat(record['source_date'])
            age = (now.astimezone(ZoneInfo('Europe/Berlin')).date()-day).days
            if record['source'] != ECB or age not in (0, 1):
                raise ValueError()
            usd, hkd = amount(record['usd_per_eur']), amount(record['hkd_per_eur'])
            if usd <= 0 or hkd <= 0 or rate != hkd/usd:
                raise ValueError()
        else:
            stamp = datetime.fromisoformat(record['as_of'])
            if stamp.tzinfo is None or not timedelta(0) <= now-stamp <= timedelta(days=1):
                raise ValueError()
        return rate
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        raise SafetyError('Verified recent USD/HKD reference required')


def parse_ecb(raw, now):
    from .hk_policy import amount
    if len(raw)>100000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise SafetyError('Unsafe ECB reference document')
    try:
        root=ET.fromstring(raw)
        days=[node for node in root.iter(NS+'Cube') if 'time' in node.attrib]
        if len(days)!=1:
            raise ValueError()
        rows=list(days[0]); rates={}
        for currency in ('USD','HKD'):
            matches=[node for node in rows if node.tag==NS+'Cube' and node.get('currency')==currency]
            if len(matches)!=1:
                raise ValueError()
            rates[currency]=amount(matches[0].get('rate'))
        if min(rates.values())<=0:
            raise ValueError()
        record=dict(source=ECB,source_date=days[0].get('time'),time_precision='date',
                    usd_per_eur=str(rates['USD']),hkd_per_eur=str(rates['HKD']),
                    hkd_per_usd=str(rates['HKD']/rates['USD']),verified=True,
                    purpose='simulation_accounting_reference_only',orders_enabled=False)
        validate_reference(record,now)
        return record
    except (ET.ParseError,TypeError,ValueError,ZeroDivisionError):
        raise SafetyError('Invalid ECB USD/HKD reference')


def validate_accounting_binding(record,portfolio_id,account,now):
    """Keep the verified activation reference fixed across the portfolio lifetime."""
    try:
        bound=datetime.fromisoformat(record['bound_at'])
        if (record.get('kind')!='fixed_simulation_accounting' or record.get('env')!='SIMULATE'
            or record.get('portfolio_id')!=portfolio_id or record.get('account')!=account
            or not portfolio_id or not account or bound.tzinfo is None or now.tzinfo is None or bound>now):
            raise ValueError()
        rate=validate_reference(record['reference'],bound)
        from .hk_policy import amount
        if amount(record['hkd_per_usd'])!=rate:raise ValueError()
        return rate
    except (KeyError,ValueError,TypeError):
        raise SafetyError('Verified fixed portfolio FX binding required')
