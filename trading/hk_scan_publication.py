"""Publish complete HK screening results without exposing partial runs."""
import json
import os
import tempfile
from pathlib import Path
from datetime import date, datetime
from .policy import SafetyError


def publish(directory, result, expected, now):
    """Caller holds scanner's single-instance lock; progress is a separate file."""
    directory = Path(directory)
    if now.tzinfo is None:
        raise SafetyError('HK publication clock requires timezone')
    date.fromisoformat(expected)
    if (result.get('date') != expected or result.get('complete') is not True
            or result.get('errors') or result.get('orders_enabled') is not False
            or not isinstance(result.get('candidates'), list)
            or not isinstance(result.get('signals'), dict)
            or not result.get('scope')):
        raise SafetyError('Incomplete or mismatched HK scan cannot be published')
    codes = [r.get('code') for r in result['candidates']]
    if (any(not isinstance(c, str) or not c.startswith('HK.') for c in codes)
            or len(codes) != len(set(codes))
            or any(c not in result['signals'] for c in codes)):
        raise SafetyError('HK candidates lack unique signal identities')
    target = directory / 'scan.json'
    if target.exists():
        previous = json.loads(target.read_text())
        if date.fromisoformat(previous['date']) > date.fromisoformat(expected):
            raise SafetyError('Cannot replace newer HK scan with older result')
    payload = dict(result, published_at=now.isoformat())
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.scan-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return payload
