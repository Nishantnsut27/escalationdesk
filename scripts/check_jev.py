"""Explicit OpenRouter Jev check with the labelled import fixture; consumes provider quota."""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import JEV_KEY
from backend.evidence import EvidenceContext
from backend.imports import parse_logs
from backend.provider import jev_http_client
from backend.triage import JevTriage


async def main():
    if not JEV_KEY:
        raise SystemExit('Configure OPENROUTER_API_KEY in .env or backend/.env first.')
    rows, summary = parse_logs((ROOT / 'examples/import-check.jsonl').read_text(encoding='utf-8'))
    context = EvidenceContext(SimpleNamespace(collection={'symptom': 'Inspect the supplied observations.', 'summary': json.dumps(summary)}))
    context.records = {row['id']: {k: v for k, v in row.items() if k != 'original'} for row in rows}
    usage = {}
    async with jev_http_client() as client:
        result = await JevTriage(client).classify(context, usage, lambda _: None, time.time() + 300)
    path = ROOT / '.cache/phase7' / ('jev-check-' + str(time.time_ns()) + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'fixture': 'examples/import-check.jsonl (artificial)', 'triage': result}, indent=2), encoding='utf-8')
    print(json.dumps({key: result.get(key) for key in ('state', 'reason', 'category', 'confidence', 'returned_model', 'actual_usage', 'requests', 'duration_ms', 'diagnostics')}))
    print(str(path))
    return 0 if result['state'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
