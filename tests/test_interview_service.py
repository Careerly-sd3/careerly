"""Offline ORM ownership/transaction tests; live semantics use the disposable runner."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch
from flask import Flask, g
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from db import DatabaseError
from services import interview_service as service
import tracker_data


class InterviewServiceTests(unittest.TestCase):
    def setUp(self):
        context = Flask('interview-unit').app_context()
        context.push(); self.addCleanup(context.pop)
        for target in ('orm_db.get_engine', 'orm_db.Session'):
            p = patch(target); mock = p.start(); self.addCleanup(p.stop)
            if target.endswith('Session'): self.factory = mock
        p = patch('mysql.connector.connect', side_effect=AssertionError('No network'))
        self.network = p.start(); self.addCleanup(p.stop)
        self.session = self.factory.return_value
        self.result = self.session.execute.return_value
        self.result.rowcount = 1
        self.time = datetime(2026, 11, 9, 12)
        self.application = dict(application_id=11, user_id=7, company_id=3,
                                job_title='Synthetic role', status='Applied', company='Synthetic company')
        self.row = dict(interview_id=41, application_id=11, scheduled_at=self.time,
                        interview_type='online', venue_or_link=None, notes='Synthetic notes',
                        outcome=None, job_title='Synthetic role', company='Synthetic company')
        self.result.mappings.return_value.one_or_none.return_value = self.row
        self.result.mappings.return_value.all.return_value = []
        self.session.flush.side_effect = lambda: setattr(self.session.add.call_args.args[0], 'interview_id', 41)
        self.input = dict(scheduled_at=self.time, interview_type='online', venue_or_link='https://example.invalid/meeting', notes='Synthetic notes')

    def tearDown(self):
        self.network.assert_not_called()

    def create(self, **overrides):
        self.result.mappings.return_value.one_or_none.side_effect = [self.application, self.row]
        return service.create_interview(7, 11, **(self.input | overrides))

    def update(self, **overrides):
        return service.update_interview(7, 41, **(self.input | {'outcome':'Passed'} | overrides))

    def compiled(self, index):
        return self.session.execute.call_args_list[index].args[0].compile(dialect=mysql.dialect())

    def assert_rollback(self):
        self.session.rollback.assert_called_once()
        self.session.commit.assert_not_called()
        self.session.close.assert_called_once()
        self.assertFalse(g.get('orm_operation_active', False))

    def test_get_owned_application_uses_owner_predicate(self):
        self.result.mappings.return_value.one_or_none.return_value = self.application
        self.assertEqual(service.get_owned_application(7, 11), self.application)
        self.assertEqual(self.compiled(0).params, dict(application_id_1=11,user_id_1=7))
        self.assert_rollback()

    def test_get_interview_presents_null_without_mutation(self):
        result = service.get_interview(7, 41)
        self.assertEqual(set(result), set(self.row))
        self.assertEqual(result['outcome'], 'Pending')
        self.assertIsNone(self.row['outcome'])
        self.assertEqual(self.compiled(0).params, dict(interview_id_1=41,user_id_1=7))
        self.assertIn('JOIN `JobApplication`', str(self.compiled(0)))
        self.assert_rollback()

    def test_absent_and_foreign_resources_share_not_found(self):
        self.result.mappings.return_value.one_or_none.return_value = None
        for operation in (service.get_owned_application, service.get_interview):
            for identity in (41,999):
                with self.assertRaisesRegex(service.InterviewNotFound, 'Resource not found.'):
                    operation(8, identity)
                self.assertEqual(self.compiled(-1).params['user_id_1'],8)
        self.assertEqual(self.session.rollback.call_count,4)
        self.session.commit.assert_not_called()

    def test_listing_order_cutoff_and_dictionary_contract(self):
        rows=[self.row | dict(interview_id=i,scheduled_at=self.time+timedelta(seconds=s)) for i,s in [(5,0),(2,60),(3,60),(6,-60),(7,-60),(1,-120)]]
        self.result.mappings.return_value.all.return_value=rows
        result=service.list_interviews(7,cutoff=self.time.replace(tzinfo=timezone.utc))
        self.assertEqual([r['interview_id'] for r in result],[5,2,3,6,7,1])
        self.assertTrue(all(r['outcome']=='Pending' for r in result))
        compiled=self.compiled(0)
        self.assertEqual(compiled.params['cutoff'],self.time)
        self.assertEqual(compiled.params['user_id_1'],7)
        order=str(compiled).split('ORDER BY')[1]
        self.assertEqual(order.count('CASE WHEN'),3)
        self.assertIn('scheduled_at END DESC',order)
        self.assertTrue(order.endswith('`Interview`.interview_id ASC'))
        self.assert_rollback()

    def test_listing_uses_single_current_utc_cutoff(self):
        before=datetime.now(timezone.utc).replace(tzinfo=None,microsecond=0)
        self.assertEqual(service.list_interviews(7),[])
        after=datetime.now(timezone.utc).replace(tzinfo=None,microsecond=0)
        cutoff=self.compiled(0).params['cutoff']
        self.assertTrue(before<=cutoff<=after)
        self.assertIsNone(cutoff.tzinfo)

    def test_create_locks_parent_then_flushes_and_reads_saved_row(self):
        result=self.create()
        lock=self.compiled(0)
        self.assertIn('FOR UPDATE',str(lock))
        self.assertEqual(lock.params,dict(application_id_1=11,user_id_1=7))
        record=self.session.add.call_args.args[0]
        self.assertEqual(record.application_id,11)
        self.assertEqual(record.outcome,'Pending')
        self.assertEqual(self.compiled(1).params,dict(interview_id_1=41,user_id_1=7))
        self.assertEqual(result['interview_id'],41)
        self.session.flush.assert_called_once()
        self.session.commit.assert_called_once()
        self.session.close.assert_called_once()
        self.session.rollback.assert_not_called()

    def test_aware_time_is_converted_to_utc_and_seconds(self):
        self.create(scheduled_at=datetime(2026,11,9,14,0,0,123,tzinfo=timezone(timedelta(hours=2))))
        self.assertEqual(self.session.add.call_args.args[0].scheduled_at,self.time)

    def test_update_is_scoped_and_cannot_reassign_application(self):
        self.assertEqual(self.update()['application_id'],11)
        self.assertIn('FOR UPDATE',str(self.compiled(0)))
        update=self.compiled(1)
        self.assertEqual(update.params['user_id_1'],7)
        self.assertEqual(update.params['interview_id_1'],41)
        self.assertIn('`Interview`.application_id IN (SELECT `JobApplication`.application_id',str(update))
        self.assertNotIn('application_id',str(update).split(' SET ')[1].split(' WHERE ')[0])
        self.assertEqual(self.compiled(2).params,dict(interview_id_1=41,user_id_1=7))
        self.session.commit.assert_called_once()

    def test_notes_only_update_preserves_exact_whitespace(self):
        notes='  exact\r\ntext  '
        service.update_interview_notes(7,41,notes=notes)
        write=self.compiled(1)
        self.assertEqual(str(write).split(' SET ')[1].split(' WHERE ')[0],'notes=%s')
        self.assertEqual(write.params,dict(notes=notes,interview_id_1=41,user_id_1=7))
        self.assertIn('FOR UPDATE',str(self.compiled(0)))

    def test_unchanged_edit_and_notes_zero_changed_rows_succeed(self):
        self.result.rowcount=0
        self.assertEqual(self.update()['interview_id'],41)
        self.assertEqual(service.update_interview_notes(7,41,notes='same')['interview_id'],41)
        self.assertEqual(self.session.commit.call_count,2)

    def test_delete_scopes_dml_and_commits(self):
        self.assertIsNone(service.delete_interview(7,41))
        self.assertEqual(self.compiled(1).params,dict(interview_id_1=41,user_id_1=7))
        self.session.commit.assert_called_once()

    def test_missing_foreign_mutations_never_write(self):
        self.result.mappings.return_value.one_or_none.return_value=None
        for operation in (lambda:service.create_interview(8,11,**self.input),lambda:self.update(),lambda:service.delete_interview(7,999),lambda:service.update_interview_notes(8,41,notes='x')):
            self.session.reset_mock()
            with self.assertRaises(service.InterviewNotFound): operation()
            self.assertEqual(self.session.execute.call_count,1)
            self.session.add.assert_not_called()
            self.assert_rollback()

    def test_missing_generated_id_and_saved_lookup_roll_back_atomic_create(self):
        for stage in ('identity','lookup'):
            self.session.reset_mock()
            self.result.mappings.return_value.one_or_none.side_effect=[self.application,None]
            self.session.flush.side_effect=lambda:setattr(self.session.add.call_args.args[0],'interview_id',0 if stage=='identity' else 41)
            with self.assertRaises((DatabaseError,service.InterviewNotFound)):
                service.create_interview(7,11,**self.input)
            self.assert_rollback()

    def test_zero_delete_count_is_not_found(self):
        self.result.rowcount=0
        with self.assertRaises(service.InterviewNotFound): service.delete_interview(7,41)
        self.assert_rollback()

    def test_values_bound_and_association_parameters_rejected(self):
        payload="synthetic'); DELETE FROM Interview; --"
        self.create(notes=payload,venue_or_link=payload)
        self.assertEqual(self.session.add.call_args.args[0].notes,payload)
        for call in self.session.execute.call_args_list:
            self.assertNotIn(payload,str(call.args[0].compile(dialect=mysql.dialect())))
        with self.assertRaises(TypeError):service.update_interview(7,41,**self.input,outcome='Pending',application_id=12)
        with self.assertRaises(TypeError):service.create_interview(7,11,**self.input,posted_user_id=8)

    def test_refusal_preserves_legacy_work_for_reads_and_writes(self):
        for active,autocommit in ((True,False),(False,True)):
            legacy=MagicMock(in_transaction=active,autocommit=autocommit)
            g.mysql_connection=legacy
            for operation in (self.create,self.update,lambda:service.delete_interview(7,41),lambda:service.list_interviews(7),lambda:service.get_owned_application(7,11)):
                with self.assertRaises(DatabaseError):operation()
            legacy.commit.assert_not_called(); legacy.rollback.assert_not_called(); legacy.close.assert_not_called()
            legacy.cursor.assert_not_called()
        self.factory.assert_not_called()

    def test_execute_commit_begin_and_cleanup_failures_are_safe(self):
        for stage in ('execute','commit','begin','close'):
            self.session.reset_mock()
            for method in ('execute','commit','begin','close'):getattr(self.session,method).side_effect=None
            getattr(self.session,stage).side_effect=OperationalError('private SQL',{'notes':'private'},Exception('private'))
            with self.assertRaises(DatabaseError) as caught: self.create()
            self.assertNotIn('private',str(caught.exception))
            self.session.close.assert_called_once()
            self.assertFalse(g.get('orm_operation_active',False))
            if stage!='close':self.session.rollback.assert_called_once()

    def test_rollback_failure_and_interruption_cleanup(self):
        self.session.execute.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.create()
        self.assert_rollback()
        self.session.reset_mock()
        self.session.rollback.side_effect=RuntimeError('private')
        with self.assertRaises(DatabaseError) as caught:self.create()
        self.assertNotIn('private',str(caught.exception))
        self.session.close.assert_called_once()

    def test_tracker_uses_one_read_session_and_one_cutoff(self):
        with patch('tracker_data._fetch_all',return_value=[]) as applications, patch('tracker_data._list_interviews',return_value=[]) as interviews:
            page=tracker_data.load_tracker_page(7)
        applications.assert_called_once_with(self.session,7)
        self.assertIs(interviews.call_args.args[0],self.session)
        self.assertEqual(interviews.call_args.args[1],7)
        self.assertIsInstance(interviews.call_args.kwargs['cutoff'],datetime)
        self.session.begin.assert_called_once()
        self.assert_rollback()
        self.assertEqual(page['total_applications'],0)

    def test_supported_types_and_outcomes_are_exact(self):
        for kind in ("online", "phone", "technical", "onsite"):
            for outcome in ("Pending", "Passed", "Failed"):
                with self.subTest(kind=kind, outcome=outcome):
                    self.create(interview_type=kind, outcome=outcome)
        for field, values in {
            "interview_type": (None, "", "Online", "online ", "remote", 1),
            "outcome": (None, "", "pending", "Passed ", "Offer", 1),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.factory.reset_mock()
                    with self.assertRaises(service.InterviewValidationError) as error:
                        service.create_interview(7, 11, **(self.input | {field: value}))
                    self.assertIn(field, error.exception.errors)
                    self.factory.assert_not_called()

    def test_required_datetime_and_bounded_optional_text(self):
        for overrides, field in [
            ({"scheduled_at": None}, "scheduled_at"),
            ({"scheduled_at": "2026-11-09"}, "scheduled_at"),
            ({"scheduled_at": datetime(999, 12, 31)}, "scheduled_at"),
            ({"venue_or_link": "x" * 501}, "venue_or_link"),
            ({"venue_or_link": 1}, "venue_or_link"),
            ({"venue_or_link": "\ud800"}, "venue_or_link"),
            ({"notes": "x" * 65536}, "notes"),
            ({"notes": "\u00e9" * 32768}, "notes"),
            ({"notes": 1}, "notes"),
            ({"notes": "\ud800"}, "notes"),
        ]:
            with self.subTest(field=field):
                with self.assertRaises(service.InterviewValidationError) as error:
                    service.create_interview(7, 11, **(self.input | overrides))
                self.assertIn(field, error.exception.errors)
        self.factory.assert_not_called()
        self.create(venue_or_link="x" * 500, notes="x" * 65535)

    def test_invalid_identity_never_queries(self):
        for value in (None, True, 0, -1, 2147483648, "7", "7 OR 1=1", 7.0):
            with self.subTest(value=value), self.assertRaises(service.InterviewValidationError):
                service.list_interviews(value)
        self.factory.assert_not_called()

    def test_tracker_application_projection_ownership_and_materialisation(self):
        statement=tracker_data._applications_statement(7)
        compiled=statement.compile(dialect=mysql.dialect())
        self.assertEqual(compiled.params,dict(user_id_1=7))
        self.assertNotIn('job_ad_text',statement.selected_columns.keys())
        self.assertEqual(set(statement.selected_columns.keys()),{
            'application_id','company','job_title','status','applied_at','closing_date',
            'created_at','location','work_type','opportunity_type','salary_text','job_url',
            'notes','updated_at','website_url'})
        self.result.mappings.return_value.all.return_value=[self.application]
        result=tracker_data._fetch_all(self.session,7)
        self.assertEqual(result,[self.application])
        self.assertIsNot(result[0],self.application)

    def test_tracker_second_read_failure_rolls_back_whole_read_unit(self):
        with patch('tracker_data._fetch_all',return_value=[]), patch('tracker_data._list_interviews',side_effect=OperationalError('private SQL',{},Exception('private'))):
            with self.assertLogs('tracker_data',level='ERROR') as captured:
                with self.assertRaises(DatabaseError):tracker_data.load_tracker_page(7)
        self.assert_rollback()
        self.assertNotIn('private SQL',''.join(captured.output))

    def test_borrowed_interview_read_does_not_manage_transaction(self):
        self.assertEqual(service._list_interviews(self.session,7,cutoff=self.time),[])
        self.session.begin.assert_not_called()
        self.session.commit.assert_not_called()
        self.session.rollback.assert_not_called()
        self.session.close.assert_not_called()

    def test_nested_orm_refusal_keeps_outer_scope_active(self):
        g.orm_operation_active=True
        with self.assertRaises(service.InterviewTransactionError):self.update()
        with self.assertRaises(DatabaseError):service.list_interviews(7)
        self.assertTrue(g.orm_operation_active)
        self.factory.assert_not_called()

    def test_tracker_closes_read_unit_before_pure_display_transforms(self):
        def display(applications, interviews, cutoff):
            self.session.rollback.assert_called_once()
            self.session.close.assert_called_once()
            self.assertFalse(g.get('orm_operation_active', False))
            self.assertEqual(applications, [])
            self.assertEqual(interviews, [])
            return []
        with patch('tracker_data._fetch_all', return_value=[]), patch('tracker_data._list_interviews', return_value=[]), patch('tracker_data._application_rows', side_effect=display):
            page = tracker_data.load_tracker_page(7)
        self.assertEqual(page['applications'], [])
