/** Create one owner-bound MIDI-to-sheet job, then transfer bytes directly to MinIO. */
export const MAX_SHEET_SOURCE_BYTES = 10 * 1024 * 1024;
const SHEET_EXTENSIONS = new Set(['mid', 'midi']);

export function validateSheetMidiFile(file) {
    const extension = file?.name?.split('.').pop()?.toLowerCase();
    if (!SHEET_EXTENSIONS.has(extension)) return 'Choose a MID or MIDI file.';
    if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > MAX_SHEET_SOURCE_BYTES) {
        return 'Scores must be between 1 byte and 10 MiB.';
    }
    return '';
}

export async function uploadSheetMidi({ file, authenticatedFetch, uploadFetch = fetch }) {
    const validationError = validateSheetMidiFile(file);
    if (validationError) throw new Error(validationError);
    const response = await authenticatedFetch('/sheet-jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            direction: 'midi_to_sheet',
            filename: file.name,
            content_type: file.type || 'application/octet-stream',
            size_bytes: file.size,
        }),
    });
    const contract = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(contract.error || `Could not create a MIDI-to-sheet job (${response.status}).`);
    }
    if (
        response.status !== 201
        || !contract.job_id
        || contract.direction !== 'midi_to_sheet'
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
