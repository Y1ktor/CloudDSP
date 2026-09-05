import React, { useEffect, useMemo, useRef } from 'react';
import {
    getAdtofDrumVoice,
    getDrumPlaybackVelocity,
    getDrumVoiceTrackId,
    getMidiNotes,
    isDrumVoiceAudible
} from '../utils/DrumMidi';
import { gainDbToSmplrOutputVolume, getMelodicPlaybackVelocity } from '../utils/MidiPlayback';

// The scheduler deliberately runs independently from the visual playhead.
// A 40 ms wake-up with 250 ms of scheduled Web Audio time is enough headroom
// for normal browser work without repeatedly walking every note on every
// animation frame.
const SCHEDULER_INTERVAL_MS = 40;
const SCHEDULER_LOOKAHEAD_SECONDS = 0.25;
const DISABLED_NOTE_VELOCITY = 0.015;

function forEachSynthRef(synthRef, isDrumTrack, callback) {
    if (isDrumTrack) {
        synthRef?.forEach?.(callback);
        return;
    }
    if (synthRef) callback(synthRef);
}

function stopSynths(synthRef, isDrumTrack) {
    forEachSynthRef(synthRef, isDrumTrack, (candidateRef) => {
        try {
            candidateRef?.current?.stop();
        } catch {
            // A sampler can be mid-disposal during an unmount.
        }
    });
}

/**
 * Build a stable, time-ordered event list once per MIDI data update. The
 * previous implementation flattened and scanned this list for every visual
 * animation frame, which became especially expensive for long projects.
 */
function createScheduledNotes(midiData) {
    return getMidiNotes(midiData)
        .map((note, sourceIndex) => ({
            note,
            sourceIndex,
            time: Number(note?.time),
        }))
        .filter(({ time }) => Number.isFinite(time) && time >= 0)
        .sort((left, right) => left.time - right.time || left.sourceIndex - right.sourceIndex);
}

/** Find the first event at or after a transport position in O(log n). */
function lowerBoundByTime(notes, position) {
    let low = 0;
    let high = notes.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (notes[middle].time < position) low = middle + 1;
        else high = middle;
    }
    return low;
}

function fallbackPlaybackRate(activeBpm, originalBpm) {
    const requestedRate = Number(activeBpm) / Number(originalBpm);
    if (!Number.isFinite(requestedRate) || requestedRate <= 0) return 1;
    // Keep the fallback consistent with AudioMultiTrackPlayer's source-node
    // playback-rate clamp when an older caller has not supplied transportRef.
    return Math.max(0.5, Math.min(4, requestedRate));
}

/**
 * Read the audio-clock transport state without relying on a React playhead
 * update. transportRef is produced by useAudioMultiTrackPlayer and has the
 * shape { position, offset, startTime, rate, isPlaying, revision }. The old
 * progress/start-time arguments remain as a temporary compatibility fallback.
 */
function readTransportSnapshot({
    audioCtxRef,
    transportRef,
    progress,
    isPlaying,
    activeBpm,
    originalBpm,
    transportStartTime,
}) {
    const context = audioCtxRef?.current;
    if (!context) return null;

    const transport = transportRef?.current;
    const rate = Number(transport?.rate);
    const playbackRate = Number.isFinite(rate) && rate > 0
        ? rate
        : fallbackPlaybackRate(activeBpm, originalBpm);
    const offset = Number.isFinite(Number(transport?.offset))
        ? Number(transport.offset)
        : Math.max(0, Number(progress) || 0);
    const startTime = Number.isFinite(Number(transport?.startTime))
        ? Number(transport.startTime)
        : (Number.isFinite(Number(transportStartTime)) ? Number(transportStartTime) : null);
    const transportIsPlaying = typeof transport?.isPlaying === 'boolean'
        ? transport.isPlaying
        : Boolean(isPlaying);
    const now = context.currentTime;

    // During the short scheduled-start lead, the logical position remains at
    // offset. Once sources start, derive position directly from AudioContext.
    const position = transportIsPlaying && startTime !== null
        ? offset + Math.max(0, now - startTime) * playbackRate
        : (Number.isFinite(Number(transport?.position))
            ? Number(transport.position)
            : offset);

    // A monotonically increasing revision is emitted for play, pause, seek,
    // cycle restart, and playback-rate changes. It lets the scheduler reset
    // its cursor without treating normal clock motion as a discontinuity.
    const revision = Number.isFinite(Number(transport?.revision))
        ? Number(transport.revision)
        : null;
    const fallbackKey = `${transportIsPlaying ? 'playing' : 'paused'}:${startTime ?? 'none'}:${playbackRate}`;

    return {
        context,
        now,
        offset,
        startTime,
        position: Math.max(0, position),
        playbackRate,
        isPlaying: transportIsPlaying,
        transportKey: revision === null ? fallbackKey : `revision:${revision}`,
    };
}

