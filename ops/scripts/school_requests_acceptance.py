"""Explicit live checks on existing listeners. The full worker test is staging-only."""
from base64 import b64encode
from datetime import datetime, timedelta
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from school_directory import Directory


mode=sys.argv[1:]
scope=os.environ.get('BRP_JOB_QUEUE_SCOPE')
port=int(os.environ['BRP_BACKEND_PORT'])
if not ((scope,port) in {('staging',8001),('prod',8000)}
        and (mode==['--release-smoke'] or (mode==['--live-staging'] and scope=='staging'))
        and os.environ.get('BRP_SCHOOL_SCOPES')==os.environ.get('BRP_SCHOOL_REQUESTS')=='1'):
    raise SystemExit("Select an enabled release for --release-smoke, or staging for --live-staging")
dbpath=Path(os.environ['BRP_RUNTIME_DB_PATH']); directory=Directory(os.environ['BRP_SCHOOL_DIRECTORY_DB'])
assert dbpath==Path('/opt/brp/shared/runtime/brp_runtime.sqlite') and dbpath.is_file()
prefix='release-app-'+uuid4().hex[:12]
user,north,south,dual=[prefix+'-'+n+'@example.invalid' for n in ('new','north','south','dual')]
people=(user,north,south,dual)
schools=[]; checks=[]
def good(label):
    checks.append(label);print('PASS:',label,flush=True)


def http(method,path,actor,data=None,workspace=None,expect=(200,)):
    assert actor in people
    headers={'Authorization':'Bearer '+os.environ['BRP_BACKEND_SERVICE_TOKEN'],'X-BRP-User-Email':actor,'Content-Type':'application/json'}
    if workspace:headers['X-BRP-School']=workspace
    req=Request(f'http://127.0.0.1:{port}/api'+path,headers=headers,method=method,
                data=json.dumps(data).encode() if data is not None else None)
    try:
        with urlopen(req,timeout=130) as response:code,raw=response.status,response.read()
    except HTTPError as e:code,raw=e.code,e.read()
    if code not in expect:raise AssertionError(f'{method} {path}: got {code}, expected {expect}; '+raw.decode(errors='replace')[:320])
    if raw[:1] in (b'{',b'['):return json.loads(raw)
    return raw


def submit_fleet(actor,workspace=None,expected=(201,)):
    result=http('POST','/fleet-planner/preview',actor,{'market':'CN','rider_counts':[5,4]},workspace)
    assert result['summary']['total_riders']==9
    return http('POST','/fleet-planner/history',actor,{'title':prefix,'preview_result':result,
        'global_plan_result':{'routes':[],'summary':{'test_preview_only':True}}},workspace,expected)


def workbook():
    wb=Workbook();s=wb.active;s.title='current_plan_assignments'
    s.append(['route_id','stop_sequence','bus_type','country','city','address','passenger_count'])
    s.append(['R1',1,'Small Bus','China','Shanghai','上海图书馆淮海路馆',2])
    s.append(['R1',2,'Small Bus','China','Shanghai','上海交通大学徐汇校区',0])
    f=wb.create_sheet('current_plan_fleet');f.append(['bus_type','seat_count','vehicle_count']);f.append(['Small Bus',18,1])
    buf=BytesIO();wb.save(buf)
    return {'file_base64':b64encode(buf.getvalue()).decode(),'file_name':prefix+'-ToSchool.xlsx',
        'job_custom_name':prefix,'scheduled_job':True,'submission_id':prefix,
        'scheduled_date':(datetime.now(ZoneInfo('Asia/Shanghai'))+timedelta(days=6)).date().isoformat(),
        'return_scheduled_time':'15:40','address_review_acknowledged':True,
        'config':{'service_direction':'To School','time_window_start':'06:30','time_window_end':'08:00','stop_service_minutes':1}}


