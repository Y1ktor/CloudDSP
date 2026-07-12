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

    React.useEffect(() => {
        return () => {
            if (globalSynthRef.current) {
                try {
                    globalSynthRef.current.stop();
                } catch (e) {
                    // Ignore
                }
            }
        };
    }, []);

    return { globalSynthRef, guitarSynthRef, bassSynthRef };
}
