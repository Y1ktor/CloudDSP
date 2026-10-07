import assert from 'node:assert/strict';
import test from 'node:test';
import { MAX_SCORE_SOURCE_BYTES, uploadScore, validateScoreFile } from './scoreUpload.js';

const file = new File(['%PDF-1.4\n'], 'clean.pdf', { type: 'application/pdf' });

test('score upload rejects invalid files before creating a job', async () => {
    assert.match(validateScoreFile(new File(['x'], '../bad.svg')), /PDF/);
    assert.match(validateScoreFile({ name: 'huge.png', size: MAX_SCORE_SOURCE_BYTES + 1 }), /25 MiB/);
    let called = false;
    await assert.rejects(uploadScore({
        file: { name: 'empty.pdf', size: 0 },
        authenticatedFetch: () => { called = true; },
    }), /25 MiB/);
    assert.equal(called, false);
});

test('score upload creates an authenticated intent then posts exact form to MinIO', async () => {
    const events = [];
    const result = await uploadScore({
        file,
        authenticatedFetch: async (path, options) => {
            events.push('api');
            assert.equal(path, '/score-jobs');
            assert.equal(options.method, 'POST');
            assert.deepEqual(JSON.parse(options.body), {
                direction: 'score_to_midi', filename: 'clean.pdf',
                content_type: 'application/pdf', size_bytes: file.size,
            });
            return {
                ok: true, status: 201,
                json: async () => ({
                    job_id: 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',
                    direction: 'score_to_midi',
                    upload_url: 'http://minio.localhost:8080/clouddsp-uploads',
                    upload_fields: { key: 'score-inputs/a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11/source.pdf', policy: 'signed' },
                    max_source_bytes: MAX_SCORE_SOURCE_BYTES,
                }),
            };
        },
        uploadFetch: async (url, options) => {
            events.push('minio');
            assert.equal(url, 'http://minio.localhost:8080/clouddsp-uploads');
            assert.equal(options.method, 'POST');
            assert.equal(options.body.get('policy'), 'signed');
            assert.equal(options.body.get('file').name, 'clean.pdf');
            return { ok: true };
        },
    });
    assert.deepEqual(events, ['api', 'minio']);
    assert.equal(result.status, 'uploaded');
});

test('failed secure form never posts source bytes', async () => {
    let posted = false;
    await assert.rejects(uploadScore({
        file,
        authenticatedFetch: async () => ({ ok: false, status: 503, json: async () => ({ error: 'Unavailable.' }) }),
        uploadFetch: () => { posted = true; },
    }), /Unavailable/);
    assert.equal(posted, false);
});
