"""Conservative offline HK fee estimate; not a broker fee confirmation.

Source: https://www.moomoo.com/au/support/topic6_397 (checked 2026-09-17 NZ).
Single order, one trading day. Supply each execution separately for third-party
rounding; unknown stamp exemption is charged conservatively. No promotions assumed.
"""
from decimal import Decimal, ROUND_CEILING
from .hk_policy import amount
from .policy import SafetyError

SOURCE='https://www.moomoo.com/au/support/topic6_397'

def order_estimate(execution_amounts_hkd, *, stamp_exempt=False):
    if not isinstance(stamp_exempt,bool):raise SafetyError('Explicit stamp classification required')
    values=[amount(v) for v in execution_amounts_hkd]
    if not values or any(v<=0 for v in values):raise SafetyError('Positive HK execution amounts required')
    def cents(v):return v.quantize(Decimal('.01'),rounding=ROUND_CEILING)
    total=sum(values)
    parts=dict(commission=cents(max(Decimal(3),total*Decimal('.0003'))),platform=Decimal(15),
               settlement=Decimal(0),stamp=Decimal(0),trading=Decimal(0),sfc=Decimal(0),frc=Decimal(0))
    for v in values:
        parts['settlement']+=cents(v*Decimal('.000042'))
        parts['stamp']+=Decimal(0) if stamp_exempt else (v*Decimal('.001')).to_integral_value(rounding=ROUND_CEILING)
        parts['trading']+=cents(max(Decimal('.01'),v*Decimal('.0000565')))
        parts['sfc']+=cents(max(Decimal('.01'),v*Decimal('.000027')))
        parts['frc']+=cents(v*Decimal('.0000015'))
    return dict(currency='HKD',components={k:str(v) for k,v in parts.items()},total_hkd=str(sum(parts.values())),
                source=SOURCE,estimated=True,broker_confirmed=False,orders_enabled=False)


def order_upper_bound(notional_hkd,filled_qty,*,stamp_exempt=False):
    """Whole-share paper orders: bound unknown per-execution rounding.

    At most filled_qty executions are possible when fractional shares are not
    allowed. For n positive executions, sum(ceil(x_i)) <= ceil(sum(x_i))+n-1.
    Four cent-rounded levies plus dollar-rounded stamp duty use that bound;
    order commission and platform fee remain once per order.
    """
    qty=amount(filled_qty)
    if qty<=0 or qty!=qty.to_integral_value():raise SafetyError('Positive whole-share fill required')
    base=order_estimate([notional_hkd],stamp_exempt=stamp_exempt)
    extra=(qty-1)*(Decimal('.04')+(Decimal(0) if stamp_exempt else Decimal(1)))
    base.update(total_hkd=str(amount(base['total_hkd'])+extra),rounding_allowance_hkd=str(extra),
                model='whole_share_execution_rounding_upper_bound',filled_qty=str(qty))
    return base