def run():
    for label,manager in [('A',north),('B',south)]:
        sid=directory.create(prefix+' '+label,manager,manager)['id'];schools.append(sid)
        directory.change(sid,dual,'operator',None,manager)
    a,b=schools
    request='/me/school-requests'
    assert http('GET','/me/school-workspaces',user)['requests_enabled']
    ra,rb=[http('POST',request,user,{'school':s,'role':'operator'},expect=(201,))['id'] for s in schools]
    pa,pb=['request:'+r for r in (ra,rb)]
    assert {r['id'] for r in http('GET',request,north)['applications']}=={ra}
    http('POST',request+'/'+ra+'/decision',south,{'status':'approved','role':'operator'},expect=(403,))
    good('two applications; reviewers restricted to own school')
    f1=submit_fleet(user,pa)['job']['run_id'];f2=submit_fleet(user,pb)['job']['run_id']
    path='/fleet-planner/history/'
    http('GET',path+f1,user)
    for observer in (north,south,dual):
        http('GET',path+f1,observer,expect=(403,404))
        assert f1 not in str(http('GET',path.rstrip('/'),observer))
    with sqlite3.connect(dbpath) as db:
        assert not db.execute('select 1 from side_tool_runs where run_id in (?,?)',(f1,f2)).fetchone()
    good('real numeric fleet preview; saved results visible only to applicant; absent from legacy table')
    w=workbook()
    pair=http('POST','/workbooks/submit',user,w,pa,(202,))['paired_jobs']
    ids=[r['job_id'] for r in pair];assert len(ids)==2
    for j in ids:
        http('GET','/jobs/'+j,user)
        for suffix in ('','/map-data/current','/exports/return-routes'):
            http('GET','/jobs/'+j+suffix,south,expect=(403,404))
    good('real workbook submitted through staging; AM and PM private; detail, map, export restricted')
    immediate=http('POST','/distance-checker/direct-school/jobs',user,
        {**w,'scheduled_job':False,'analysis_direction':'To School','final_time_validation_mode':'legacy'},pa,(202,))
    jid=immediate['job']['job_id']; end=time.monotonic()+210
    while True:
        with sqlite3.connect(dbpath) as raw:
            row=raw.execute("select status,error,json_extract(record_json,'$.result.status') from application_jobs where job_id=?",(jid,)).fetchone()
            assert row and not raw.execute('select 1 from jobs where job_id=?',(jid,)).fetchone()
        if row[0]=='succeeded':break
        if row[0] in ('failed','canceled') or time.monotonic()>end:raise AssertionError(('private-worker',row))
        time.sleep(1)
    link='/distance-checker/direct-school/jobs/'+jid
    assert 'succeeded' in str(http('GET',link,user))
    http('GET',link,north,expect=(403,404))
    good('real private Direct-to-School immediate worker succeeded; result readable only by applicant')
    # Exercise the atomic rejection inside a rolled-back transaction; never remove a live pair.
    from history_adoption import HistoryAdmin
    from school_requests import _adopt
    from school_directory import DirectoryError
    with sqlite3.connect(dbpath) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
        try:
            assert db.execute('delete from application_jobs where job_id=? and owner_email=?',(ids[1],user)).rowcount==1
            try:_adopt(db,ra,user,a,north,HistoryAdmin)
            except DirectoryError:pass
            else:raise AssertionError('an incomplete pair was accepted')
        finally:db.rollback()
    good('missing PM rolls back; no partial adoption')
    decision={'status':'approved','role':'operator'}
    http('POST',request+'/'+ra+'/decision',north,decision)
    assert http('POST',request+'/'+ra+'/decision',north,decision)['unchanged']
    with sqlite3.connect(dbpath) as db:
        for j in ids:
            row=db.execute("select status,json_extract(metadata_json,'$.school_id'),json_extract(metadata_json,'$.paired_job_ids') from jobs where job_id=?",(j,)).fetchone()
            assert row[:2]==('scheduled',a) and set(json.loads(row[2]).values())==set(ids)
        assert db.execute("select json_extract(summary_json,'$.school_id') from side_tool_runs where run_id=?",(f1,)).fetchone()[0]==a
        assert db.execute('select 1 from application_tool_runs where run_id=?',(f2,)).fetchone()
        assert not db.execute('select 1 from side_tool_runs where run_id=?',(f2,)).fetchone()
        assert db.execute("select status,json_extract(metadata_json,'$.school_id') from jobs where job_id=?",(jid,)).fetchone()==('succeeded',a)
    for observer in (north,south):http('GET',path+f1,observer,expect=(403,404))
    good('one approval adopts only its own fleet result and complete scheduled pair, without sharing; repeat is idempotent')
    http('POST',request+'/'+rb+'/decision',south,{'status':'approved','role':'viewer'})
    submit_fleet(user,b,(403,))
    submit_fleet(dual,None,(422,))
    f3=submit_fleet(dual,a)['job']['run_id'];f4=submit_fleet(dual,b)['job']['run_id']
    with sqlite3.connect(dbpath) as db:
        assert dict(db.execute("select run_id,json_extract(summary_json,'$.school_id') from side_tool_runs where run_id in (?,?)",(f3,f4)))=={f3:a,f4:b}
    good('viewer cannot submit; multi-school submission requires selection and binds to selected school')
    for suffix in ('','/map-data/current','/exports/return-routes'):http('GET','/jobs/'+ids[0]+suffix,south,expect=(403,404))
    good('cross-school direct links still denied after approval')


