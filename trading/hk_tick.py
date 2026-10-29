"""HKEX table 01 phase 2, effective 2026-08-03. Not other instruments.

Source: https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2026/ReductionofMinimumSpreadsFAQP2_E.pdf
Effective date verified against HKEX Rules of the Exchange updates.
"""
from datetime import date
from .hk_policy import amount
from .policy import SafetyError


def tick_size(price, *, table, kind, session):
    if table != '01' or kind != 'COMMON' or date.fromisoformat(session) < date(2026,8,3):
        raise SafetyError('Verified current common-share spread table 01 required')
    p=amount(price)
    if not amount('.01') <= p <= amount('9995'):
        raise SafetyError('Price outside HKEX table 01')
    for maximum,step in (('.25','.001'),('10','.005'),('20','.01'),('50','.02'),
                         ('100','.05'),('200','.1'),('500','.2'),('1000','.5'),
                         ('2000','1'),('5000','2'),('9995','5')):
        if p <= amount(maximum):return amount(step)
    raise SafetyError('No verified HK tick')
