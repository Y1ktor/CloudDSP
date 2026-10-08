/** Each studio reads its own account-backed library; no browser history cache. */
export function historyKindForPath(pathname) {
    return pathname === '/score-to-midi' || pathname === '/score-to-midi/' ? 'score' : 'stem';
}

export async function loadJobHistory(authenticatedFetch, kind, signal) {
    const path = kind === 'score' ? '/score-jobs' : '/jobs';
    const response = await authenticatedFetch(path, { signal });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Could not load previous jobs (${response.status}).`);
    // Keep score rows explicitly directional, including when another converter
    // later gets its own table/API. Stem libraries retain their existing shape.
    return (Array.isArray(payload.jobs) ? payload.jobs : []).filter(
        (job) => kind !== 'score' || job.direction === 'score_to_midi',
    );
}

/** Local PostgreSQL returns ISO timestamps; cloud history uses epoch seconds. */
export function jobExpiryMilliseconds(value) {
    if (value === null || value === undefined || value === '') return NaN;
    const seconds = Number(value);
    return Number.isFinite(seconds) ? seconds * 1000 : Date.parse(value);
}
