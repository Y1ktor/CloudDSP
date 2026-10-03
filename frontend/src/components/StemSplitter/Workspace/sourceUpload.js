const MAX_SOURCE_UPLOAD_BYTES = 256 * 1024 * 1024;
const SUPPORTED_AUDIO_EXTENSIONS = new Set([
    '.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.aiff', '.aif', '.webm',
]);

function sourceExtension(filename) {
    const index = filename.lastIndexOf('.');
    return index < 0 ? '' : filename.slice(index).toLowerCase();
}

/** Validate the selected File before the workspace creates an upload job. */
export function validateSourceUpload(file) {
    if (!SUPPORTED_AUDIO_EXTENSIONS.has(sourceExtension(file.name))) {
        return 'Choose WAV, MP3, FLAC, M4A, AAC, OGG, Opus, AIFF, or WebM audio.';
    }
    if (!Number.isFinite(file.size) || file.size < 1 || file.size > MAX_SOURCE_UPLOAD_BYTES) {
        return 'Choose an audio file no larger than 256 MiB.';
    }
    return null;
}
