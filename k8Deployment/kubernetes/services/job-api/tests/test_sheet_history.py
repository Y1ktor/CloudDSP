"""Separate retained score history, safe previews, and immutable owner checks."""

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.authentication import AuthenticatedPrincipal, require_authenticated_principal
from app.database import DatabaseSettings, DatabaseUnavailable
from app.sheet_database import (
                          LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL,
                          list_retained_sheet_jobs_for_owner)
from app.sheet_routes import router as app
from app.sheet_routes import list_sheet_jobs, sheet_job_snapshot_response
from app.object_storage import ObjectStorageSettings
from app.presigned_download import PresignedDownloadContractError, create_presigned_download_url

JOB = '2fbd8181-3068-4e77-b46a-e0a9d667b2a7'


class ScoreHistoryTests(unittest.TestCase):
    def test_read_binds_owner_retention_direction_and_newest_first(self):
        settings = DatabaseSettings(host='db.test', port=5432, database='test',
                                    username='test', password='not-a-real-password')
        with (patch('app.sheet_database.DatabaseSettings.from_environment', return_value=settings),
              patch('app.sheet_database.psycopg.connect') as connect):
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [{'job_id': JOB, 'source_filename': 'Debussy.pdf'}]
            rows = list_retained_sheet_jobs_for_owner('verified-owner')
        self.assertEqual(rows[0]['job_id'], JOB)
        cursor.execute.assert_called_once_with(LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL, ('verified-owner',))
        self.assertIn('FROM public.midi_sheet_jobs', LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL)
        self.assertIn("direction = 'midi_to_sheet'", LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL)
        self.assertIn('expires_at > CURRENT_TIMESTAMP', LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL)
        self.assertIn('ORDER BY updated_at DESC', LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL)
        self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])
        self.assertNotIn('input_object_key', LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL)
        self.assertNotIn('result_midi_key', LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL)

    def test_route_uses_verified_owner_and_serializes_no_store_history(self):
        row = {'job_id': JOB, 'direction': 'midi_to_sheet', 'source_filename': 'Debussy.pdf',
               'status': 'completed', 'expires_at': datetime(2026, 10, 20, tzinfo=UTC)}
        with patch('app.sheet_routes.list_retained_sheet_jobs_for_owner', return_value=[row]) as query:
            response = list_sheet_jobs(AuthenticatedPrincipal(subject='owner-a'))
        query.assert_called_once_with('owner-a')
        payload = json.loads(response.body)
        self.assertEqual(datetime.fromisoformat(payload['jobs'][0]['expires_at']), row['expires_at'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        route = next(route for route in app.routes if route.path == '/sheet-jobs' and 'GET' in route.methods)
        self.assertIn(require_authenticated_principal, [dep.call for dep in route.dependant.dependencies])

    def test_database_outage_has_safe_retryable_response(self):
        with patch('app.sheet_routes.list_retained_sheet_jobs_for_owner', side_effect=DatabaseUnavailable('private detail')):
            response = list_sheet_jobs(AuthenticatedPrincipal(subject='owner-a'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {'error': 'Job history is temporarily unavailable.'})

    def test_pending_source_has_no_signature_and_removes_all_private_coordinates(self):
        row = {'job_id': JOB, 'status': 'upload_pending', 'source_uploaded': False,
               '_source_bucket': 'private', '_source_key': 'private/key'}
        with patch('app.sheet_routes.create_presigned_download_url') as sign:
            response = sheet_job_snapshot_response(row)
        sign.assert_not_called()
        payload = json.loads(response.body)
        self.assertNotIn('_source_bucket', payload)
        self.assertNotIn('_source_key', payload)
        self.assertNotIn('source_url', payload)

    def test_verified_source_is_signed_even_while_processing_or_failed(self):
        settings = MagicMock(uploads_bucket='clouddsp-uploads')
        for status in ('processing', 'failed'):
            with self.subTest(status=status):
                row = {'job_id': JOB, 'status': status, 'source_uploaded': True,
                       '_source_bucket': 'clouddsp-uploads', '_source_key': f'midi-sheet-inputs/{JOB}/source.mid'}
                with (patch('app.sheet_routes.ObjectStorageSettings.from_environment', return_value=settings),
                      patch('app.sheet_routes.create_presigned_download_url', return_value='http://source') as sign):
                    response = sheet_job_snapshot_response(row)
                self.assertEqual(json.loads(response.body)['source_url'], 'http://source')
                self.assertEqual(sign.call_args.kwargs['kind'], 'sheet-source')

    def test_source_signer_rejects_foreign_or_unexpected_objects_before_issuing_permission(self):
        settings = ObjectStorageSettings(internal_endpoint='http://minio.test:9000',
            public_endpoint='http://minio.localhost:8080', uploads_bucket='clouddsp-uploads',
            region='us-east-1', addressing_style='path', access_key='test', secret_key='not-real')
        with patch('app.presigned_download.boto3.client') as client:
            for key in (f'midi-sheet-inputs/{JOB}/wrong.pdf', f'uploads/{JOB}/source.mid',
                        'midi-sheet-inputs/other/source.png', f'midi-sheet-inputs/{JOB}/../source.png'):
                with self.subTest(key=key), self.assertRaises(PresignedDownloadContractError):
                    create_presigned_download_url(settings, job_id=JOB, object_key=key, kind='sheet-source')
            client.assert_not_called()
            client.return_value.generate_presigned_url.return_value = 'http://minio.localhost:8080/signed'
            for extension in ('mid',):
                key = f'midi-sheet-inputs/{JOB}/source.{extension}'
                create_presigned_download_url(settings, job_id=JOB, object_key=key, kind='sheet-source')
                self.assertEqual(client.return_value.generate_presigned_url.call_args.kwargs['Params']['Key'], key)
