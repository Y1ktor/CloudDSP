"""Separate retained score history, safe previews, and immutable owner checks."""

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.authentication import AuthenticatedPrincipal, require_authenticated_principal
from app.database import (DatabaseSettings, DatabaseUnavailable,
                          LIST_RETAINED_SCORE_JOBS_FOR_OWNER_SQL,
                          list_retained_score_jobs_for_owner)
from app.main import app, list_score_jobs, score_job_snapshot_response
from app.object_storage import ObjectStorageSettings
from app.presigned_download import PresignedDownloadContractError, create_presigned_download_url

JOB = '2fbd8181-3068-4e77-b46a-e0a9d667b2a7'


class ScoreHistoryTests(unittest.TestCase):
    def test_read_binds_owner_retention_direction_and_newest_first(self):
        settings = DatabaseSettings(host='db.test', port=5432, database='test',
                                    username='test', password='not-a-real-password')
        with (patch('app.database.DatabaseSettings.from_environment', return_value=settings),
              patch('app.database.psycopg.connect') as connect):
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [{'job_id': JOB, 'source_filename': 'Debussy.pdf'}]
            rows = list_retained_score_jobs_for_owner('verified-owner')
        self.assertEqual(rows[0]['job_id'], JOB)
        cursor.execute.assert_called_once_with(LIST_RETAINED_SCORE_JOBS_FOR_OWNER_SQL, ('verified-owner',))
        self.assertIn('FROM public.score_jobs', LIST_RETAINED_SCORE_JOBS_FOR_OWNER_SQL)
        self.assertIn("direction = 'score_to_midi'", LIST_RETAINED_SCORE_JOBS_FOR_OWNER_SQL)
        self.assertIn('expires_at > CURRENT_TIMESTAMP', LIST_RETAINED_SCORE_JOBS_FOR_OWNER_SQL)
        self.assertIn('ORDER BY updated_at DESC', LIST_RETAINED_SCORE_JOBS_FOR_OWNER_SQL)
        self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])
        self.assertNotIn('input_object_key', LIST_RETAINED_SCORE_JOBS_FOR_OWNER_SQL)
        self.assertNotIn('result_midi_key', LIST_RETAINED_SCORE_JOBS_FOR_OWNER_SQL)

    def test_route_uses_verified_owner_and_serializes_no_store_history(self):
        row = {'job_id': JOB, 'direction': 'score_to_midi', 'source_filename': 'Debussy.pdf',
               'status': 'completed', 'expires_at': datetime(2026, 10, 20, tzinfo=UTC)}
        with patch('app.main.list_retained_score_jobs_for_owner', return_value=[row]) as query:
            response = list_score_jobs(AuthenticatedPrincipal(subject='owner-a'))
        query.assert_called_once_with('owner-a')
        payload = json.loads(response.body)
        self.assertEqual(datetime.fromisoformat(payload['jobs'][0]['expires_at']), row['expires_at'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        route = next(route for route in app.routes if route.path == '/score-jobs' and 'GET' in route.methods)
        self.assertIn(require_authenticated_principal, [dep.call for dep in route.dependant.dependencies])

    def test_database_outage_has_safe_retryable_response(self):
        with patch('app.main.list_retained_score_jobs_for_owner', side_effect=DatabaseUnavailable('private detail')):
            response = list_score_jobs(AuthenticatedPrincipal(subject='owner-a'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {'error': 'Job history is temporarily unavailable.'})

    def test_pending_source_has_no_signature_and_removes_all_private_coordinates(self):
        row = {'job_id': JOB, 'status': 'upload_pending', 'source_uploaded': False,
               '_source_bucket': 'private', '_source_key': 'private/key'}
        with patch('app.main.create_presigned_download_url') as sign:
            response = score_job_snapshot_response(row)
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
                       '_source_bucket': 'clouddsp-uploads', '_source_key': f'score-inputs/{JOB}/source.pdf'}
                with (patch('app.main.ObjectStorageSettings.from_environment', return_value=settings),
                      patch('app.main.create_presigned_download_url', return_value='http://source') as sign):
                    response = score_job_snapshot_response(row)
                self.assertEqual(json.loads(response.body)['source_url'], 'http://source')
                self.assertEqual(sign.call_args.kwargs['kind'], 'score-source')

    def test_source_signer_rejects_foreign_or_unexpected_objects_before_issuing_permission(self):
        settings = ObjectStorageSettings(internal_endpoint='http://minio.test:9000',
            public_endpoint='http://minio.localhost:8080', uploads_bucket='clouddsp-uploads',
            region='us-east-1', addressing_style='path', access_key='test', secret_key='not-real')
        with patch('app.presigned_download.boto3.client') as client:
            for key in (f'score-inputs/{JOB}/wrong.pdf', f'uploads/{JOB}/source.pdf',
                        'score-inputs/other/source.png', f'score-inputs/{JOB}/../source.png'):
                with self.subTest(key=key), self.assertRaises(PresignedDownloadContractError):
                    create_presigned_download_url(settings, job_id=JOB, object_key=key, kind='score-source')
            client.assert_not_called()
            client.return_value.generate_presigned_url.return_value = 'http://minio.localhost:8080/signed'
            for extension in ('pdf', 'png', 'jpg', 'jpeg'):
                key = f'score-inputs/{JOB}/source.{extension}'
                create_presigned_download_url(settings, job_id=JOB, object_key=key, kind='score-source')
                self.assertEqual(client.return_value.generate_presigned_url.call_args.kwargs['Params']['Key'], key)
