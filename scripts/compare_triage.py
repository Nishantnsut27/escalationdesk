"""Freeze one evaluation case; explicitly compare modes with --live. Uses isolated storage."""
import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import db
from backend.config import GROQ_KEY, JEV_KEY
from backend.evidence import EvidenceTools, encoded
from backend.imports import parse_logs
from backend.investigator import Investigator, collect_baseline
from backend.provider import groq_http_client, jev_http_client
from backend.triage import JevTriage, triage_payload

BASE = ROOT / '.cache/phase7/comparison'


def freeze(case, source=None):
    directory = BASE / case
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'snapshot.json'
    if target.exists():
        snapshot = json.loads(target.read_text(encoding='utf-8'))
        digest = snapshot.pop('sha256')
        if hashlib.sha256(encoded(snapshot)).hexdigest() != digest:
            raise ValueError('Frozen snapshot hash mismatch; preserve the original before preparing another version.')
        return {**snapshot, 'sha256': digest}
    if case == 'imported':
        source = source or ROOT / 'examples/import-check.jsonl'
        rows, summary = parse_logs(Path(source).read_text(encoding='utf-8'))
        kind = 'artificial_import_fixture'
    else:
        if not source:
            raise ValueError('Provide --source with a saved live_check JSON artifact for this case.')
        captured = json.loads(Path(source).read_text(encoding='utf-8'))
        rows = captured['evidence']
        if not isinstance(rows, list) or not rows:
            raise ValueError('Source must contain retained evidence observations.')
        if case == 'incomplete':
            rows = [r for r in rows if r['kind'] == 'log' and r['service'] in {'gateway', 'order'}]
        summary = None
        kind = 'restricted_controlled_recording' if case == 'incomplete' else 'controlled_recording'
    # Expected answers, scenario names, reports and controller metadata never enter the evidence view.
    rows = [{k: v for k, v in row.items() if k not in {'user_id', 'run_id', 'collection_id', 'scenario', 'fault', 'expected'}} for row in rows]
    snapshot = {'provenance': kind, 'source': str(Path(source).resolve()), 'evidence': rows, 'summary': summary}
    snapshot['sha256'] = hashlib.sha256(encoded(snapshot)).hexdigest()
    target.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
    return snapshot


def install_snapshot(case, snapshot):
    db.DATA = BASE / case / 'data'
    db.init()
    owner, scope = 'phase7-evaluator', 'frozen-evidence'
    db.execute('INSERT OR IGNORE INTO users VALUES(?,?,?,?,?)', (owner, owner, 'phase7@evaluation.invalid', 'login-disabled', db.now()))
    imported = snapshot['summary'] is not None
    if imported:
        db.execute('INSERT OR IGNORE INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(?,?,?,?,?,?,?,?)',
                   (scope, owner, 'Evaluation observations', 'Inspect the supplied observations.', 'paste', db.now(), snapshot['sha256'], json.dumps(snapshot['summary'])))
    else:
        db.execute('INSERT OR IGNORE INTO runs(id,user_id,scenario,state,created_at) VALUES(?,?,?,?,?)',
                   (scope, owner, 'recorded', 'completed', db.now()))
        db.execute('INSERT OR IGNORE INTO incidents VALUES(?,?,?,?,?,?,?)', (scope, scope, owner, 'Evaluation observations', 'resolved', db.now(), db.now()))
    field = 'collection_id' if imported else 'run_id'
    for row in snapshot['evidence']:
        db.execute(f'INSERT OR IGNORE INTO evidence(id,{field},user_id,kind,service,timestamp,payload) VALUES(?,?,?,?,?,?,?)',
                   (row['id'], scope, owner, row['kind'], row['service'], row.get('timestamp'), json.dumps(row)))
    return owner, scope, imported


async def run(args):
    snapshot = freeze(args.case, args.source)
    owner, scope, imported = install_snapshot(args.case, snapshot)
    def tools():
        return EvidenceTools(None if imported else scope, owner, groq=True, collection_id=scope if imported else None)
    baseline, _ = collect_baseline(tools(), lambda *a: None)
    payload, triage_scope = triage_payload(baseline)
    print(json.dumps({'case': args.case, 'snapshot_sha256': snapshot['sha256'], 'observations': len(snapshot['evidence']),
                      'triage_bytes': len(encoded(payload)), 'triage_groups': len(triage_scope['groups']), 'live': args.live}), flush=True)
    if not args.live:
        return
    modes = ['jev', 'groq', 'combined'] if args.mode == 'all' else [args.mode]
    if any(mode != 'jev' for mode in modes) and not GROQ_KEY:
        raise ValueError('Groq is not configured.')
    if 'jev' in modes and not JEV_KEY:
        raise ValueError('OpenRouter Jev is not configured.')
    async with groq_http_client() as groq, jev_http_client() as jev:
        classifier = JevTriage(jev)
        worker = Investigator(groq, triage=classifier)
        for mode in modes:
            output = {'case': args.case, 'mode': mode, 'snapshot_sha256': snapshot['sha256'], 'provenance': snapshot['provenance']}
            if mode == 'jev':
                usage = {}
                result = await classifier.classify(baseline, usage, lambda _: None, time.time() + 300)
                output.update(triage=result, state=result['state'])
            else:
                id = db.uid()
                usage = {'triage': {'requested': True, 'state': 'pending'}} if mode == 'combined' else {}
                field = 'collection_id' if imported else 'incident_id'
                db.execute(f'INSERT INTO investigations(id,{field},user_id,engine,state,created_at,usage) VALUES(?,?,?,?,?,?,?)',
                           (id, scope, owner, 'groq', 'queued', db.now(), json.dumps(usage)))
                await worker.investigate(id)
                row = db.one('SELECT * FROM investigations WHERE id=?', (id,))
                output.update(state=row['state'], investigation_id=id, report=json.loads(row['report']) if row['report'] else None,
                              usage=json.loads(row['usage']), activity=json.loads(row['activity']), error=row['error'])
            path = BASE / args.case / f'{mode}-{time.time_ns()}.json'
            path.write_text(json.dumps(output, indent=2), encoding='utf-8')
            print(json.dumps({'mode': mode, 'state': output['state'], 'artifact': str(path)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', choices=['pool', 'slow', 'imported', 'incomplete'])
    parser.add_argument('--source', type=Path, help='Saved live_check artifact, or UTF-8 logs for the imported case. Used only when freezing a new snapshot.')
    parser.add_argument('--mode', choices=['jev', 'groq', 'combined', 'all'], default='all')
    parser.add_argument('--live', action='store_true', help='Explicitly consume configured provider quota; otherwise only freeze and validate inputs.')
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == '__main__':
    main()
