"""Release only durably proven pre-submission failures; never infer from absence."""
import json
from pathlib import Path
from .shared_capital import admission_lock
from .policy import SafetyError


def recover(journal,reservations,stock,root):
    with admission_lock(root):
        portfolio=reservations.db.execute('SELECT portfolio FROM identity WHERE id=1').fetchone()[0]
        if stock.get('env')!='SIMULATE' or stock.get('hk_account')!=journal.account or stock.get('portfolio_id')!=portfolio:
            raise SafetyError('Bound HK portfolio required for aborted recovery')
        recovered=[]
        for tag,body,evidence in journal.db.execute('SELECT tag,request,evidence FROM aborted_admissions'):
            request=json.loads(body);proof=json.loads(evidence)
            if request.get('account')!=journal.account or request.get('env')!='SIMULATE' or proof.get('broker_call_started') is not False:
                raise SafetyError('Invalid pre-submission failure evidence')
            if journal.db.execute('SELECT 1 FROM submissions WHERE tag=?',(tag,)).fetchone():
                raise SafetyError('Submission claim exists; retain capital')
            reservations.db.execute('BEGIN IMMEDIATE')
            try:
                row=reservations.db.execute('SELECT state FROM holds WHERE key=?',(tag,)).fetchone()
                if row and row[0]!='RELEASED':
                    if row[0]!='HELD' or reservations.db.execute('SELECT 1 FROM settled_fills WHERE key=?',(tag,)).fetchone():
                        raise SafetyError('Filled capital cannot be released')
                    # Invalidate before reducing occupied capital; fresh production
                    # and broker reconciliation are required before any new buy.
                    (Path(root)/'data/trading/hk_risk_snapshot.json').unlink(missing_ok=True)
                    reservations.db.execute("UPDATE holds SET state='RELEASED',evidence=? WHERE key=?",(evidence,tag))
                    recovered.append(tag)
                reservations.db.commit()
            except Exception:reservations.db.rollback();raise
        return recovered
