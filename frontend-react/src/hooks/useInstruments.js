import React from 'react';

/**
 * useInstruments Hook
 * 
 * Owns the persistent smplr instances used for MIDI playback. Every melodic
 * stem receives its own synth ref, and every ADTOF drum voice receives its own
 * sampler ref. That routing keeps the console's per-track and per-drum-lane
 * gain controls independent.
 * 
 * @returns {Object} Object containing the initialized synthesizer refs
 */
export function useInstruments() {
    const midiSynthRefs = React.useRef(new Map());
    const drumVoiceSynthRefs = React.useRef(new Map());

    React.useEffect(() => {
        const melodicSynthRefs = midiSynthRefs.current;
        const drumSynthRefs = drumVoiceSynthRefs.current;
        return () => {
            const stopSynth = (synthRef) => {
                if (!synthRef?.current) return;
                try {
                    synthRef.current.stop();
                } catch {
                    // Ignore
                }
            };

            melodicSynthRefs.forEach(stopSynth);
            drumSynthRefs.forEach((voiceSynthRefs) => {
                voiceSynthRefs.forEach(stopSynth);
            });
        };
    }, []);

    return { midiSynthRefs, drumVoiceSynthRefs };
}
