const DEMO_MANIFEST_URL = '/demo/manifest.json';

function sameOriginAssetUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
        const url = new URL(value.trim(), window.location.origin);
        if (
            url.origin !== window.location.origin
            || !['http:', 'https:'].includes(url.protocol)
            || !url.pathname.startsWith('/demo/')
        ) {
            return null;
        }
        return url.href;
    } catch {
        return null;
    }
}

function normaliseArtifact(value) {
    const source = typeof value === 'string' ? { url: value } : value;
    if (!source || typeof source !== 'object') return null;

    const url = sameOriginAssetUrl(source.url || source.path);
    if (!url) return null;
    return {
        ...source,
        status: 'ready',
        url,
    };
}

function normaliseArtifactMap(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(
        Object.entries(value)
            .map(([name, artifact]) => [name, normaliseArtifact(artifact)])
            .filter(([name, artifact]) => Boolean(name && artifact)),
    );
}

function normaliseTempo(value) {
    const source = typeof value === 'number' ? { bpm: value } : value;
    const bpm = Number(source?.bpm);
    if (!Number.isFinite(bpm) || bpm <= 0) return null;
    return {
        ...source,
        bpm,
        confidence: source.confidence || 'demo',
    };
}

function normaliseDemoJob(value, index) {
    if (!value || typeof value !== 'object') return null;
    const id = String(value.id || value.job_id || '').trim();
    const title = String(value.title || value.source_filename || '').trim();
    const original = normaliseArtifact(
        value.original
        || value.original_url
        || value.artifacts?.original,
    );
    if (!id || !title || !original) return null;

    return {
        id,
        title,
        description: typeof value.description === 'string' ? value.description.trim() : '',
        sourceFilename: String(value.source_filename || value.filename || title).trim(),
        stemMode: String(value.stem_mode || '6-stems'),
        isDefault: value.default === true || value.is_default === true || index === 0,
        original,
        stems: normaliseArtifactMap(value.stems || value.artifacts?.stems),
        midi: normaliseArtifactMap(value.midi || value.artifacts?.midi),
        tempo: normaliseTempo(value.tempo || value.bpm),
    };
}

/**
 * Load the public, same-origin demo catalog. A missing catalog is expected
 * before curated demo assets are published, so callers receive an empty list
 * instead of an application error.
 *
 * Contract:
 * {
 *   "default_job_id": "demo-one",
 *   "jobs": [{
 *     "id": "demo-one",
 *     "title": "Example song",
 *     "source_filename": "example.wav",
 *     "original": "/demo/demo-one/original.wav",
 *     "stems": { "vocals": "/demo/demo-one/stems/vocals.wav" },
 *     "midi": { "vocals": "/demo/demo-one/midi/vocals.mid" },
 *     "tempo": { "bpm": 120, "confidence": "high" }
 *   }]
 * }
 */
export async function loadDemoCatalog({ signal } = {}) {
    const response = await fetch(DEMO_MANIFEST_URL, {
        cache: 'no-cache',
        credentials: 'same-origin',
        signal,
    });
    if (response.status === 404) return { jobs: [], defaultJobId: null };
    if (!response.ok) throw new Error(`Demo catalog request failed (HTTP ${response.status}).`);
    const contentType = response.headers.get('Content-Type') || '';
    if (!/(?:^|\s|;)application\/(?:[a-z0-9.+-]*\+)?json(?:\s*;|\s*$)/i.test(contentType)) {
        throw new Error('Demo catalog response is not JSON.');
    }

    const payload = await response.json();
    const rawJobs = Array.isArray(payload) ? payload : payload?.jobs || payload?.examples || [];
    const jobs = (Array.isArray(rawJobs) ? rawJobs : [])
        .map(normaliseDemoJob)
        .filter(Boolean);
    const requestedDefaultId = !Array.isArray(payload)
        ? String(payload?.default_job_id || payload?.defaultJobId || '').trim()
        : '';
    const defaultJob = jobs.find((job) => job.id === requestedDefaultId)
        || jobs.find((job) => job.isDefault)
        || jobs[0]
        || null;
    return { jobs, defaultJobId: defaultJob?.id || null };
}

export function createDemoJobSnapshot(demo) {
    if (!demo) return null;
    const jobId = `demo:${demo.id}`;
    return {
        job_id: jobId,
        is_demo: true,
        demo_id: demo.id,
        status: 'completed',
        revision: 1,
        source_type: 'demo',
        source_filename: demo.sourceFilename,
        stem_mode: demo.stemMode,
        original_url: demo.original.url,
        stems: demo.stems,
        midi: demo.midi,
        tempo: demo.tempo,
    };
}