function isTrackAudible(trackName, mutedTracks, soloedTracks) {
    const hasSolos = Object.values(soloedTracks || {}).some(Boolean);
    return hasSolos ? Boolean(soloedTracks?.[trackName]) : !mutedTracks?.[trackName];
}

function markStopped(schedulerState, synthRef, isDrumTrack) {
    if (!schedulerState.stopped) {
        stopSynths(synthRef, isDrumTrack);
    }
    schedulerState.stopped = true;
    schedulerState.forceReset = true;
}

/**
 * useMidiSynth
 *
 * Schedules a track through a short, audio-clock-driven look-ahead window.
 * It indexes a sorted note list with a cursor, so transport frames advance in
 * O(notes due in the next 250 ms), not O(all notes in every track).
 *
 * @param {React.MutableRefObject<AudioContext>} audioCtxRef Shared Web Audio context.
 * @param {number} progress Legacy visual-position fallback. New callers must
 *   pass transportRef and should not pass a per-frame progress prop.
 * @param {boolean} isPlaying React transport state, retained for lifecycle
 *   compatibility; transportRef is authoritative while it is available.
 * @param {Object} parsedMidiStems Parsed `@tonejs/midi` data for all tracks.
 * @param {string} trackName The specific stem bound to this synth.
 * @param {number} activeBpm Current user-adjusted BPM.
 * @param {number} originalBpm Backend-selected original/master BPM.
 * @param {React.MutableRefObject|Map} synthRef Per-track synth ref, or per-voice drum refs.
 * @param {boolean} isMidiMode Whether MIDI synthesis is active for this track.
 * @param {Object} mutedTracks Track mute map.
 * @param {Object} soloedTracks Track solo map.
 * @param {Object} drumMutedVoices ADTOF lane mute map.
 * @param {Object} drumSoloedVoices ADTOF lane solo map.
 * @param {number|null} transportStartTime Legacy scheduled context-time fallback.
 * @param {number} trackGainDb Parent track gain.
 * @param {Object} drumVoiceGainsDb Individual ADTOF lane gains.
 * @param {React.MutableRefObject} transportRef Preferred stable transport
 *   reference from useAudioMultiTrackPlayer.
 */
