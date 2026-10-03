import React from 'react';
import { useMidiSynth } from '../../../hooks/useMidiSynth';

// The scheduler follows the shared transport ref; visual progress stays out of its update path.
const MidiScheduler = React.memo(function MidiScheduler({
    trackName,
    activeBpm,
    originalBpm,
    isPlaying,
    parsedMidiStems,
    audioCtxRef,
    transportRef,
    synthRef,
    isMidiMode,
    mutedTracks,
    soloedTracks,
    drumMutedVoices,
    drumSoloedVoices,
    trackGainDb,
    drumVoiceGainsDb
}) {
    useMidiSynth(
        audioCtxRef,
        0,
        isPlaying,
        parsedMidiStems,
        trackName,
        activeBpm,
        originalBpm,
        synthRef,
        isMidiMode,
        mutedTracks,
        soloedTracks,
        drumMutedVoices,
        drumSoloedVoices,
        null,
        trackGainDb,
        drumVoiceGainsDb,
        transportRef,
    );
    return null;
});

export default MidiScheduler;
