/** Create one owner-bound score job, then transfer bytes directly to MinIO. */
export const MAX_SCORE_SOURCE_BYTES = 25 * 1024 * 1024;
const SCORE_EXTENSIONS = new Set(['pdf', 'png', 'jpg', 'jpeg']);

export function validateScoreFile(file) {
    const extension = file?.name?.split('.').pop()?.toLowerCase();
    if (!SCORE_EXTENSIONS.has(extension)) return 'Choose a PDF, PNG, JPG, or JPEG score.';
    if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > MAX_SCORE_SOURCE_BYTES) {
        return 'Scores must be between 1 byte and 25 MiB.';
    }
    return '';
}

export async function uploadScore({ file, authenticatedFetch, uploadFetch = fetch }) {
    const validationError = validateScoreFile(file);
    if (validationError) throw new Error(validationError);
    const response = await authenticatedFetch('/score-jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            direction: 'score_to_midi',
            filename: file.name,
            content_type: file.type || 'application/octet-stream',
            size_bytes: file.size,
        }),
    });
    const contract = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(contract.error || `Could not create a score job (${response.status}).`);
    }
    if (
        response.status !== 201
        || !contract.job_id
        || contract.direction !== 'score_to_midi'
        || !contract.upload_url
        || !contract.upload_fields
        || contract.max_source_bytes < file.size
    ) {
        throw new Error('The score API returned an incomplete upload contract. Please try again.');
    }
    const form = new FormData();
    Object.entries(contract.upload_fields).forEach(([name, value]) => form.append(name, value));
    form.append('file', file);
    const uploadResponse = await uploadFetch(contract.upload_url, { method: 'POST', body: form });
    if (!uploadResponse.ok) {
        throw new Error(`Score upload failed (${uploadResponse.status}). Please try again.`);
    }
    // S3 success proves object acceptance, not yet a verified OMR result.
    // MinIO publishes the score-prefix event to the waiting RabbitMQ queue.
    return { jobId: contract.job_id, status: 'uploaded' };
}
