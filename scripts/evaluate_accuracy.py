"""Freeze and evaluate twelve controlled cases; semantic scores require source review."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import statistics
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import db, demo, postgres
from backend.investigator import Investigator
from backend.lab import summary, store_rows
from backend.provider import groq_http_client, jev_http_client
from backend.triage import JevTriage
from psycopg import sql
from scripts.operator_control import workload, request_pause, paused

BASE=ROOT/'.cache/phase10/benchmark-v1'
MODES=('snapshot','tools','triage')
OWNER='accuracy-evaluator'
MODEL_MODULES=('evidence.py','triage.py','investigator.py')


def freeze_implementation():
    """Retain this benchmark's investigator while application fixes continue."""
    directory=BASE/'implementation'
    manifest_path=directory/'manifest.json'
    if not manifest_path.exists():
        sources={name:(ROOT/'backend'/name).read_bytes() for name in MODEL_MODULES}
        hashes={name:hashlib.sha256(value).hexdigest() for name,value in sources.items()}
        for path in BASE.glob('*/attempts/*.json'):
            recorded=read(path).get('implementation_sha256')
            if recorded and recorded!=hashes:
                raise ValueError('Retain the originally recorded investigator sources before continuing this benchmark.')
        directory.mkdir(parents=True,exist_ok=True)
        for name,value in sources.items():
            path=directory/name
            if path.exists() and path.read_bytes()!=value:
                raise ValueError('Frozen implementation content differs.')
            path.write_bytes(value)
        save(manifest_path,{'created_at':db.now(),'sha256':hashes})
    manifest=read(manifest_path)
    for name,digest in manifest['sha256'].items():
        if name not in MODEL_MODULES or hashlib.sha256((directory/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen investigator implementation changed.')
    return manifest


def load_frozen_implementation():
    global Investigator,JevTriage
    manifest=freeze_implementation()
    import backend
    for name in MODEL_MODULES:
        module_name='backend.'+Path(name).stem
        spec=importlib.util.spec_from_file_location(module_name,BASE/'implementation'/name)
        module=importlib.util.module_from_spec(spec)
        sys.modules[module_name]=module
        spec.loader.exec_module(module)
        setattr(backend,Path(name).stem,module)
    Investigator=backend.investigator.Investigator
    JevTriage=backend.triage.JevTriage
    return manifest['sha256']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as output:
        json.dump(value,output,ensure_ascii=False,indent=2)


def clean(rows):
    return [{k:v for k,v in row.items() if k not in {'user_id','run_id','collection_id','scenario','fault','expected'}} for row in rows]


def healthy_view(rows):
    good=list(dict.fromkeys(r['trace_id'] for r in rows if r.get('trace_id') and r['service']=='gateway' and
          200<=r.get('http_status',r.get('attributes',{}).get('http_status',0))<300))[:3]
    traced=[r for r in rows if r.get('trace_id') in good]
    times=[r['timestamp'] for r in traced if r.get('timestamp')]
    if not times:
        raise ValueError('No complete successful requests in the recording.')
    # Preserve measured counters in the selected interval. In recovered windows
    # cumulative failure counters are historical noise, not a current incident.
    result=[r for r in rows if r.get('trace_id') in good or
            (r['kind']=='metric' and min(times)<=r.get('timestamp','')<=max(times))]
    if not good or any(r.get('status')=='error' or r.get('level')=='error' for r in result):
        raise ValueError('The selected control is not independently healthy.')
    return result


def freeze():
    manifest=BASE/'manifest.json'
    if manifest.exists():
        result=read(manifest)
        for case in result['cases']:
            snapshot=read(BASE/case['id']/'snapshot.json')
            if demo.digest(snapshot)!=case['snapshot_sha256']:
                raise ValueError('Frozen evidence changed; retain this benchmark and choose a new version.')
        return result
    gallery={c['slug']:c for c in read(ROOT/'demo/v1/recordings.json')['cases']}
    sources={}
    for kind in ('pool','slow'):
        older=ROOT/f'.cache/phase7/comparison/{kind}/snapshot.json'
        if not older.exists():
            raise ValueError('Retained earlier recording required: '+str(older))
        sources[kind+'-1']=clean(read(older)['evidence'])
        sources[kind+'-2']=clean(gallery[kind]['evidence'])
    definitions=[]
    for name,rows in sources.items():
        definitions.append((name,name.split('-')[0],rows,'payment',
            'Application pool acquisition failure and saturation; no proven leak/database overload.' if name.startswith('pool') else
            'Payment work outlasts the linked Order deadline; internal reason remains unknown.'))
    for index,(source,rows) in enumerate(sources.items(),1):
        definitions.append((f'healthy-{index}','healthy',healthy_view(rows),'unknown',
            'No observed failing request in this bounded successful control. Do not invent a failure or claim universal health. Source: '+source))
    definitions.extend([
        ('ambiguous-1','ambiguous',[r for r in sources['pool-2'] if r['kind']=='log' and r['service']=='gateway'],'unknown','Caller HTTP symptoms cannot locate the deeper failing operation.'),
        ('ambiguous-2','ambiguous',[r for r in sources['slow-2'] if r['kind']=='log' and r['service'] in {'gateway','order'}],'unknown','Caller timeout is observed; downstream processing evidence is withheld and its internal cause is unlocated.'),
        ('ambiguous-3','ambiguous',clean(gallery['incomplete']['evidence']),'unknown','Intermittent caller symptoms with missing downstream evidence require abstention.'),
        ('ambiguous-4','ambiguous',[r for r in clean(gallery['startup']['evidence']) if r['service']=='gateway' and r.get('trace_id')],'unknown','Connection failures are observed; process/startup evidence is withheld, so the underlying cause is unknown.')])
    cases=[]
    for name,category,rows,origin,expectation in definitions:
        if not rows or len({r['id'] for r in rows})!=len(rows):
            raise ValueError('Invalid evaluation evidence.')
        snapshot={'evidence':rows,'summary':summary(rows,category=='ambiguous'),
                  'symptom':'Assess the supplied operational observations and their uncertainty.'}
        save(BASE/name/'snapshot.json',snapshot)
        cases.append({'id':name,'category':category,'snapshot_sha256':demo.digest(snapshot),
                      'observations':len(rows),'expected_origin':origin,'rubric':expectation})
    result={'version':1,'created_at':db.now(),'provenance':'Frozen views of two distinct controlled recording sessions; healthy and missing-evidence controls are evaluator-selected. Not customer incidents.',
            'selection':'All first attempts are compared. Retries are retained and reported separately. No accuracy claim without semantic review.',
            'cases':cases}
    save(manifest,result)
    return result


def install(case,snapshot):
    schema='operator_accuracy_'+hashlib.sha256(case['id'].encode()).hexdigest()[:16]
    db._target,db._schema=postgres.test_target(),schema
    db.DATA=BASE/case['id']/'data'
    with postgres.connection(db._target) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
    db.init()
    db.execute('INSERT INTO users VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(OWNER,'Evaluator','accuracy@evaluation.invalid','login-disabled',db.now()))
    existing=db.one('SELECT fingerprint FROM collections WHERE id=%s',('frozen',))
    if existing:
        if existing['fingerprint']!=case['snapshot_sha256']:
            raise ValueError('Installed benchmark evidence differs from its frozen version.')
        return
    db.execute('INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
        ('frozen',OWNER,'Recorded observations',snapshot['symptom'],'evaluation',db.now(),case['snapshot_sha256'],json.dumps(snapshot['summary'])))
    store_rows('frozen',OWNER,snapshot['evidence'])


async def run(args):
    manifest=freeze()
    if not args.live:
        print(json.dumps({'cases':len(manifest['cases']),'live':False,'manifest':str(BASE/'manifest.json')})); return
    implementation_hashes=load_frozen_implementation()
    class Evaluator(Investigator):
        async def groq(self,tools,record,usage=None,progress=lambda _:None):
            return await super().groq(tools,record,usage,progress,snapshot_only=self.mode=='snapshot')
    # Share the demo-preparation lock: only one explicit provider workload.
    with workload() as pause_check:
        try:
            async with groq_http_client() as groq, jev_http_client() as jev:
                worker=Evaluator(groq,triage=JevTriage(jev))
                for index,case in enumerate(manifest['cases']):
                    if args.case and case['id'] not in args.case:
                        continue
                    snapshot=read(BASE/case['id']/'snapshot.json')
                    install(case,snapshot)
                    # Rotate order to reduce systematic provider/time bias.
                    for mode in MODES[index%3:]+MODES[:index%3]:
                        if paused(pause_check):
                            return
                        if args.mode and mode!=args.mode:
                            continue
                        folder=BASE/case['id']/'attempts'
                        if not args.retry and list(folder.glob(mode+'-*.json')):
                            continue
                        worker.mode=mode
                        identifier=db.uid()
                        usage={'triage':{'requested':True,'state':'pending'}} if mode=='triage' else {}
                        db.execute('INSERT INTO investigations(id,collection_id,user_id,engine,state,created_at,usage) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                                   (identifier,'frozen',OWNER,'groq','queued',db.now(),json.dumps(usage)))
                        try:
                            await worker.investigate(identifier)
                        except asyncio.CancelledError:
                            db.execute("UPDATE investigations SET state='interrupted',completed_at=%s,error='Operator evaluation interrupted; partial activity retained.' WHERE id=%s AND state IN ('queued','running','quota_waiting')",(db.now(),identifier))
                            raise
                        finally:
                            row=db.one('SELECT * FROM investigations WHERE id=%s',(identifier,))
                            result={k:row[k] for k in ('id','state','created_at','completed_at','error')}
                            result.update({k:json.loads(row[k]) if row[k] else None for k in ('report','usage','activity')})
                            result.update(case=case['id'],mode=mode,snapshot_sha256=case['snapshot_sha256'])
                            result['implementation_sha256']=implementation_hashes
                            result['storage_sha256']=hashlib.sha256((ROOT/'backend/postgres.py').read_bytes()).hexdigest()
                            save(folder/(mode+'-'+identifier+'.json'),result)
                            print(json.dumps({'case':case['id'],'mode':mode,'id':identifier,'state':result['state']}),flush=True)
                        if result['usage'].get('retry_at'):
                            print(json.dumps({'deferred_until':result['usage']['retry_at']})); return
        finally:
            db.close()


def review(path,scores):
    result=read(path)
    if result['state']!='completed':
        raise ValueError('Failed attempts are counted automatically, not scored as reports.')
    required={'mechanism_correct','service_correct','abstention_appropriate','unsupported_claims','citations','recommendation_score','notes'}
    if set(scores)!=required or any(type(scores[k]) is not bool for k in ('mechanism_correct','service_correct','abstention_appropriate')):
        raise ValueError('Review all required accuracy and abstention fields.')
    if type(scores['recommendation_score']) is not int or scores['recommendation_score'] not in (0,1,2) or not isinstance(scores['unsupported_claims'],list) or len(scores['notes'])<40:
        raise ValueError('Use recommendation score 0-2 and meaningful review notes.')
    report=result['report']
    cited=set(report['evidence_ids'])|{i for h in report['hypotheses'] for i in h['evidence_ids']}
    reviewed=set()
    for citation in scores['citations']:
        if set(citation)!={'source_id','supports_claim','claim','notes'} or type(citation['supports_claim']) is not bool or not citation['notes']:
            raise ValueError('Each citation needs a semantic source review.')
        reviewed.add(citation['source_id'])
    if reviewed!=cited:
        raise ValueError('Review every cited source and no extra sources.')
    save(BASE/result['case']/'reviews'/(result['id']+'.json'),{'attempt_digest':demo.digest(result),'reviewed_at':db.now(),**scores})


def summarize():
    manifest=freeze()
    records=[]
    for case in manifest['cases']:
        attempts=sorted((read(p) for p in (BASE/case['id']/'attempts').glob('*.json')),key=lambda r:r['created_at'])
        first=set()
        for result in attempts:
            usage=result['usage']; triage=usage.get('triage',{})
            review_path=BASE/case['id']/'reviews'/(result['id']+'.json')
            score=read(review_path) if review_path.exists() else None
            if score and score['attempt_digest']!=demo.digest(result):
                raise ValueError('Reviewed attempt changed.')
            row={'case':case['id'],'mode':result['mode'],'id':result['id'],'first_attempt':result['mode'] not in first,
                 'state':result['state'],'error':result['error'],'semantic_review':score,
                 'implementation_sha256':result.get('implementation_sha256'), 'storage_sha256':result.get('storage_sha256'),
                 'latency_ms':usage.get('duration_ms'),'groq_calls':usage.get('requests',0),'jev_calls':triage.get('requests',0),
                 'tool_reads':len(result['activity']),'actual_groq_usage':[d.get('actual_usage') for d in usage.get('diagnostics',[]) if d.get('actual_usage')],
                 'actual_jev_usage':[d.get('actual_usage') for d in triage.get('diagnostics',[]) if d.get('actual_usage')],
                 'jev_cost':sum(d.get('cost',0) for d in triage.get('diagnostics',[])),
                 'jev_advisory':triage.get('advisory_context'),'assessment':(result['report'] or {}).get('assessment')}
            first.add(result['mode']); records.append(row)
    aggregates={}
    for mode in MODES:
        rows=[r for r in records if r['mode']==mode and r['first_attempt']]
        reviewed=[r['semantic_review'] for r in rows if r['semantic_review']]
        aggregates[mode]={'attempted':len(rows),'failed':sum(r['state']!='completed' for r in rows),'reviewed':len(reviewed),
            'mechanism_correct':sum(r['mechanism_correct'] for r in reviewed),'service_correct':sum(r['service_correct'] for r in reviewed),
            'unsupported_claims':sum(len(r['unsupported_claims']) for r in reviewed),
            'supported_citations':sum(c['supports_claim'] for r in reviewed for c in r['citations']),
            'reviewed_citations':sum(len(r['citations']) for r in reviewed),
            'appropriate_abstention':sum(r['abstention_appropriate'] for r in reviewed),
            'recommendation_points':sum(r['recommendation_score'] for r in reviewed)}
        values=aggregates[mode]
        completed=sum(r['state']=='completed' for r in rows)
        latencies=[r['latency_ms'] for r in rows if isinstance(r['latency_ms'],(float,int))]
        values.update(completed=completed,unreviewed_completed=completed-len(reviewed),
            completion_rate=completed/len(rows) if rows else None,
            mechanism_accuracy_all_attempts=values['mechanism_correct']/len(rows) if rows and completed==len(reviewed) else None,
            service_accuracy_all_attempts=values['service_correct']/len(rows) if rows and completed==len(reviewed) else None,
            semantic_citation_support=values['supported_citations']/values['reviewed_citations'] if values['reviewed_citations'] else None,
            mean_recommendation_score=values['recommendation_points']/len(reviewed) if reviewed else None,
            median_latency_ms=statistics.median(latencies) if latencies else None,
            groq_calls=sum(r['groq_calls'] for r in rows),jev_calls=sum(r['jev_calls'] for r in rows))
    output={'created_at':db.now(),'cases':12,'aggregates':aggregates,'attempts':records,
            'comparison_complete':all(value['attempted']==12 and value['unreviewed_completed']==0 for value in aggregates.values()),
            'measurement_notes':['Failed attempts remain in the denominator once all completed reports are reviewed.',
                'Latency includes database/network delays; storage implementation changed during this retained run. Compare latency cautiously.',
                'Investigator, evidence selection and triage modules are pinned to the benchmark implementation manifest.'],
            'conclusion':'No demonstrated accuracy benefit is claimed. Assess paired first attempts after all reports have source-based semantic reviews.'}
    path=BASE/('summary-'+db.uid()+'.json'); save(path,output)
    print(json.dumps({'summary':str(path),'aggregates':aggregates}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('freeze','run','pause','review','summary'))
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--retry',action='store_true')
    parser.add_argument('--case',nargs='+')
    parser.add_argument('--mode',choices=MODES)
    parser.add_argument('--attempt',type=Path)
    parser.add_argument('--scores',type=Path)
    args=parser.parse_args()
    if args.action=='pause':
        request_pause()
    elif args.action=='review':
        if not args.attempt or not args.scores:
            parser.error('review requires --attempt and --scores')
        review(args.attempt,read(args.scores))
    elif args.action=='summary':
        summarize()
    else:
        if args.action=='freeze': args.live=False
        asyncio.run(run(args))


if __name__=='__main__':
    main()
