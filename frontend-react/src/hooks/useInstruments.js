import React from 'react';

/**
 * useInstruments Hook
 * 
 * Initializes and manages the lifecycle of the Web Audio API synthesizers used 
 * for MIDI playback. Instantiates specific instrument plugins (like the `smplr` 
 * SplendidGrandPiano, acoustic_guitar_nylon, and acoustic_bass) into stable React refs 
 * so they persist across renders without causing memory leaks.
 * 
 * @returns {Object} Object containing the initialized synthesizer refs
 */
export function useInstruments() {
    const globalSynthRef = React.useRef(null);
    const guitarSynthRef = React.useRef(null);
    const bassSynthRef = React.useRef(null);
    const drumSynthRef = React.useRef(null);

    React.useEffect(() => {
        return () => {
            [globalSynthRef, guitarSynthRef, bassSynthRef, drumSynthRef].forEach((synthRef) => {
                if (!synthRef.current) return;
                try {
                    synthRef.current.stop();
                } catch {
                    // Ignore
                }
            });
        };
    }, [globalSynthRef, guitarSynthRef, bassSynthRef, drumSynthRef]);

    return { globalSynthRef, guitarSynthRef, bassSynthRef, drumSynthRef };
}