export function useMidiSynth(
    audioCtxRef,
    progress,
    isPlaying,
    parsedMidiStems,
    trackName,
    activeBpm,
    originalBpm,
    synthRef,
    isMidiMode,
    mutedTracks = {},
    soloedTracks = {},
    drumMutedVoices = {},
    drumSoloedVoices = {},
    transportStartTime = null,
    trackGainDb = 0,
    drumVoiceGainsDb = {},
    transportRef = null,
) {
    const trackData = parsedMidiStems?.[trackName];
    const isDrumTrack = trackData?.isAdtofDrum === true;
    const schedulerStateRef = useRef({
        cursor: 0,
        noteListVersion: -1,
        transportKey: null,
        forceReset: true,
        stopped: true,
        lastStartErrorKey: null,
    });
    const noteListVersionRef = useRef(0);

    // A MIDI editor operation replaces the top-level parsedMidiStems object
    // after mutating note instances. Depending on that object deliberately
    // rebuilds the index after an edit, undo, or revert without putting any
    // work on the visual-playhead path.
    const scheduledNotes = useMemo(() => {
        if (!parsedMidiStems || !trackData?.midiData) return [];
        return createScheduledNotes(trackData.midiData);
    }, [parsedMidiStems, trackData?.midiData]);
    const scheduledNotesRef = useRef(scheduledNotes);
    useEffect(() => {
        scheduledNotesRef.current = scheduledNotes;
        noteListVersionRef.current += 1;
        const schedulerState = schedulerStateRef.current;
        schedulerState.forceReset = true;
        schedulerState.stopped = true;
        stopSynths(synthRef, isDrumTrack);
    }, [scheduledNotes, synthRef, isDrumTrack]);

    // Read the latest values from the scheduler interval without restarting
    // it for visual progress updates or gain-only UI changes.
    const latestSettingsRef = useRef(null);
    latestSettingsRef.current = {
        progress,
        isPlaying,
        parsedMidiStems,
        activeBpm,
        originalBpm,
        isMidiMode,
        mutedTracks,
        soloedTracks,
        drumMutedVoices,
        drumSoloedVoices,
        transportStartTime,
        transportRef,
    };

    // The MIDI loader constructs isolated synth outputs before its parsed MIDI
    // becomes available. Keep each output at the same dB gain as its console
    // row; unlike velocity scaling, this also preserves positive gain on notes
    // whose source MIDI velocity is already 127.
    useEffect(() => {
        forEachSynthRef(synthRef, isDrumTrack, (candidateRef, voiceId) => {
            const voiceTrackId = isDrumTrack
                ? getDrumVoiceTrackId(trackName, voiceId)
                : null;
            const voiceGainDb = voiceTrackId ? Number(drumVoiceGainsDb[voiceTrackId]) || 0 : 0;
            const totalGainDb = (Number(trackGainDb) || 0) + voiceGainDb;
            if (candidateRef?.current?.output) {
                candidateRef.current.output.volume = gainDbToSmplrOutputVolume(totalGainDb);
            }
        });
    }, [synthRef, isDrumTrack, trackName, trackGainDb, drumVoiceGainsDb]);

    // Stop already scheduled one-shots and seek the scheduler cursor when a
    // user-visible playback control changes. smplr cannot selectively cancel
    // one future note, so rebuilding only the short look-ahead window is the
    // correct immediate response to M/S, MIDI mode, and tempo changes.
    useEffect(() => {
        const schedulerState = schedulerStateRef.current;
        schedulerState.forceReset = true;
        schedulerState.stopped = true;
        stopSynths(synthRef, isDrumTrack);
    }, [
        isPlaying,
        isMidiMode,
        activeBpm,
        originalBpm,
        mutedTracks,
        soloedTracks,
        drumMutedVoices,
        drumSoloedVoices,
        synthRef,
        isDrumTrack,
    ]);

    // Cleanup when a scheduler unmounts (for example, when a different saved
    // job replaces the current MIDI set).
    useEffect(() => () => {
        stopSynths(synthRef, isDrumTrack);
    }, [synthRef, isDrumTrack]);

    // One bounded interval per active MIDI stem. It is intentionally not tied
    // to progress: progress is a visual concern and may update at display rate
    // or be throttled independently by Safari.
    useEffect(() => {
        if (!isPlaying || !isMidiMode) return undefined;

        const scheduleDueNotes = () => {
            const settings = latestSettingsRef.current;
            if (!settings?.isMidiMode || !settings.isPlaying || !synthRef || !settings.originalBpm) {
                markStopped(schedulerStateRef.current, synthRef, isDrumTrack);
                return;
            }

            const snapshot = readTransportSnapshot({
                audioCtxRef,
                transportRef: settings.transportRef,
                progress: settings.progress,
                isPlaying: settings.isPlaying,
                activeBpm: settings.activeBpm,
                originalBpm: settings.originalBpm,
                transportStartTime: settings.transportStartTime,
            });
            if (!snapshot || !snapshot.isPlaying) {
                markStopped(schedulerStateRef.current, synthRef, isDrumTrack);
                return;
            }

            const schedulerState = schedulerStateRef.current;
            if (!isTrackAudible(trackName, settings.mutedTracks, settings.soloedTracks)) {
                markStopped(schedulerState, synthRef, isDrumTrack);
                return;
            }

            const notes = scheduledNotesRef.current;
            const noteListVersion = noteListVersionRef.current;
            if (
                schedulerState.forceReset
                || schedulerState.stopped
                || schedulerState.noteListVersion !== noteListVersion
                || schedulerState.transportKey !== snapshot.transportKey
            ) {
                // A transport revision means sources were re-scheduled (play,
                // seek, BPM change, or cycle restart). Start from the current
                // audio-clock position rather than replaying previously due
                // notes, and cancel the prior short scheduling window.
                if (!schedulerState.stopped) stopSynths(synthRef, isDrumTrack);
                schedulerState.cursor = lowerBoundByTime(notes, snapshot.position);
                schedulerState.noteListVersion = noteListVersion;
                schedulerState.transportKey = snapshot.transportKey;
                schedulerState.forceReset = false;
                schedulerState.stopped = false;
                schedulerState.lastStartErrorKey = null;
            }

            const windowEnd = snapshot.position + SCHEDULER_LOOKAHEAD_SECONDS * snapshot.playbackRate;
            while (schedulerState.cursor < notes.length) {
                const scheduledNote = notes[schedulerState.cursor];
                if (scheduledNote.time >= windowEnd) break;
                schedulerState.cursor += 1;

                const { note } = scheduledNote;
                // A velocity below this threshold is CloudDSP's custom
                // disabled-note marker. It remains visible/exportable but is
                // intentionally silent in local synthesis.
                if (note.velocity !== undefined && note.velocity <= DISABLED_NOTE_VELOCITY) continue;

                const drumVoice = isDrumTrack ? getAdtofDrumVoice(note.midi) : null;
                // ADTOF pitch values identify drum classes. Never fall back to
                // a piano pitch when a malformed drum MIDI note is encountered.
                if (isDrumTrack && !drumVoice) continue;
                if (isDrumTrack && !isDrumVoiceAudible(
                    trackName,
                    drumVoice,
                    settings.drumMutedVoices,
                    settings.drumSoloedVoices,
                )) continue;

                const noteSynthRef = drumVoice
                    ? synthRef.get?.(drumVoice.id)
                    : synthRef;
                if (!noteSynthRef?.current) {
                    // Do not advance beyond an unavailable output. Retrying on
                    // the next tick preserves the former hook's late-loader
                    // behavior instead of silently losing a whole time range.
                    schedulerState.cursor -= 1;
                    break;
                }

                const relativeTransportTime = Math.max(0, scheduledNote.time - snapshot.offset);
                const scheduledContextTime = Math.max(
                    snapshot.now,
                    (snapshot.startTime ?? snapshot.now) + relativeTransportTime / snapshot.playbackRate,
                );
                const duration = Number(note.duration);
                const scheduledDuration = Number.isFinite(duration) && duration > 0
                    ? duration / snapshot.playbackRate
                    : 0.01;

                try {
                    noteSynthRef.current.start({
                        note: drumVoice ? drumVoice.sample : note.midi,
                        velocity: drumVoice
                            ? getDrumPlaybackVelocity(note, drumVoice)
                            : getMelodicPlaybackVelocity(note, trackName),
                        time: scheduledContextTime,
                        duration: scheduledDuration,
                    });
                } catch (error) {
                    // A synth can be replaced while samples are loading. Retry
                    // this event on the next tick rather than marking it as
                    // scheduled and creating a permanent gap.
                    schedulerState.cursor -= 1;
                    const errorKey = `${snapshot.transportKey}:${schedulerState.cursor}:${error?.message || 'unknown'}`;
                    if (schedulerState.lastStartErrorKey !== errorKey) {
                        schedulerState.lastStartErrorKey = errorKey;
                        console.warn(`[CloudDSP] MIDI scheduler could not queue '${trackName}' yet:`, error);
                    }
                    break;
                }
            }
        };

        scheduleDueNotes();
        const intervalId = window.setInterval(scheduleDueNotes, SCHEDULER_INTERVAL_MS);
        return () => window.clearInterval(intervalId);
    }, [audioCtxRef, isDrumTrack, isMidiMode, isPlaying, synthRef, trackName]);
}
