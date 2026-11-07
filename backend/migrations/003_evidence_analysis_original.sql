
CREATE TABLE error_signatures(version TEXT NOT NULL,id TEXT NOT NULL,family TEXT NOT NULL,
 label TEXT NOT NULL,pattern TEXT,PRIMARY KEY(version,id));
INSERT INTO error_signatures(version,id,family,label,pattern) VALUES
('signatures-v1','null_reference','application_exception','Null reference / NPE','NullPointerException|null reference|nullreferenceexception|cannot (read|set) propert[^\n]*(null|undefined)|NoneType[^\n]*has no attribute'),
('signatures-v1','type_error','application_exception','Type error','TypeError|type mismatch|invalid cast|ClassCastException'),
('signatures-v1','unhandled_exception','application_exception','Unhandled exception','unhandled exception|uncaught exception|Exception in thread|Traceback \(most recent call last\)'),
('signatures-v1','out_of_memory','resource_exhaustion','Out of memory','OutOfMemoryError|out[ -]of[ -]memory|OOMKilled|(^|[^a-z])OOM([^a-z]|$)|MemoryError'),
('signatures-v1','heap_exhaustion','resource_exhaustion','Heap exhaustion','heap (space|exhaust|limit)|heap[^\n]*(exhausted|allocation failed)|allocation failed[^\n]*heap'),
('signatures-v1','gc_pressure','resource_exhaustion','Explicit GC pressure','GC overhead limit exceeded|GC pressure|garbage collection pressure|GC thrashing'),
('signatures-v1','connection_exhaustion','resource_exhaustion','Connection exhaustion','connection pool[^\n]*(exhaust|saturat|full)|too many connections|connection acquisition timed out|no (available|free) connections'),
('signatures-v1','thread_exhaustion','resource_exhaustion','Thread exhaustion','unable to create[^\n]*thread|thread pool[^\n]*(exhaust|saturat)|no available threads'),
('signatures-v1','file_handle_exhaustion','resource_exhaustion','File-handle exhaustion','too many open files|file (handle|descriptor)[^\n]*exhaust|EMFILE|ENFILE'),
('signatures-v1','request_timeout','latency_timeout','Request timeout','request[^\n]*(timed out|timeout)|deadline exceeded|TimeoutError|ReadTimeout|request_timeout'),
('signatures-v1','dependency_timeout','latency_timeout','Dependency timeout','(upstream|downstream|dependency)[^\n]*(timed out|timeout)|dependency_timeout'),
('signatures-v1','database_timeout','latency_timeout','Database timeout','(database|query|statement|lock wait)[^\n]*(timed out|timeout)|database_timeout'),
('signatures-v1','service_unavailable','dependency_failure','Unavailable service','service unavailable|upstream unavailable|dependency unavailable|no healthy upstream|ServiceUnavailable'),
('signatures-v1','upstream_failure','dependency_failure','Explicit upstream failure','(upstream|downstream|dependency)[^\n]*(failed|failure|error response|HTTP 5[0-9][0-9])|bad gateway'),
('signatures-v1','crash_loop','deployment_lifecycle','Crash loop','CrashLoopBackOff|crash[ -]loop|back-off restarting failed container'),
('signatures-v1','readiness_failure','deployment_lifecycle','Failed readiness / startup check','(readiness|startup|liveness) (probe|check)[^\n]*(fail|error)|failed (readiness|startup)'),
('signatures-v1','pod_termination','deployment_lifecycle','Explicit pod termination','pod[^\n]*(terminated|terminating|evicted|killed)|container[^\n]*(terminated|OOMKilled)|SIGTERM received'),
('signatures-v1','missing_schema','database_query','Missing schema','no such (table|column)|undefined (table|column)|relation[^\n]*does not exist|unknown column|schema[^\n]*(missing|does not exist)'),
('signatures-v1','query_rejected','database_query','Rejected query','SQLSyntaxError|syntax error at or near|query (rejected|failed)|constraint violation|duplicate key value violates'),
('signatures-v1','deadlock','database_query','Deadlock','deadlock detected|deadlock found|DeadlockLoser|SQLSTATE[^\n]*40P01'),
('signatures-v1','dns_failure','network_connectivity','DNS failure','ENOTFOUND|EAI_AGAIN|DNS[^\n]*(fail|error)|name or service not known|name resolution[^\n]*fail'),
('signatures-v1','tls_failure','network_connectivity','TLS failure','TLS[^\n]*(fail|error)|SSL[^\n]*(fail|error)|certificate[^\n]*(expired|verify failed|invalid)|CERT_HAS_EXPIRED'),
('signatures-v1','connection_refused','network_connectivity','Connection refused','ECONNREFUSED|connection refused|ConnectError'),
('signatures-v1','connection_reset','network_connectivity','Connection reset','ECONNRESET|connection reset|broken pipe'),
('signatures-v1','authentication_rejected','authentication_access','Authentication rejected','authentication (failed|rejected)|invalid credentials|unauthorized|permission denied|access denied'),
('signatures-v1','configuration_error','configuration','Configuration error','missing (required )?(configuration|config|environment variable)|invalid configuration|ConfigurationError'),
('signatures-v1','validation_error','data_validation','Validation error','ValidationError|validation failed|invalid input|JSONDecodeError|ValueError'),
('signatures-v1','filesystem_error','storage_filesystem','Filesystem error','FileNotFoundError|no such file or directory|read-only file system|no space left on device|ENOSPC'),
('signatures-v1','messaging_failure','jobs_messaging','Messaging / job failure','message[^\n]*(delivery failed|rejected)|job (failed|failure)|queue[^\n]*(full|unavailable)|dead.letter'),
('signatures-v1','throttled','rate_limit_quota','Throttling','rate limit[^\n]*(exceed|reach)|too many requests|throttl|quota exceeded|HTTP 429');

