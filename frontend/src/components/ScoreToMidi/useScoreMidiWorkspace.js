/** MIDI-only workspace state for reviewing a score transcription result. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAudioMultiTrackPlayer } from '../../hooks/useAudioMultiTrackPlayer';
import { useGlobalShortcuts } from '../../hooks/useGlobalShortcuts';
import { useInstruments } from '../../hooks/useInstruments';
import { useMidiManager } from '../../hooks/useMidiManager';
import { useUndoHistory } from '../../hooks/useUndoHistory';
import { useWorkspaceTimeline } from '../StemSplitter/Workspace/useWorkspaceTimeline';

const TRACK_NAME = 'Score';

export function useScoreMidiWorkspace(midiFile, sessionKey) {
    const [selectedTrack, setSelectedTrack] = useState(null);
    const [editorOpenTrack, setEditorOpenTrack] = useState(null);
    const [activeMidiTracks, setActiveMidiTracks] = useState({});
    const initializedSessionRef = useRef(null);
    const midiSources = useMemo(() => midiFile ? { [TRACK_NAME]: midiFile } : null, [midiFile]);

    const { midiSynthRefs, drumVoiceSynthRefs, releaseInstrument, resetInstruments } = useInstruments();
    const audioEngine = useAudioMultiTrackPlayer({}, null, activeMidiTracks, null, sessionKey, true);
    const { setTimeSignature, setOriginalBpm, setBpm, setDuration } = audioEngine;
    const {
        parsedMidiStems, setParsedMidiStems, originalMidiStems,
        isMidiLoading, midiLoadErrors, playbackInstrumentStatus,
        ensurePlaybackInstrument, releasePlaybackInstrument,
    } = useMidiManager(
        midiSources, null, sessionKey, audioEngine.timeSignature,
        audioEngine.audioCtxRef, midiSynthRefs, drumVoiceSynthRefs,
        releaseInstrument, resetInstruments,
    );
    const scoreData = parsedMidiStems[TRACK_NAME];
    const { undoStacks, pushUndoState, handleUndoMidi, handleRevertMidi } = useUndoHistory(
        parsedMidiStems, setParsedMidiStems, originalMidiStems,
    );

    useEffect(() => {
        if (!scoreData || initializedSessionRef.current === sessionKey) return;
        initializedSessionRef.current = sessionKey;
        const header = scoreData.midiData?.header;
        const signature = header?.timeSignatures?.[0]?.timeSignature;
        if (Array.isArray(signature) && signature.length === 2) {
            setTimeSignature(`${signature[0]}/${signature[1]}`);
        }
        setOriginalBpm(scoreData.bpm);
        setBpm(scoreData.bpm);
        setDuration(scoreData.durationInSeconds);
        if (ensurePlaybackInstrument(TRACK_NAME)) {
            setActiveMidiTracks({ [TRACK_NAME]: true });
        }
    }, [ensurePlaybackInstrument, scoreData, sessionKey, setBpm, setDuration, setOriginalBpm, setTimeSignature]);

    useEffect(() => {
        if (!scoreData || initializedSessionRef.current !== sessionKey) return;
        setDuration(scoreData.midiData?.duration || scoreData.durationInSeconds);
    }, [scoreData, sessionKey, setDuration]);

    const timeline = useWorkspaceTimeline({ audioEngine, jobId: sessionKey, editorOpenTrack });
    const toggleMidiMode = useCallback(() => {
        if (activeMidiTracks[TRACK_NAME]) {
            releasePlaybackInstrument(TRACK_NAME);
            setActiveMidiTracks({ [TRACK_NAME]: false });
        } else if (ensurePlaybackInstrument(TRACK_NAME)) {
            setActiveMidiTracks({ [TRACK_NAME]: true });
        }
    }, [activeMidiTracks, ensurePlaybackInstrument, releasePlaybackInstrument]);
    const handleOpenEditor = useCallback(() => setEditorOpenTrack(TRACK_NAME), []);
    const handleCloseEditor = useCallback(() => setEditorOpenTrack(null), []);

    useGlobalShortcuts({
        togglePlay: audioEngine.togglePlay,
        handleGoToBeginning: audioEngine.handleGoToBeginning,
        setIsCycling: audioEngine.setIsCycling,
        toggleSolo: audioEngine.toggleSolo,
        toggleMute: audioEngine.toggleMute,
        editorOpenTrack,
        selectedTrack,
        handleUndoMidi,
    });

    const timelineRows = useMemo(() => scoreData ? [{
        id: TRACK_NAME, trackName: TRACK_NAME, url: null, kind: 'stem',
    }] : [], [scoreData]);
    const midiStatusByTrack = {
        [TRACK_NAME]: midiLoadErrors[TRACK_NAME]
            ? 'failed' : scoreData ? 'ready' : 'loading',
    };

    return {
        trackName: TRACK_NAME,
        audioEngine,
        timeline,
        parsedMidiStems,
        setParsedMidiStems,
        isMidiLoading,
        midiLoadError: midiLoadErrors[TRACK_NAME] || '',
        playbackInstrumentStatus: playbackInstrumentStatus[TRACK_NAME],
        scoreData,
        selectedTrack,
        setSelectedTrack,
        editorOpenTrack,
        handleOpenEditor,
        handleCloseEditor,
        activeMidiTracks,
        toggleMidiMode,
        timelineRows,
        midiStatusByTrack,
        midiSynthRefs,
        undoStacks,
        pushUndoState,
        handleUndoMidi,
        handleRevertMidi,
    };
}
