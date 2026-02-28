import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_BACKEND = ROOT / 'python-backend'
if str(PY_BACKEND) not in sys.path:
    sys.path.insert(0, str(PY_BACKEND))
