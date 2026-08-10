export const TRACK_GAIN_MIN_DB = -12;
export const TRACK_GAIN_MAX_DB = 12;

/** Clamp one console gain control to the supported ±12 dB range. */
export function clampTrackGainDb(value) {
    const numericValue = Number(value);
    return Math.max(
        TRACK_GAIN_MIN_DB,
        Math.min(TRACK_GAIN_MAX_DB, Number.isFinite(numericValue) ? numericValue : 0)
    );
}

/** Convert a dB gain value to the equivalent linear Web Audio gain. */
export function dbToLinearGain(decibels) {
    return 10 ** (Number.isFinite(Number(decibels)) ? Number(decibels) / 20 : 0);
}

/**
 * smplr's OutputChannel uses a squared MIDI-volume curve. Convert a relative
 * dB gain to that control while preserving smplr's normal 100-volume baseline.
 */
export function gainDbToSmplrOutputVolume(decibels) {
    return 100 * Math.sqrt(dbToLinearGain(decibels));
}

/**
 * Per-instrument MIDI playback balance.
 *
 * These scales affect generated MIDI synthesis only; they do not alter the
 * original Demucs stem audio or write a different velocity into exported MIDI.
 */
const MELODIC_TRACK_VELOCITY_SCALES = Object.freeze({
    guitar: 2.0,
    piano: 0.6,
});

/** Return smplr's finite 1–127 velocity for a melodic MIDI note. */
export function getMelodicPlaybackVelocity(note, trackName) {
    const sourceVelocity = note?.velocity !== undefined ? note.velocity : 0.8;
    const scale = MELODIC_TRACK_VELOCITY_SCALES[String(trackName).toLowerCase()] ?? 1;
    return Math.round(Math.max(1, Math.min(127, sourceVelocity * 127 * scale)));
}
