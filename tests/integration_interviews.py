"""Stage-three checks called only by the isolated disposable-server runner."""
from datetime import timedelta, timezone
from unittest.mock import patch
from sqlalchemy import event, update
from flask import g
import mysql.connector
import db
import orm_db
from models import JobApplication, Interview
from services import interview_service as service
import tracker_data


def run_checks(app, check, now):
    def read(identity, owner=1):
        with app.app_context():
            return service.get_interview(owner, identity)

    def create(application, owner=1, **values):
        with app.app_context():
            return service.create_interview(owner, application,
                **(dict(scheduled_at=now, interview_type='online') | values))

    def not_found(operation):
        try:
            operation()
        except service.InterviewNotFound:
            return True
        return False

    with app.app_context():
        with orm_db.operation_session() as session:
            parent = JobApplication(user_id=1, company_id=1, job_title='Synthetic stage three',
                                    opportunity_type='internship', status='Interview',
                                    notes='parent notes', created_at=now, updated_at=now)
            session.add(parent); session.flush()
            application = parent.application_id
    with app.app_context():
        context = service.get_owned_application(1, application)
        check(context['application_id']==application and context['user_id']==1,
              'owned application dictionary lookup persists')
        check(not_found(lambda:service.get_owned_application(2, application)),
              'foreign application lookup is indistinguishable from missing')
    created=create(application,scheduled_at=(now+timedelta(hours=1)).replace(tzinfo=timezone.utc,microsecond=123))
    identity=created['interview_id']
    check(created['scheduled_at']==now+timedelta(hours=1) and created['outcome']=='Pending',
          'interview create converts aware UTC to seconds and defaults Pending')
    check(read(identity)==created, 'interview create/read persists across connections')
    foreign=create(2,owner=2)
    with app.app_context():
        operations=(lambda:service.get_interview(2,identity),
                    lambda:service.create_interview(2,application,scheduled_at=now,interview_type='online'),
                    lambda:service.update_interview(2,identity,scheduled_at=now,interview_type='phone',outcome='Failed'),
                    lambda:service.update_interview_notes(2,identity,notes='foreign'),
                    lambda:service.delete_interview(2,identity),
                    lambda:service.get_interview(1,2147483647))
        check(all(not_found(operation) for operation in operations),
              'two-user reads and every mutation refuse foreign/missing resources')
        check(all(r['interview_id']!=identity for r in service.list_interviews(2,cutoff=now)),
              'foreign owner listing excludes other user interviews')
    check(read(identity)==created, 'foreign mutations preserve every interview field')
    with app.app_context():
        edited=service.update_interview(1,identity,scheduled_at=now,interview_type='technical',
                    venue_or_link='Synthetic room',notes='edit note',outcome='Passed')
    check(edited['application_id']==application and edited['outcome']=='Passed' and edited['interview_type']=='technical',
          'edit persists and preserves immutable application association')
    exact='  synthetic notes\r\nwith trailing spaces  '
    with app.app_context():
        noted=service.update_interview_notes(1,identity,notes=exact)
    check(noted==edited | {'notes':exact}, 'inline notes preserve all unrelated fields and exact text')
    with app.app_context():
        check(service.update_interview_notes(1,identity,notes=exact)==noted,
              'unchanged inline notes succeed against MySQL')
        check(service.update_interview(1,identity,scheduled_at=now,interview_type='technical',
              venue_or_link='Synthetic room',notes=exact,outcome='Passed')==noted,
              'unchanged full interview edit succeeds against MySQL')
        cleared=service.update_interview_notes(1,identity,notes=None)
    check(cleared['notes'] is None and read(identity)['notes'] is None,
          'clearing notes persists NULL across connections')
    with app.app_context():
        with orm_db.operation_session() as session:
            session.execute(update(Interview).where(Interview.interview_id==identity).values(outcome=None))
    check(read(identity)['outcome']=='Pending', 'legacy NULL outcome is presented as Pending')
    with app.app_context():
        legacy=db.get_db(); cursor=legacy.cursor(buffered=True)
        cursor.execute('SELECT outcome FROM Interview WHERE interview_id=%s',(identity,))
        check(cursor.fetchone()==(None,), 'NULL outcome presentation does not rewrite storage')
        cursor.close(); legacy.rollback()
    # Explicit tie cohort, ignoring older fixtures created by stages one/two.
    times=(0,60,60,-60,-60,-120)
    cohort=[create(application,scheduled_at=now+timedelta(seconds=seconds))['interview_id'] for seconds in times]
    with app.app_context():
        listed=service.list_interviews(1,cutoff=now)
    check([r['interview_id'] for r in listed if r['interview_id'] in cohort]==cohort,
          'ordering is cutoff-inclusive upcoming ascending/past descending with ID ties')
    # Recheck ownership after a successful preflight, before a separate write.
    with app.app_context():
        service.get_owned_application(1,application)
        with orm_db.operation_session() as session:
            session.execute(update(JobApplication).where(JobApplication.application_id==application).values(user_id=2))
        check(not_found(lambda:service.create_interview(1,application,scheduled_at=now,interview_type='online')),
              'create rechecks ownership after preflight changes')
        with orm_db.operation_session() as session:
            session.execute(update(JobApplication).where(JobApplication.application_id==application).values(user_id=1))
    # Real DBAPI commit interruption after UPDATE: session rollback must discard it.
    with app.app_context():
        engine=orm_db.get_engine()
        with patch.object(engine.dialect,'do_commit',side_effect=mysql.connector.OperationalError('synthetic private',errno=2013)):
            try: service.update_interview_notes(1,identity,notes='must rollback')
            except db.DatabaseError: pass
            else: raise AssertionError('Commit failure was ignored')
        check(not g.get('orm_operation_active',False),'failed interview commit releases ownership')
    check(read(identity)['notes'] is None,'failed interview commit leaves persisted notes unchanged')
    # Lock both the parent-create path and interview-mutation path separately.
    for table,key,target,operation in (
        ('JobApplication','application_id',application,lambda:service.create_interview(1,application,scheduled_at=now,interview_type='online')),
        ('Interview','interview_id',identity,lambda:service.update_interview_notes(1,identity,notes='blocked'))):
        with app.app_context():
            locker=db.get_db(); cursor=locker.cursor(buffered=True)
            cursor.execute('SELECT `'+key+'` FROM `'+table+'` WHERE `'+key+'`=%s FOR UPDATE',(target,))
            cursor.fetchall()
            with app.app_context():
                engine=orm_db.get_engine()
                def timeout_setup(connection,record):
                    c=connection.cursor(); c.execute('SET SESSION innodb_lock_wait_timeout=1'); c.close()
                event.listen(engine,'connect',timeout_setup)
                try:
                    try:operation()
                    except db.DatabaseError:pass
                    else:raise AssertionError('Ownership lock was ignored')
                finally:event.remove(engine,'connect',timeout_setup)
            locker.rollback(); cursor.close()
        check(read(identity)['notes'] is None,table+' lock contention safely blocks competing write')
    # One non-locking snapshot must span application and interview reads.
    original=tracker_data._fetch_all
    inserted=[]
    def between_reads(session,owner):
        applications=original(session,owner)
        # Commit on an independent connection while Tracker's snapshot stays open.
        inserted.append(create(application,scheduled_at=now+timedelta(days=1))['interview_id'])
        return applications
    with app.app_context():
        with patch('tracker_data._fetch_all',side_effect=between_reads),patch('tracker_data._now_utc',return_value=now):
            page=tracker_data.load_tracker_page(1)
        check(not g.get('orm_operation_active',False),'Tracker shared read transaction cleans up')
    target_row=next(r for r in page['applications'] if r['application_id']==application)
    check(inserted[0] not in [r['interview_id'] for r in target_row['interviews']],
          'Tracker second read retains first-read snapshot despite concurrent committed insert')
    with app.app_context():
        check(inserted[0] in [r['interview_id'] for r in service.list_interviews(1,cutoff=now)],
              'fresh read observes concurrent insert after Tracker snapshot ends')
        with patch('tracker_data._now_utc',return_value=now):
            filtered=tracker_data.load_tracker_page(1,status='Rejected')
        check(filtered['applications']==[] and filtered['total_applications']==page['total_applications'],
              'Tracker filtering preserves unfiltered counts')
        standalone=tracker_data.load_interviews_page(1)
    check(set(page)=={'applications','total_applications','company_options','role_options','interviews',
                     'has_more_interviews','practice_meta','status_counts'},'Tracker dictionary output contract remains unchanged')
    check(0<len(page['interviews'])<=2 and page['interviews'][0]['job_title'] in page['practice_meta'],
          'Tracker upcoming cards and practice context remain intact')
    check(set(standalone)=={'interviews'}, 'standalone Interviews page data contract remains unchanged')
    with app.app_context():service.delete_interview(1,identity)
    check(not_found(lambda:read(identity)), 'interview delete persists across connections')
    check(read(foreign['interview_id'],2)==foreign, 'other owner interview survives all checks')

    # Production callers, real sessions and real templates against this same
    # UUID-verified disposable server. Never load private environment files.
    import importlib
    import os
    with patch('dotenv.load_dotenv', return_value=False), patch.dict(os.environ, SECRET_KEY='synthetic-disposable-route-key'):
        web = importlib.import_module('app')
    client = web.app.test_client()
    with client.session_transaction() as signed:
        signed['_user_id'] = '1'
        signed['_fresh'] = True
        signed['_csrf_token'] = 'synthetic-route-token'
    queries = []
    rendered = []
    with web.app.app_context():
        web_engine = orm_db.get_engine()
    def query_seen(connection, cursor, statement, parameters, context, many):
        queries.append(True)  # Deliberately retain no SQL or parameter values.
    original_render = web.render_template
    def render_after_cleanup(*args, **kwargs):
        if g.get('orm_operation_active', False) or 'mysql_connection' in g:
            raise AssertionError('Rendering still owns a database operation')
        before = len(queries)
        html = original_render(*args, **kwargs)
        if len(queries) != before:
            raise AssertionError('Template issued a lazy database query')
        rendered.append(args[0])
        return html
    event.listen(web_engine, 'before_cursor_execute', query_seen)
    try:
        with patch.object(web, 'render_template', side_effect=render_after_cleanup):
            check(client.get('/tracker').status_code == 200,
                  'real Tracker route renders after snapshot rollback with no lazy SQL')
            check(client.get('/interviews').status_code == 200,
                  'real Interviews route renders after its read transaction ends')
            new_url = '/applications/'+str(application)+'/interviews/new'
            check(client.get(new_url).status_code == 200,
                  'real add form completes authentication and owned-application reads')
            form = dict(csrf_token='synthetic-route-token', date='2026-11-12', time='14:00',
                        interview_type='online', venue_or_link='', notes='synthetic route note', outcome='Pending')
            check(client.post(new_url, data=form).status_code == 302,
                  'real create route preflight read does not block subsequent write')
            with app.app_context():
                route_id = max(r['interview_id'] for r in service.list_interviews(1, cutoff=now))
            edit = '/interviews/'+str(route_id)+'/edit'
            delete_url = '/interviews/'+str(route_id)+'/delete'
            notes_url = '/interviews/'+str(route_id)+'/notes'
            check(client.get(edit).status_code == 200,
                  'real edit form renders materialised interview data')
            check(client.post(edit, data=form | {'outcome': 'Passed'}).status_code == 302,
                  'real edit route completes preflight read before guarded write')
            check(client.post(notes_url, data={'csrf_token': 'synthetic-route-token', 'notes': 'route inline note'}).status_code == 200,
                  'real inline-notes route completes preflight read before guarded write')
            check(read(route_id)['notes'] == 'route inline note',
                  'real route notes persist after request teardown')
            check(client.post(notes_url, data={'notes': 'no CSRF'}).status_code == 400,
                  'real route still rejects missing CSRF before mutation')
            check(client.get(delete_url).status_code == 200,
                  'real delete confirmation renders after ownership read cleanup')
            check(client.post(delete_url, data={'csrf_token': 'synthetic-route-token'}).status_code == 302,
                  'real delete route completes preflight read before guarded write')
            check(not_found(lambda:read(route_id)), 'real route deletion persists after teardown')
        check({'tracker.html', 'interviews/index.html', 'interviews/form.html', 'interviews/delete.html'}.issubset(rendered),
              'all affected production templates render with no active session or lazy SQL')
    finally:
        event.remove(web_engine, 'before_cursor_execute', query_seen)
        web_engine.dispose()
