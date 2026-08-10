import React, { useRef, useEffect } from 'react';
import {
    getAdtofDrumVoice,
    getDrumPlaybackVelocity,
    getDrumVoiceTrackId,
    getMidiNotes,
    isDrumVoiceAudible
} from '../utils/DrumMidi';
import { gainDbToSmplrOutputVolume, getMelodicPlaybackVelocity } from '../utils/MidiPlayback';

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
 * useMidiSynth
 * 
 * Custom hook to encapsulate all audio scheduling and MIDI synthesis logic using the `smplr` library.
 * Isolates Web Audio API scheduling from the presentational UI components.
 * 
 * @param {React.MutableRefObject<AudioContext>} audioCtxRef - Reference to the shared Web Audio API AudioContext.
 * @param {number} progress - The current playback time of the master timeline in seconds.
 * @param {boolean} isPlaying - Whether the master transport is currently playing.
 * @param {Object} parsedMidiStems - Dictionary containing parsed `@tonejs/midi` data for all tracks.
 * @param {string} trackName - The name of the specific track this synth is bound to (e.g. 'piano', 'bass').
 * @param {number} activeBpm - The current user-adjusted BPM of the master timeline.
 * @param {number} originalBpm - The master original BPM determined by the project, used to calculate playbackRate.
 * @param {React.MutableRefObject|Map} synthRef - Per-track synth ref, or per-voice refs for an ADTOF kit.
 * @param {boolean} isMidiMode - Whether MIDI synthesis is currently active for this track.
 * @param {Object} mutedTracks - Dictionary of muted tracks.
 * @param {Object} soloedTracks - Dictionary of soloed audio tracks.
 * @param {Object} drumMutedVoices - Dictionary of muted ADTOF MIDI lanes.
 * @param {Object} drumSoloedVoices - Dictionary of soloed ADTOF MIDI lanes.
 * @param {number|null} transportStartTime - Scheduled AudioContext time for the shared stem transport.
 * @param {number} trackGainDb - Parent track gain, shared by stem audio and MIDI output.
 * @param {Object} drumVoiceGainsDb - Independent gain values for individual ADTOF drum lanes.
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
    drumVoiceGainsDb = {}
) {
    const scheduledNotesRef = useRef(new Set());
    const isDrumTrack = parsedMidiStems?.[trackName]?.isAdtofDrum === true;

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

    useEffect(() => {
        if (!isMidiMode) stopSynths(synthRef, isDrumTrack);
    }, [isMidiMode, synthRef, isDrumTrack]);

    // A scheduled one-shot cannot be selectively cancelled in smplr. Stop the
    // kit and rebuild the short scheduler window whenever a drum lane's M/S
    // state changes, so the console responds immediately and correctly.
    useEffect(() => {
        if (!isDrumTrack) return;
        scheduledNotesRef.current.clear();
        stopSynths(synthRef, true);
    }, [isDrumTrack, synthRef, drumMutedVoices, drumSoloedVoices]);

    // Cleanup memory when the editor is completely closed (component unmounts)
    useEffect(() => {
        return () => {
            stopSynths(synthRef, isDrumTrack);
        };
    }, [synthRef, isDrumTrack]);

    const prevProgressRef = useRef(progress);

    // 2. Playback Scheduler Loop
    useEffect(() => {
        const delta = progress - prevProgressRef.current;
        prevProgressRef.current = progress;
        
        // If the playhead jumped significantly (e.g. > 500ms), clear memory and stop ringing notes
        if (Math.abs(delta) > 0.5) {
            scheduledNotesRef.current.clear();
            stopSynths(synthRef, isDrumTrack);
        }

        if (!isMidiMode || !isPlaying || !synthRef || !parsedMidiStems || !parsedMidiStems[trackName] || !originalBpm) return;
        
        const hasSolos = Object.values(soloedTracks).some(val => val);
        const shouldBeMuted = hasSolos ? !soloedTracks[trackName] : !!mutedTracks[trackName];

        if (shouldBeMuted) {
            // Stop any currently playing notes if we just got muted/unsoloed while playing
            stopSynths(synthRef, isDrumTrack);
            return;
        }

        const trackData = parsedMidiStems[trackName];
        
        // The master timeline's time-stretching playback rate (e.g., 65 / 130 = 0.5x speed)
        const playbackRate = activeBpm / originalBpm; 

        // We look ahead a fixed amount in real-world time (500ms)
        const realWorldLookahead = 0.5;
        // Which corresponds to this much unstretched audio time:
        const unstretchedLookahead = realWorldLookahead * playbackRate;
        const transportLeadSeconds = Math.max(
            0,
            (transportStartTime || audioCtxRef.current.currentTime) - audioCtxRef.current.currentTime
        );
        
        getMidiNotes(trackData.midiData).forEach((note, index) => {
            // note.time and progress are both in absolute, UNSTRETCHED seconds
            if (note.time >= progress && note.time < progress + unstretchedLookahead) {
                // If velocity is <= 0.015, it acts as our custom "disabled" flag. Skip playing it.
                if (note.velocity !== undefined && note.velocity <= 0.015) return;

                if (!scheduledNotesRef.current.has(index)) {
                    // How much unstretched time until the note occurs?
                    const unstretchedDelay = note.time - progress;
                    
                    // Convert that delay into real-world time factoring in the playback rate
                    const realWorldDelay = unstretchedDelay / playbackRate;
                    
                    // Convert the note's original duration into real-world duration
                    const realWorldDuration = note.duration / playbackRate;

                    const drumVoice = isDrumTrack ? getAdtofDrumVoice(note.midi) : null;
                    // ADTOF pitch values identify drum classes, while smplr's
                    // The dedicated drum sampler accepts a sample name such as "kick".
                    // Never fall back to a piano pitch for an unknown drum note.
                    if (isDrumTrack && !drumVoice) return;
                    if (isDrumTrack && !isDrumVoiceAudible(
                        trackName,
                        drumVoice,
                        drumMutedVoices,
                        drumSoloedVoices
                    )) return;

                    const noteSynthRef = drumVoice
                        ? synthRef.get?.(drumVoice.id)
                        : synthRef;
                    if (!noteSynthRef?.current) return;

                    noteSynthRef.current.start({
                        note: drumVoice ? drumVoice.sample : note.midi,
                        velocity: drumVoice
                            ? getDrumPlaybackVelocity(note, drumVoice)
                            : getMelodicPlaybackVelocity(note, trackName),
                        time: audioCtxRef.current.currentTime + transportLeadSeconds + realWorldDelay,
                        duration: realWorldDuration
                    });
                    scheduledNotesRef.current.add(index);
                }
            }
        });
    }, [
        progress,
        isPlaying,
        isMidiMode,
        activeBpm,
        originalBpm,
        parsedMidiStems,
        trackName,
        audioCtxRef,
        synthRef,
        mutedTracks,
        soloedTracks,
        isDrumTrack,
        drumMutedVoices,
        drumSoloedVoices,
        transportStartTime,
        trackGainDb,
        drumVoiceGainsDb
    ]);

    // 3. Clear scheduled notes when pausing
    useEffect(() => {
        if (!isPlaying) {
            scheduledNotesRef.current.clear();
            stopSynths(synthRef, isDrumTrack);
        }
    }, [isPlaying, synthRef, isDrumTrack]);
}
