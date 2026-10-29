"""Independent SIMULATE sleeve arithmetic, not an executor authorization."""
from decimal import Decimal, InvalidOperation
from .policy import SafetyError

SLEEVES = ('US_STOCK', 'US_OPTION', 'HK')


def budget_view(sleeves, *, env, loss_latched):
    """Use reconciled USD values including fees and conservative inventory marks.

    Caller must prove account ownership, fresh valuation, reservations and all
    controls under the admission lock. No capital migration or ledger writes.
    Reserve cash and quote-only observer balances must never enter these inputs.
    """
    if not isinstance(loss_latched, bool):
        raise SafetyError('Explicit persisted loss latch required')
    if env != 'SIMULATE' or not isinstance(sleeves, dict) or set(sleeves) != set(SLEEVES):
        raise SafetyError('Three explicit simulation sleeves required')
    def amount(value):
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise SafetyError('Invalid sleeve amount')
        try: result = Decimal(str(value))
        except InvalidOperation as exc: raise SafetyError('Invalid sleeve amount') from exc
        if not result.is_finite(): raise SafetyError('Nonfinite sleeve amount')
        return result
    available, equity = {}, Decimal(0)
    for name in SLEEVES:
        s = sleeves[name]
        if not isinstance(s, dict) or not all(k in s for k in ('initial', 'cash', 'equity', 'deployed', 'reserved', 'exit_fees')):
            raise SafetyError('Incomplete sleeve budget')
        a = {k: amount(s[k]) for k in ('initial', 'cash', 'equity', 'deployed', 'reserved', 'exit_fees')}
        if a['initial'] != 500 or any(a[k] < 0 for k in ('cash', 'deployed', 'reserved', 'exit_fees')):
            raise SafetyError('Sleeve budget or cash violation')
        # Gains do not expand the authorized investment ceiling. Reservations
        # and exit fees remain local; unused peer cash cannot fund this sleeve.
        available[name] = max(Decimal(0), min(a['cash'], a['initial'] - a['deployed']) - a['reserved'] - a['exit_fees'])
        equity += a['equity']
    loss = max(Decimal(0), Decimal(1500) - equity)
    stopped = loss_latched or loss >= 100
    return dict(available={k: Decimal(0) if stopped else v for k,v in available.items()},
                combined_loss=loss, entry_block=stopped, loss_latched=stopped)