INSERT INTO error_signatures VALUES('signatures-v1','unknown','unknown','Unclassified',NULL),
 ('signatures-v1','mixed','mixed','Multiple error families',NULL);
ALTER TABLE evidence_index ADD COLUMN request_id TEXT;
ALTER TABLE evidence_index ADD COLUMN span_id TEXT;
ALTER TABLE evidence_index ADD COLUMN parent_id TEXT;
ALTER TABLE evidence_index ADD COLUMN search_text TEXT;
CREATE TABLE evidence_error_tags(evidence_id TEXT NOT NULL REFERENCES evidence(id),version TEXT NOT NULL,
 signature TEXT NOT NULL,family TEXT NOT NULL,PRIMARY KEY(evidence_id,version,signature),
 FOREIGN KEY(version,signature) REFERENCES error_signatures(version,id));
CREATE INDEX evidence_request ON evidence_index(request_id,observed_at);
CREATE INDEX evidence_span_parent ON evidence_index(trace_id,span_id,parent_id);
CREATE INDEX evidence_owner_scope ON evidence(user_id,collection_id,kind,service);
CREATE INDEX evidence_owner_run ON evidence(user_id,run_id,kind,service);
CREATE INDEX evidence_signature ON evidence_error_tags(version,signature,evidence_id);
ALTER TABLE investigations ADD COLUMN stage TEXT;
ALTER TABLE investigations ADD COLUMN supplied_excerpts TEXT;
ALTER TABLE supplements ADD COLUMN stage TEXT;
ALTER TABLE supplements ADD COLUMN supplied_excerpts TEXT;
CREATE FUNCTION observation_signature_text(document JSONB) RETURNS TEXT LANGUAGE SQL IMMUTABLE AS $$
 SELECT COALESCE(NULLIF(concat_ws(E'\n',document->>'message',document->>'exception_type',
 document->>'error_type',document->>'error',document->>'stack_trace',document->>'exception'),''),document->>'original','')
$$;
CREATE FUNCTION index_failure_tags(identifier TEXT,document JSONB) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO evidence_error_tags(evidence_id,version,signature,family)
 SELECT identifier,s.version,s.id,s.family FROM error_signatures s WHERE s.version='signatures-v1'
 AND (observation_signature_text(document) ~* s.pattern OR (s.id='unhandled_exception' AND document->>'exception_handled'='false'))
 ON CONFLICT DO NOTHING;
 IF NOT EXISTS (SELECT 1 FROM evidence_error_tags WHERE evidence_id=identifier AND version='signatures-v1') THEN
  INSERT INTO evidence_error_tags VALUES(identifier,'signatures-v1','unknown','unknown');
 ELSIF (SELECT COUNT(DISTINCT family) FROM evidence_error_tags WHERE evidence_id=identifier AND version='signatures-v1')>1 THEN
  INSERT INTO evidence_error_tags VALUES(identifier,'signatures-v1','mixed','mixed') ON CONFLICT DO NOTHING;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION index_observation() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE arrival BIGINT; stamp TIMESTAMPTZ; line BIGINT; document JSONB; severity TEXT;
BEGIN
 document := NEW.payload::jsonb;
 BEGIN stamp := NEW.timestamp::timestamptz; EXCEPTION WHEN others THEN stamp := NULL; END;
 BEGIN line := COALESCE((document->>'source_line')::bigint,0); EXCEPTION WHEN others THEN line := 0; END;
 severity := lower(COALESCE(document->>'level','unknown'));
 severity := CASE WHEN severity IN ('fatal','critical') THEN 'error' WHEN severity='warn' THEN 'warning'
 WHEN severity IN ('trace','debug','info','warning','error') THEN severity ELSE 'unknown' END;
 INSERT INTO evidence_index(evidence_id,observed_at,trace_id,level,message,source_line,request_id,span_id,parent_id,search_text)
 VALUES(NEW.id,stamp,NULLIF(document->>'trace_id',''),severity,document->>'message',line,
 NULLIF(document->>'request_id',''),NULLIF(document->>'span_id',''),NULLIF(COALESCE(document->>'parent_id',document->>'parent_span_id'),''),
 concat_ws(E'\n',observation_signature_text(document),document->>'original'));
 PERFORM index_failure_tags(NEW.id,document);
 UPDATE arrival_clock SET value=value+1 WHERE id=1 RETURNING value INTO arrival;
 INSERT INTO evidence_arrivals VALUES(arrival,NEW.id);
 RETURN NEW;
END $$;
UPDATE evidence_index i SET request_id=NULLIF(e.payload::jsonb->>'request_id',''),span_id=NULLIF(e.payload::jsonb->>'span_id',''),
 parent_id=NULLIF(COALESCE(e.payload::jsonb->>'parent_id',e.payload::jsonb->>'parent_span_id'),''),
 search_text=concat_ws(E'\n',observation_signature_text(e.payload::jsonb),e.payload::jsonb->>'original'),
 level=CASE WHEN lower(i.level) IN ('fatal','critical') THEN 'error' WHEN lower(i.level)='warn' THEN 'warning'
 WHEN lower(i.level) IN ('trace','debug','info','warning','error') THEN lower(i.level) ELSE 'unknown' END
 FROM evidence e WHERE e.id=i.evidence_id;
SELECT index_failure_tags(id,payload::jsonb) FROM evidence;
