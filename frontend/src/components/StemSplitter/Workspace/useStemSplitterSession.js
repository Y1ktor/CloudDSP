/**
 * Coordinates workspace audio and MIDI engines, instrument lifecycles, track
 * controls, tempo, editor state, undo, and shortcuts.
 */
import React from 'react';
import { useAudioMultiTrackPlayer } from '../../../hooks/useAudioMultiTrackPlayer';
import { useInstruments } from '../../../hooks/useInstruments';
import { useMidiManager } from '../../../hooks/useMidiManager';
import { useGlobalShortcuts } from '../../../hooks/useGlobalShortcuts';
import { useUndoHistory } from '../../../hooks/useUndoHistory';
import { ADTOF_DRUM_VOICES, getDrumVoiceTrackId } from '../../../utils/DrumMidi';
import { getMidiStatusByTrack, getTracksToRender, getTimelineRows } from './projectTracks';

/**
 * Own the workspace session: audio/MIDI engines, lazy instruments, editing
 * mode, drum controls, tempo, undo, and shortcuts. A job change resets the
 * same session state as before; timing and file downloads have separate hooks.
 */
export function useStemSplitterSession({ file, stemUrls, sourceUrl, midiUrls, midiStates, jobId, jobTempo }) {
    const [showSigMenu, setShowSigMenu] = React.useState(false);
    const [selectedTrack, setSelectedTrack] = React.useState(null);
    const [editorOpenTrack, setEditorOpenTrack] = React.useState(null);
    const [activeMidiTracks, setActiveMidiTracks] = React.useState({});
    const [midiStateBeforeEditor, setMidiStateBeforeEditor] = React.useState({});
    const [expandedDrumTracks, setExpandedDrumTracks] = React.useState({});
    const [drumMutedVoices, setDrumMutedVoices] = React.useState({});
    const [drumSoloedVoices, setDrumSoloedVoices] = React.useState({});
    const [drumVoiceGainsDb, setDrumVoiceGainsDb] = React.useState({});

    const toggleDrumSubtracks = (trackName) => {
        setExpandedDrumTracks(prev => ({ ...prev, [trackName]: !prev[trackName] }));
    };

    const toggleDrumMute = (trackName, voiceId) => {
        const voiceTrackId = getDrumVoiceTrackId(trackName, voiceId);
        setDrumMutedVoices(prev => ({ ...prev, [voiceTrackId]: !prev[voiceTrackId] }));
    };

    const toggleDrumSolo = (trackName, voiceId) => {
        const voiceTrackId = getDrumVoiceTrackId(trackName, voiceId);
        setDrumSoloedVoices(prev => ({ ...prev, [voiceTrackId]: !prev[voiceTrackId] }));
    };

    const setDrumVoiceGainDb = (trackName, voiceId, decibels) => {
        const voiceTrackId = getDrumVoiceTrackId(trackName, voiceId);
        const numericValue = Number(decibels);
        setDrumVoiceGainsDb((previous) => ({
            ...previous,
            [voiceTrackId]: Number.isFinite(numericValue)
                ? Math.max(-12, Math.min(12, numericValue))
                : 0,
        }));
    };

    // 1. Instruments
    const {
        midiSynthRefs,
        drumVoiceSynthRefs,
        releaseInstrument,
        resetInstruments,
    } = useInstruments();

    // 2. Audio Player
    const audioEngine = useAudioMultiTrackPlayer(stemUrls, file, activeMidiTracks, sourceUrl, jobId);
    const {
        setBpm,
        setOriginalBpm,
    } = audioEngine;

    // 3. MIDI Manager
    const {
        parsedMidiStems,
        setParsedMidiStems,
        originalMidiStems,
        isMidiLoading,
        ensurePlaybackInstrument,
        releasePlaybackInstrument,
    } = useMidiManager(
        midiUrls, midiStates, jobId, audioEngine.timeSignature, audioEngine.audioCtxRef,
        midiSynthRefs, drumVoiceSynthRefs, releaseInstrument, resetInstruments
    );

    const setMidiMode = React.useCallback((trackName, enabled) => {
        if (!trackName) return;
        if (enabled) {
            // Instantiating sampled instruments is intentionally demand-driven:
            // merely receiving a MIDI file must not allocate its sample bank.
            if (!ensurePlaybackInstrument(trackName)) return;
        } else {
            releasePlaybackInstrument(trackName);
        }
        setActiveMidiTracks((previous) => ({ ...previous, [trackName]: enabled }));
    }, [ensurePlaybackInstrument, releasePlaybackInstrument]);

    const handleOpenEditor = React.useCallback((trackName) => {
        const wasMidiEnabled = Boolean(activeMidiTracks[trackName]);
        setMidiStateBeforeEditor((previous) => ({ ...previous, [trackName]: wasMidiEnabled }));
        setEditorOpenTrack(trackName);
        if (!wasMidiEnabled) setMidiMode(trackName, true);
    }, [activeMidiTracks, setMidiMode]);

    const handleCloseEditor = React.useCallback(() => {
        if (editorOpenTrack && !midiStateBeforeEditor[editorOpenTrack]) {
            setMidiMode(editorOpenTrack, false);
        }
        setEditorOpenTrack(null);
    }, [editorOpenTrack, midiStateBeforeEditor, setMidiMode]);

    const toggleMidiMode = React.useCallback((trackName) => {
        setMidiMode(trackName, !activeMidiTracks[trackName]);
    }, [activeMidiTracks, setMidiMode]);

    // Original audio has no generated MIDI. Restrict the global switch to the
    // tracks whose MIDI is fully parsed so it never mutes a stem that is still
    // waiting for an extraction result.
    const midiCapableTrackNames = React.useMemo(
        () => Object.keys(parsedMidiStems),
        [parsedMidiStems]
    );
    const isGlobalMidiEnabled = midiCapableTrackNames.length > 0
        && midiCapableTrackNames.every((trackName) => activeMidiTracks[trackName]);
    const toggleGlobalMidiMode = React.useCallback(() => {
        if (midiCapableTrackNames.length === 0) return;
        const shouldEnable = !isGlobalMidiEnabled;
        midiCapableTrackNames.forEach((trackName) => setMidiMode(trackName, shouldEnable));
    }, [isGlobalMidiEnabled, midiCapableTrackNames, setMidiMode]);

    const backendTempoBpm = Number(jobTempo?.bpm);
    const hasBackendTempo = Number.isFinite(backendTempoBpm) && backendTempoBpm > 0;
    // A 120 BPM fallback keeps timeline math stable but is not a measured tempo.
    // Do not present it as a BPM result until the backend has a real candidate.
    const hasDeterminedTempo = hasBackendTempo && jobTempo?.confidence !== 'unknown';
    const appliedTempoRef = React.useRef({ jobId: null, bpm: null });

    React.useEffect(() => {
        const previous = appliedTempoRef.current;
        if (previous.jobId === jobId && previous.bpm === (hasDeterminedTempo ? backendTempoBpm : null)) return;

        if (hasDeterminedTempo) {
            setOriginalBpm(backendTempoBpm);
            setBpm(backendTempoBpm);
        } else if (previous.jobId !== jobId) {
            setOriginalBpm(null);
            setBpm(120);
        }
        appliedTempoRef.current = { jobId, bpm: hasDeterminedTempo ? backendTempoBpm : null };
    }, [jobId, hasDeterminedTempo, backendTempoBpm, setBpm, setOriginalBpm]);

    React.useEffect(() => {
        // A restored job must not retain MIDI-mode toggles or an open editor
        // from the job that was previously displayed in this workspace.
        setActiveMidiTracks({});
        setMidiStateBeforeEditor({});
        setEditorOpenTrack(null);
        setSelectedTrack(null);
        setExpandedDrumTracks({});
        setDrumMutedVoices({});
        setDrumSoloedVoices({});
        setDrumVoiceGainsDb({});
    }, [jobId]);
    const midiStatusByTrack = React.useMemo(
        () => getMidiStatusByTrack(stemUrls, midiUrls, midiStates, parsedMidiStems),
        [stemUrls, midiUrls, midiStates, parsedMidiStems]
    );
    const pendingMidiTracks = Object.entries(midiStatusByTrack)
        .filter(([trackName, status]) => trackName !== 'Original' && ['processing', 'loading'].includes(status));
    const backendMidiProcessingCount = pendingMidiTracks.filter(([, status]) => status === 'processing').length;
    const midiDownloadCount = pendingMidiTracks.filter(([, status]) => status === 'loading').length;

    // 4. Undo History
    const { undoStacks, pushUndoState, handleUndoMidi, handleRevertMidi } = useUndoHistory(
        parsedMidiStems, setParsedMidiStems, originalMidiStems
    );

    // 5. Global Shortcuts
    useGlobalShortcuts({
        togglePlay: audioEngine.togglePlay,
        handleGoToBeginning: audioEngine.handleGoToBeginning,
        setIsCycling: audioEngine.setIsCycling,
        toggleSolo: audioEngine.toggleSolo,
        toggleMute: audioEngine.toggleMute,
        editorOpenTrack,
        selectedTrack: selectedTrack?.startsWith('drums:') ? 'drums' : selectedTrack,
        handleUndoMidi
    });

    const tracksToRender = React.useMemo(
        () => getTracksToRender(file, sourceUrl, stemUrls),
        [file, sourceUrl, stemUrls]
    );
    const timelineRows = React.useMemo(
        () => getTimelineRows(tracksToRender, parsedMidiStems, expandedDrumTracks, ADTOF_DRUM_VOICES),
        [tracksToRender, parsedMidiStems, expandedDrumTracks]
    );

    return {
        showSigMenu,
        setShowSigMenu,
        selectedTrack,
        setSelectedTrack,
        editorOpenTrack,
        activeMidiTracks,
        drumMutedVoices,
        drumSoloedVoices,
        drumVoiceGainsDb,
        toggleDrumSubtracks,
        toggleDrumMute,
        toggleDrumSolo,
        setDrumVoiceGainDb,
        midiSynthRefs,
        drumVoiceSynthRefs,
        audioEngine,
        parsedMidiStems,
        setParsedMidiStems,
        isMidiLoading,
        handleOpenEditor,
        handleCloseEditor,
        toggleMidiMode,
        midiCapableTrackNames,
        isGlobalMidiEnabled,
        toggleGlobalMidiMode,
        hasDeterminedTempo,
        midiStatusByTrack,
        backendMidiProcessingCount,
        midiDownloadCount,
        undoStacks,
        pushUndoState,
        handleUndoMidi,
        handleRevertMidi,
        tracksToRender,
        timelineRows,
    };
}
