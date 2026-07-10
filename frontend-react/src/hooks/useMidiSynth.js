import React, { useState, useRef, useEffect } from 'react';
import { SplendidGrandPiano } from 'smplr';

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
 * @param {number} activeBpm - The current BPM of the master timeline (used for time-stretching).
 * @param {React.MutableRefObject<Object>} globalSynthRef - Global reference to the initialized smplr instrument.
 * @param {boolean} isMidiMode - Whether MIDI synthesis is currently active for this track.
 * @returns {Object} An object containing an `auditionNote` trigger function.
 */
export function useMidiSynth(audioCtxRef, progress, isPlaying, parsedMidiStems, trackName, activeBpm, globalSynthRef, isMidiMode) {
    const scheduledNotesRef = useRef(new Set());

    // 1. Initialize instrument when MIDI mode is enabled (now just ensures it exists)
    useEffect(() => {
        if (isMidiMode) {
            // Context and synth are now initialized globally during MIDI fetch
            // But just in case:
            if (!audioCtxRef.current) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                audioCtxRef.current = new AudioContext();
            }
            if (!globalSynthRef.current) {
                globalSynthRef.current = new SplendidGrandPiano(audioCtxRef.current);
            }
        } else {
            if (globalSynthRef.current) {
                globalSynthRef.current.stop(); // Stop all ringing notes but keep the instance alive!
            }
        }
    }, [isMidiMode, audioCtxRef, globalSynthRef]);

    // Cleanup memory when the editor is completely closed (component unmounts)
    useEffect(() => {
        return () => {
            if (globalSynthRef.current) {
                globalSynthRef.current.stop();
                // We NO LONGER set it to null here, because it's a global ref owned by StemSplitter
                // which allows it to instantly load when other tracks are opened.
            }
        };
    }, []);

    // 2. Playback Scheduler Loop
    useEffect(() => {
        if (!isMidiMode || !isPlaying || !globalSynthRef.current || !parsedMidiStems || !parsedMidiStems[trackName]) return;
        
        const trackData = parsedMidiStems[trackName];
        const originalBpm = trackData.bpm || 120;
        const notes = trackData.midiData.tracks[0].notes;
        
        // 100ms look-ahead window
        const lookaheadWindow = 0.1; 
        const playbackRate = activeBpm / originalBpm;
        
        notes.forEach((note, index) => {
            // Calculate real-world time this note should play
            const scaledTime = note.time / playbackRate;
            
            // If the note falls within our tiny lookahead window and hasn't been scheduled yet
            if (scaledTime >= progress && scaledTime < progress + lookaheadWindow) {
                if (!scheduledNotesRef.current.has(index)) {
                    const scheduleDelay = scaledTime - progress;
                    globalSynthRef.current.start({
                        note: note.midi,
                        velocity: Math.round((note.velocity !== undefined ? note.velocity : 0.8) * 127),
                        time: audioCtxRef.current.currentTime + scheduleDelay,
                        duration: note.duration / playbackRate
                    });
                    scheduledNotesRef.current.add(index);
                }
            }
        });
    }, [progress, isPlaying, isMidiMode, activeBpm, parsedMidiStems, trackName, audioCtxRef]);

    // 3. Clear scheduled notes if we seek, pause, or switch tracks
    useEffect(() => {
        scheduledNotesRef.current.clear();
        if (globalSynthRef.current && !isPlaying) {
            globalSynthRef.current.stop(); // Stop immediately on pause
        }
    }, [progress < 0.1, isPlaying, trackName]); // Clear when seeking back to 0 or pausing

    /**
     * Instantly triggers a short 0.5s playback of a specific note.
     * Useful for auditioning notes when clicked in the UI.
     * 
     * @param {Object} note - The Tone.js Midi note object to audition
     */
    const auditionNote = (note) => {
        if (isMidiMode && globalSynthRef.current && audioCtxRef.current) {
            globalSynthRef.current.start({
                note: note.midi,
                velocity: Math.round((note.velocity !== undefined ? note.velocity : 0.8) * 127),
                time: audioCtxRef.current.currentTime,
                duration: 0.5 // Short audition
            });
        }
    };

    return {
        auditionNote
    };
}
