import test from 'node:test';
import assert from 'node:assert/strict';
import { historyKindForPath, jobExpiryMilliseconds, loadJobHistory } from './jobHistory.js';

test('history follows the studio route and retains stem history elsewhere', () => {
    assert.equal(historyKindForPath('/score-to-midi'), 'score');
    assert.equal(historyKindForPath('/score-to-midi/'), 'score');
    for (const path of ['/', '/stems', '/architecture', '/cost']) assert.equal(historyKindForPath(path), 'stem');
});

test('score history uses its own endpoint and excludes other conversion directions', async () => {
    const controller = new AbortController();
    const score = { job_id: 'score', direction: 'score_to_midi' };
    const calls = [];
    const jobs = await loadJobHistory(async (...args) => {
        calls.push(args);
        return Response.json({ jobs: [score, { job_id: 'audio' }, { job_id: 'reverse', direction: 'midi_to_score' }] });
    }, 'score', controller.signal);
    assert.deepEqual(jobs, [score]);
    assert.equal(calls[0][0], '/score-jobs');
    assert.equal(calls[0][1].signal, controller.signal);
});

test('stem history retains its audio jobs and existing endpoint', async () => {
    const audio = { job_id: 'stem', stem_mode: '6-stems' };
    assert.deepEqual(await loadJobHistory(async (path) => {
        assert.equal(path, '/jobs');
        return Response.json({ jobs: [audio] });
    }, 'stem'), [audio]);
});

test('history surfaces unavailable libraries instead of treating them as empty', async () => {
    await assert.rejects(loadJobHistory(async () => Response.json(
        { error: 'Job history is temporarily unavailable.' }, { status: 503 },
    ), 'score'), /temporarily unavailable/);
    assert.deepEqual(await loadJobHistory(async () => Response.json({}), 'score'), []);
});

test('history retention accepts PostgreSQL ISO timestamps and cloud epoch seconds', () => {
    const iso = '2026-10-20T03:00:00Z';
    const milliseconds = Date.parse(iso);
    assert.equal(jobExpiryMilliseconds(iso), milliseconds);
    assert.equal(jobExpiryMilliseconds(milliseconds / 1000), milliseconds);
    assert.equal(jobExpiryMilliseconds(String(milliseconds / 1000)), milliseconds);
    for (const value of [null, undefined, '', 'invalid']) assert.ok(Number.isNaN(jobExpiryMilliseconds(value)));
});
