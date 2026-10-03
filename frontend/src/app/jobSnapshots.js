const PRESIGNED_URL_REFRESH_SAFETY_MS = 60_000;

export function isJobPending(job) {
    return job && !['completed', 'failed'].includes(job.status);
}

export function hasTerminalMidiArtifacts(job) {
    /*
     * A top-level ``completed`` status is normally sufficient, but the
     * browser must keep fetching until the snapshot actually contains every
     * stem's terminal MIDI state and a usable URL for each successful result.
     * This protects the workspace from a missed WebSocket hint or a stale
     * eventually-consistent API read immediately after processing finishes.
     */
    const stems = job?.stems || {};
    const midi = job?.midi || {};
    const stemNames = Object.keys(stems);
    if (stemNames.length === 0) return false;
    return stemNames.every((stemName) => {
        const artifact = midi[stemName];
        if (artifact?.status === 'failed') return true;
        return artifact?.status === 'ready' && Boolean(artifact.url);
    });
}

export function needsJobRefresh(job) {
    // A failed ingestion/worker job is terminal even when it never produced
    // stems or MIDI. Continuing to poll it makes the browser look as if it is
    // still waiting for results that cannot arrive.
    if (job?.status === 'failed') return false;
    return !job || isJobPending(job) || !hasTerminalMidiArtifacts(job);
}

export function sourceUrlForJob(job) {
    // A linked source has no object until ingestion uploads its durable
    // input key. The Job API omits original_url after a pre-upload failure;
    // retain the status check as a client-side guard against a stale API
    // snapshot so the audio loader never requests a known-missing key.
    return job?.status === 'source_ingestion'
        || (job?.source_type === 'yt-dlp'
            && job?.status === 'failed'
            && !job?.source_uploaded)
        ? null
        : job?.original_url;
}

export function messageForJob(job, fallback, messages) {
    if (!job) return fallback;
    if (job.status === 'source_ingestion') return 'Downloading audio from the linked source…';
    if (job.status === 'upload_pending') return messages.uploadPending;
    if (job.status === 'stem_processing') return messages.stemProcessing;
    if (job.status === 'midi_processing') return messages.midiProcessing;
    if (job.status === 'completed') return 'Stems and MIDI extraction are complete.';
    if (job.status === 'failed') return job.error || 'Processing failed. See the job status for details.';
    return fallback;
}

export function urlsForReadyArtifacts(artifacts) {
    return Object.fromEntries(
        Object.entries(artifacts || {})
            .filter(([, artifact]) => artifact?.status === 'ready' && artifact.url)
            .map(([name, artifact]) => [name, artifact.url]),
    );
}

export function readyArtifactNames(artifacts) {
    return Object.entries(artifacts || {})
        .filter(([, artifact]) => artifact?.status === 'ready' && artifact.url)
        .map(([name]) => name);
}

export function presignedUrlIsUsable(url) {
    try {
        const parsed = new URL(url);
        const legacyExpires = Number(parsed.searchParams.get('Expires'));
        if (Number.isFinite(legacyExpires) && legacyExpires > 0) {
            return legacyExpires * 1_000 > Date.now() + PRESIGNED_URL_REFRESH_SAFETY_MS;
        }

        const signatureDate = parsed.searchParams.get('X-Amz-Date');
        const signatureLifetime = Number(parsed.searchParams.get('X-Amz-Expires'));
        if (signatureDate && Number.isFinite(signatureLifetime)) {
            const match = signatureDate.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
            if (!match) return false;
            const [, year, month, day, hour, minute, second] = match;
            const expiresAt = Date.UTC(year, Number(month) - 1, day, hour, minute, second)
                + signatureLifetime * 1_000;
            return expiresAt > Date.now() + PRESIGNED_URL_REFRESH_SAFETY_MS;
        }

        // This branch is only for a future non-expiring artifact source. Job
        // API URLs today always use one of the two presigned URL formats.
        return true;
    } catch {
        return false;
    }
}

export function preserveReadyArtifactUrls(previous, snapshot) {
    /*
     * Job API snapshots carry newly signed URLs. Replacing an already-mounted
     * media element's URL on every progress poll can cancel its load, and it
     * causes the MIDI loader to re-download the same immutable artifact. Keep
     * a URL only while it has at least one minute left; otherwise accept the
     * Job API's fresh URL before the browser retries an expired signature.
     */
    if (!previous) return snapshot;
    const result = { ...snapshot };
    if (snapshot.original_url && previous.original_url && presignedUrlIsUsable(previous.original_url)) {
        result.original_url = previous.original_url;
    }
    for (const collectionName of ['stems', 'midi']) {
        const previousArtifacts = previous[collectionName] || {};
        const nextArtifacts = snapshot[collectionName] || {};
        result[collectionName] = Object.fromEntries(
            Object.entries(nextArtifacts).map(([name, artifact]) => {
                const prior = previousArtifacts[name];
                if (
                    artifact?.status === 'ready'
                    && artifact.url
                    && prior?.status === 'ready'
                    && prior.url
                    && presignedUrlIsUsable(prior.url)
                ) {
                    return [name, { ...artifact, url: prior.url }];
                }
                return [name, artifact];
            }),
        );
    }
    return result;
}
