"""Protection-test provenance for this exact local Python implementation."""
import hashlib,json
from pathlib import Path

def fingerprint(root):
    root=Path(root);digest=hashlib.sha256()
    for p in sorted([*root.glob('trading/*.py'),*root.glob('tests/test*.py')]):
        digest.update(str(p.relative_to(root)).encode());digest.update(p.read_bytes())
    return digest.hexdigest()

def protection_passed(root):
    try:
        r=json.loads((Path(root)/'data/trading/protection_validation.json').read_text())
        return r.get('passed') is True and r.get('tests',0)>=163 and r.get('fingerprint')==fingerprint(root)
    except (OSError,ValueError):return False
