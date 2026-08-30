import React from 'react';

function disposeSynth(synthRef) {
    const synth = synthRef?.current;
    if (!synth) return;

    try {
        // smplr instruments own scheduled callbacks, output nodes, and decoded
        // sample buffers. `stop()` alone leaves all of those resources alive.
        if (typeof synth.dispose === 'function') {
            synth.dispose();
        } else {
            synth.stop?.();
        }
    } catch {
        // A pending load or an already-disposed smplr instrument may reject a
        // second teardown. The owning map is still cleared below.
    }
    synthRef.current = null;
}

function disposeSynthMap(synthRefs) {
    synthRefs.forEach(disposeSynth);
    synthRefs.clear();
}

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

    const releaseInstrument = React.useCallback((trackName) => {
        const melodicRef = midiSynthRefs.current.get(trackName);
        if (melodicRef) {
            disposeSynth(melodicRef);
            midiSynthRefs.current.delete(trackName);
        }

        const drumRefs = drumVoiceSynthRefs.current.get(trackName);
        if (drumRefs) {
            disposeSynthMap(drumRefs);
            drumVoiceSynthRefs.current.delete(trackName);
        }
    }, []);

    const resetInstruments = React.useCallback(() => {
        disposeSynthMap(midiSynthRefs.current);
        drumVoiceSynthRefs.current.forEach(disposeSynthMap);
        drumVoiceSynthRefs.current.clear();
    }, []);

    React.useEffect(() => {
        return () => {
            resetInstruments();
        };
    }, [resetInstruments]);

    return {
        midiSynthRefs,
        drumVoiceSynthRefs,
        releaseInstrument,
        resetInstruments,
    };
}
