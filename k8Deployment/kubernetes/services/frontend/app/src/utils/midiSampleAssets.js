/**
 * URLs for the dedicated, read-only MinIO playback bank.
 *
 * Unlike job artifacts, these are shared, non-user audio samples. The local
 * bootstrap mirrors every file under this bucket before the frontend image is
 * rolled out. Vite embeds only the public MinIO origin, never an S3 credential.
 */
export const MIDI_SAMPLE_BUCKET = 'clouddsp-midi-samples';

export function midiSampleUrl(path, origin = import.meta.env?.VITE_OBJECT_STORAGE_URL) {
    if (!origin || typeof origin !== 'string') {
        throw new Error('VITE_OBJECT_STORAGE_URL is required for local MIDI playback');
    }
    const endpoint = new URL(origin);
    if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.pathname !== '/' || endpoint.search || endpoint.hash) {
        throw new Error('MIDI sample origin must be an HTTP(S) origin without a path');
    }
    if (!path || path.split('/').some((segment) => !segment || segment === '.' || segment === '..')) {
        throw new Error('MIDI sample path must contain only nonempty object-key segments');
    }
    const encodedPath = path.split('/').map(encodeURIComponent).join('/');
    return `${endpoint.origin}/${MIDI_SAMPLE_BUCKET}/${encodedPath}`;
}

/** Match smplr's OGG-first preference while retaining its Safari MP3 fallback. */
export function soundfontFormat(audioElement, userAgent) {
    const safari = userAgent.includes('Safari') && !userAgent.includes('Chrome') && !userAgent.includes('Chromium');
    const oggSupport = audioElement.canPlayType('audio/ogg');
    return !safari && (oggSupport === 'probably' || oggSupport === 'maybe') ? 'ogg' : 'mp3';
}

export function soundfontSampleUrl(instrument) {
    const format = soundfontFormat(document.createElement('audio'), navigator.userAgent);
    return midiSampleUrl(`soundfonts/FluidR3_GM/${instrument}-${format}.js`);
}

/**
 * smplr concatenates piano sample names into URLs without encoding them.
 * Fifty-five official filenames contain '#' (for example "PP C#1.ogg");
 * browsers otherwise treat the rest as a URL fragment and request the wrong
 * MinIO key. Storage.fetch is smplr's supported request boundary, so encode
 * those path characters before the ordinary browser fetch.
 */
export const localPianoStorage = Object.freeze({
    fetch(url) {
        return fetch(url.replaceAll(' ', '%20').replaceAll('#', '%23'));
    },
});
