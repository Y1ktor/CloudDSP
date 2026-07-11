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
 * @param {number} activeBpm - The current user-adjusted BPM of the master timeline.
 * @param {number} originalBpm - The master original BPM determined by the project, used to calculate playbackRate.
 * @param {React.MutableRefObject<Object>} globalSynthRef - Global reference to the initialized smplr instrument.
 * @param {boolean} isMidiMode - Whether MIDI synthesis is currently active for this track.
 */
export function useMidiSynth(audioCtxRef, progress, isPlaying, parsedMidiStems, trackName, activeBpm, originalBpm, globalSynthRef, isMidiMode) {
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
        if (!isMidiMode || !isPlaying || !globalSynthRef.current || !parsedMidiStems || !parsedMidiStems[trackName] || !originalBpm) return;
        
        const trackData = parsedMidiStems[trackName];
        
        // The master timeline's time-stretching playback rate (e.g., 65 / 130 = 0.5x speed)
        const playbackRate = activeBpm / originalBpm; 

        // We look ahead a fixed amount in real-world time (500ms)
        const realWorldLookahead = 0.5;
        // Which corresponds to this much unstretched audio time:
        const unstretchedLookahead = realWorldLookahead * playbackRate;
        
        trackData.midiData.tracks[0].notes.forEach((note, index) => {
            // note.time and progress are both in absolute, UNSTRETCHED seconds
            if (note.time >= progress && note.time < progress + unstretchedLookahead) {
                if (!scheduledNotesRef.current.has(index)) {
                    // How much unstretched time until the note occurs?
                    const unstretchedDelay = note.time - progress;
                    
                    // Convert that delay into real-world time factoring in the playback rate
                    const realWorldDelay = unstretchedDelay / playbackRate;
                    
                    // Convert the note's original duration into real-world duration
                    const realWorldDuration = note.duration / playbackRate;

                    globalSynthRef.current.start({
                        note: note.midi,
                        velocity: Math.round((note.velocity !== undefined ? note.velocity : 0.8) * 127),
                        time: audioCtxRef.current.currentTime + realWorldDelay,
                        duration: realWorldDuration
                    });
                    scheduledNotesRef.current.add(index);
                }
            }
        });
    }, [progress, isPlaying, isMidiMode, activeBpm, originalBpm, parsedMidiStems, trackName, audioCtxRef]);

    // 3. Clear scheduled notes if we seek, pause, or switch tracks
    useEffect(() => {
        scheduledNotesRef.current.clear();
        if (globalSynthRef.current && !isPlaying) {
            globalSynthRef.current.stop(); // Stop immediately on pause
        }
    }, [progress < 0.1, isPlaying, trackName]); // Clear when seeking back to 0 or pausing
}
