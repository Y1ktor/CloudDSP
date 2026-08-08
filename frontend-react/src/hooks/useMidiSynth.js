import React, { useRef, useEffect } from 'react';
import { Sampler, SplendidGrandPiano } from 'smplr';
import {
    ADTOF_DRUM_SAMPLER_OPTIONS,
    getAdtofDrumVoice,
    getDrumPlaybackVelocity,
    getMidiNotes,
    isDrumVoiceAudible
} from '../utils/DrumMidi';

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
 * @param {React.MutableRefObject<Object>} synthRef - Global reference to the initialized smplr instrument.
 * @param {boolean} isMidiMode - Whether MIDI synthesis is currently active for this track.
 * @param {Object} mutedTracks - Dictionary of muted tracks.
 * @param {Object} soloedTracks - Dictionary of soloed audio tracks.
 * @param {Object} drumMutedVoices - Dictionary of muted ADTOF MIDI lanes.
 * @param {Object} drumSoloedVoices - Dictionary of soloed ADTOF MIDI lanes.
 * @param {number|null} transportStartTime - Scheduled AudioContext time for the shared stem transport.
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
    transportStartTime = null
) {
    const scheduledNotesRef = useRef(new Set());
    const isDrumTrack = parsedMidiStems?.[trackName]?.isAdtofDrum === true;

    // 1. Initialize instrument when MIDI mode is enabled (now just ensures it exists)
    useEffect(() => {
        if (isMidiMode) {
            // Context and synth are now initialized globally during MIDI fetch
            // But just in case:
            if (!audioCtxRef.current) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                audioCtxRef.current = new AudioContext();
            }
            if (!synthRef.current) {
                synthRef.current = isDrumTrack
                    ? Sampler(audioCtxRef.current, ADTOF_DRUM_SAMPLER_OPTIONS)
                    : new SplendidGrandPiano(audioCtxRef.current);
            }
        } else {
            if (synthRef.current) {
                synthRef.current.stop(); // Stop all ringing notes but keep the instance alive!
            }
        }
    }, [isMidiMode, audioCtxRef, synthRef, isDrumTrack]);

    // A scheduled one-shot cannot be selectively cancelled in smplr. Stop the
    // kit and rebuild the short scheduler window whenever a drum lane's M/S
    // state changes, so the console responds immediately and correctly.
    useEffect(() => {
        if (!isDrumTrack || !synthRef.current) return;
        scheduledNotesRef.current.clear();
        synthRef.current.stop();
    }, [isDrumTrack, synthRef, drumMutedVoices, drumSoloedVoices]);

    // Cleanup memory when the editor is completely closed (component unmounts)
    useEffect(() => {
        return () => {
            if (synthRef.current) {
                synthRef.current.stop();
                // We NO LONGER set it to null here, because it's a global ref owned by StemSplitter
                // which allows it to instantly load when other tracks are opened.
            }
        };
    }, [synthRef]);

    const prevProgressRef = useRef(progress);

    // 2. Playback Scheduler Loop
    useEffect(() => {
        const delta = progress - prevProgressRef.current;
        prevProgressRef.current = progress;
        
        // If the playhead jumped significantly (e.g. > 500ms), clear memory and stop ringing notes
        if (Math.abs(delta) > 0.5) {
            scheduledNotesRef.current.clear();
            if (synthRef.current) synthRef.current.stop();
        }

        if (!isMidiMode || !isPlaying || !synthRef.current || !parsedMidiStems || !parsedMidiStems[trackName] || !originalBpm) return;
        
        const hasSolos = Object.values(soloedTracks).some(val => val);
        const shouldBeMuted = hasSolos ? !soloedTracks[trackName] : !!mutedTracks[trackName];

        if (shouldBeMuted) {
            // Stop any currently playing notes if we just got muted/unsoloed while playing
            synthRef.current.stop();
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

                    synthRef.current.start({
                        note: drumVoice ? drumVoice.sample : note.midi,
                        velocity: drumVoice
                            ? getDrumPlaybackVelocity(note, drumVoice)
                            : Math.round((note.velocity !== undefined ? note.velocity : 0.8) * 127),
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
        transportStartTime
    ]);

    // 3. Clear scheduled notes when pausing
    useEffect(() => {
        if (!isPlaying) {
            scheduledNotesRef.current.clear();
            if (synthRef.current) {
                synthRef.current.stop(); // Stop immediately on pause
            }
        }
    }, [isPlaying, synthRef]);
}
