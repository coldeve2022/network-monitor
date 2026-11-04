"""Release identity, also exposed by the local API."""
import json,sys
from pathlib import Path
VERSION='2026.10.28.1'
SCHEMA_VERSION=2

def build_info():
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    try:return json.loads((root/'web/static/build-info.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return {'version':VERSION,'schema_version':SCHEMA_VERSION,'build_id':'source'}