def release_smoke():
    # No workbook, background jobs, schedules or billable routing requests.
    for label,manager in [('A',north),('B',south)]:
        schools.append(directory.create(prefix+' '+label,manager,manager)['id'])
    a,b=schools
    request='/me/school-requests'
    assert http('GET','/me/school-workspaces',user)['requests_enabled']
    ra,rb=[http('POST',request,user,{'school':s,'role':'operator'},expect=(201,))['id'] for s in schools]
    f1,f2=[submit_fleet(user,'request:'+r)['job']['run_id'] for r in (ra,rb)]
    path='/fleet-planner/history/'
    for fid in (f1,f2):
        http('GET',path+fid,user)
        for other in (north,south):
            http('GET',path+fid,other,expect=(403,404))
            assert fid not in str(http('GET',path.rstrip('/'),other))
    good('pending applicant can calculate and reopen results; other accounts cannot list or open them')
    decision={'status':'approved','role':'operator'}
    http('POST',request+'/'+ra+'/decision',south,decision,expect=(403,))
    http('POST',request+'/'+ra+'/decision',north,decision)
    assert http('POST',request+'/'+ra+'/decision',north,decision)['unchanged']
    http('GET',path+f1,south,expect=(403,404))
    http('GET',path+f1,user)
    with sqlite3.connect(dbpath) as db:
        assert db.execute("select json_extract(summary_json,'$.school_id') from side_tool_runs where run_id=?",(f1,)).fetchone()==(a,)
        assert not db.execute('select 1 from side_tool_runs where run_id=?',(f2,)).fetchone()
        assert db.execute('select status from school_requests where id=?',(rb,)).fetchone()==('pending',)
        assert db.execute('select 1 from application_tool_runs where run_id=?',(f2,)).fetchone()
    good('only authorized school can approve; approval adopts only that request; repeat is idempotent')


def cleanup():
    # All predicates are confined to this unique run; retain immutable approval receipts.
    marks=','.join('?' for _ in people)
    with sqlite3.connect(dbpath,timeout=20) as db:
        db.execute('pragma foreign_keys=on');db.execute('begin immediate')
        for table in ('jobs','application_jobs'):
            for j, in db.execute(f'select job_id from {table} where owner_email in ({marks})',people).fetchall():
                db.execute("delete from history_group_items where item_id=? and scope in ('route_audit','distance_direct_school')",(j,))
                db.execute('delete from application_checkpoints where job_id=?',(j,))
            db.execute(f'delete from {table} where owner_email in ({marks})',people)
        for table in ('side_tool_runs','application_tool_runs'):
            db.execute(f'delete from {table} where owner_email in ({marks})',people)
        db.execute(f'delete from request_tasks where request in (select id from school_requests where email in ({marks}))',people)
        db.execute(f'delete from school_requests where email in ({marks})',people)
        for table in ('jobs','side_tool_runs','application_jobs','application_tool_runs'):
            assert not db.execute(f'select 1 from {table} where owner_email in ({marks})',people).fetchone()
    with directory.transaction(True) as db:
        for sid in schools:
            assert db.execute('select name from schools where id=?',(sid,)).fetchone()[0].startswith(prefix+' ')
            db.execute('delete from changes where school_id=?',(sid,))
            db.execute('delete from memberships where school_id=?',(sid,))
            db.execute('delete from schools where id=?',(sid,))
    good('temporary schools, memberships, jobs and applications cleaned')

if __name__=='__main__':
    try:(run if mode==['--live-staging'] else release_smoke)()
    finally:cleanup()
