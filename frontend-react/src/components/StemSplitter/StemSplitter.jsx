import React from 'react';
import ControlBar from './ControlBar';
import TimelineRuler from './TimelineRuler';
import TrackList from './TrackList';
import TrackGrid from './TrackGrid';
import MidiEditorPopup from './MidiEditorPopup';

import { useAudioMultiTrackPlayer } from '../../hooks/AudioMultiTrackPlayer';
import { useMidiSynth } from '../../hooks/useMidiSynth';
import { useInstruments } from '../../hooks/useInstruments';
import { useMidiManager } from '../../hooks/useMidiManager';
import { useGlobalShortcuts } from '../../hooks/useGlobalShortcuts';
import { useUndoHistory } from '../../hooks/useUndoHistory';
import { usePlayheadScroll } from '../../hooks/usePlayheadScroll';
import { ADTOF_DRUM_VOICES, getDrumVoiceTrackId } from '../../utils/DrumMidi';

function MidiScheduler({
    trackName,
    activeBpm,
    originalBpm,
    progress,
    isPlaying,
    parsedMidiStems,
    audioCtxRef,
    synthRef,
    isMidiMode,
    mutedTracks,
    soloedTracks,
    drumMutedVoices,
    drumSoloedVoices,
    transportStartTime
}) {
    useMidiSynth(
        audioCtxRef,
        progress,
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
        transportStartTime
    );
    return null;
}

/**
 * StemSplitter Component (Orchestrator)
 * 
 * This UI component is the central orchestrator of the Stem Splitter workspace. It bridges 
 * the gap between the AWS backend connection (handled upstream in App.jsx), the audio transport 
 * (handled by `useAudioMultiTrackPlayer`), the MIDI conversion layer (`useMidiManager`), and the 
 * visual UI components.
 * 
 * ARCHITECTURE NOTE:
 * Following a strict "Smart/Dumb" pattern, this component has been refactored to contain almost 
 * zero business logic itself. All state management has been extracted into custom hooks. This 
 * file serves purely to assemble the layout and route data between the hooks and the UI components.
 * 
 * @param {Object} props - The hoisted state props provided by App.jsx
 * @param {File} props.file - The currently selected audio file
 * @param {Function} props.setFile - State setter for the file
 * @param {string} props.fileName - Display name of the file
 * @param {Function} props.setFileName - State setter for the filename
 * @param {string} props.splitMode - The selected Demucs mode (2, 4, or 6 stems)
 * @param {Function} props.setSplitMode - State setter for the mode
 * @param {boolean} props.isSplitting - Tracks if the active upload is being processed
 * @param {boolean} props.isRestoringHistoryJob - A saved job is being hydrated from private artifacts
 * @param {boolean} props.isHistoryJob - The workspace currently displays a saved job
 * @param {string} props.statusMessage - The dynamic loading text
 * @param {Object} props.stemUrls - Fresh presigned stem URLs from the durable job snapshot
 * @param {Object} props.midiUrls - Fresh presigned MIDI URLs from the durable job snapshot
 * @param {Object} props.midiStates - Per-stem durable MIDI extraction states
 * @param {Object} props.jobTempo - Backend-selected master tempo for the active job
 * @param {string} props.jobId - Durable job ID used to reset tempo between uploads
 * @param {string} props.sourceUrl - Presigned URL for the saved job's original upload
 * @param {string} props.errorMsg - Any error messages to display
 * @param {Function} props.setErrorMsg - State setter for errors
 * @param {Function} props.executeStemSplit - Master function in App.jsx to create and upload a job
 */
export default function StemSplitter({
    file, setFile,
    fileName, setFileName,
    splitMode, setSplitMode,
    isSplitting, isRestoringHistoryJob, isHistoryJob, statusMessage, stemUrls, midiUrls, midiStates, jobTempo, jobId, errorMsg, setErrorMsg,
    sourceUrl,
    executeStemSplit, executeLinkExtraction, beginNewUpload
}) {
    const [showSigMenu, setShowSigMenu] = React.useState(false);
    const [selectedTrack, setSelectedTrack] = React.useState(null);
    const [editorOpenTrack, setEditorOpenTrack] = React.useState(null);
    const [activeMidiTracks, setActiveMidiTracks] = React.useState({});
    const [midiStateBeforeEditor, setMidiStateBeforeEditor] = React.useState({});
    const [expandedDrumTracks, setExpandedDrumTracks] = React.useState({});
    const [drumMutedVoices, setDrumMutedVoices] = React.useState({});
    const [drumSoloedVoices, setDrumSoloedVoices] = React.useState({});

    const handleOpenEditor = (trackName) => {
        setMidiStateBeforeEditor(prev => ({ ...prev, [trackName]: !!activeMidiTracks[trackName] }));
        setEditorOpenTrack(trackName);
        if (!activeMidiTracks[trackName]) {
            setActiveMidiTracks(prev => ({ ...prev, [trackName]: true }));
        }
    };

    const handleCloseEditor = () => {
        if (editorOpenTrack) {
            if (!midiStateBeforeEditor[editorOpenTrack]) {
                setActiveMidiTracks(prev => ({ ...prev, [editorOpenTrack]: false }));
            }
        }
        setEditorOpenTrack(null);
    };

    const toggleMidiMode = (trackName) => {
        setActiveMidiTracks(prev => ({ ...prev, [trackName]: !prev[trackName] }));
    };

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

    // 1. Instruments
    const { globalSynthRef, guitarSynthRef, bassSynthRef, drumSynthRef } = useInstruments();

    // 2. Audio Player
    const audioEngine = useAudioMultiTrackPlayer(stemUrls, file, activeMidiTracks, sourceUrl, jobId);
    const { setBpm, setOriginalBpm } = audioEngine;

    // 3. MIDI Manager
    const { parsedMidiStems, setParsedMidiStems, originalMidiStems, isMidiLoading } = useMidiManager(
        midiUrls, midiStates, jobId, audioEngine.timeSignature, audioEngine.audioCtxRef,
        globalSynthRef, guitarSynthRef, bassSynthRef, drumSynthRef
    );

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
    }, [jobId]);
    const midiStatusByTrack = React.useMemo(() => {
        return Object.keys(stemUrls || {}).reduce((statuses, trackName) => {
            if (parsedMidiStems[trackName]) {
                statuses[trackName] = 'ready';
            } else if (midiStates?.[trackName]?.status === 'failed') {
                statuses[trackName] = 'failed';
            } else if (midiUrls?.[trackName]) {
                statuses[trackName] = 'loading';
            } else if (trackName === 'Original') {
                statuses[trackName] = 'unavailable';
            } else {
                statuses[trackName] = 'processing';
            }
            return statuses;
        }, {});
    }, [stemUrls, midiUrls, midiStates, parsedMidiStems]);
    const pendingMidiTracks = Object.entries(midiStatusByTrack)
        .filter(([trackName, status]) => trackName !== 'Original' && ['processing', 'loading'].includes(status));
    const backendMidiProcessingCount = pendingMidiTracks.filter(([, status]) => status === 'processing').length;
    const midiDownloadCount = pendingMidiTracks.filter(([, status]) => status === 'loading').length;
    const showActivityNotice = isSplitting || isRestoringHistoryJob;
    const activityMessage = isRestoringHistoryJob ? 'Stems and MIDI will arrive shortly.' : statusMessage;

    const renderActivityNotice = () => showActivityNotice && (
        <div style={{
            display: 'flex', alignItems: 'center', gap: '9px',
            background: 'rgba(76, 175, 80, 0.13)', color: '#d7f3dc',
            border: '1px solid rgba(76, 175, 80, 0.42)', borderRadius: '4px',
            padding: '9px 12px', fontSize: '13px', fontWeight: '600'
        }}>
            <span aria-hidden="true" style={{
                width: '10px', height: '10px', border: '2px solid rgba(215, 243, 220, 0.35)',
                borderTopColor: '#d7f3dc', borderRadius: '50%', animation: 'spin 1s linear infinite'
            }} />
            {activityMessage}
            {isSplitting && !hasDeterminedTempo && <span style={{ color: '#a9d8b0', fontWeight: '500' }}>BPM pending</span>}
        </div>
    );

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

    React.useEffect(() => {
        return () => {
            if (globalSynthRef.current) {
                try { globalSynthRef.current.stop(); } catch (e) { }
            }
        };
    }, [globalSynthRef]);

    const handleFileUpload = (e) => {
        const uploadedFile = e.target.files[0];
        if (uploadedFile) {
            setFile(uploadedFile);
            setFileName(uploadedFile.name);
            beginNewUpload();
            setErrorMsg("");
        }
    };

    const tracksToRender = React.useMemo(() => {
        const tr = {};
        if (file) tr.Original = null;
        else if (sourceUrl) tr['Original'] = sourceUrl;
        // Retain this fallback for incomplete legacy jobs created before the
        // Job API returned original uploads. New and restored jobs use the
        // real source audio above, never a duplicate stem.
        else if (!file && stemUrls && Object.keys(stemUrls).length > 0) tr['Original'] = stemUrls[Object.keys(stemUrls)[0]];
        if (stemUrls) Object.assign(tr, stemUrls);
        return tr;
    }, [file, sourceUrl, stemUrls]);

    // ADTOF emits a single drum MIDI file, but its five fixed note classes
    // represent distinct instruments. The audio stays on the parent row and
    // its child MIDI lanes are only rendered after the user expands Drums.
    const timelineRows = React.useMemo(() => (
        Object.entries(tracksToRender).flatMap(([trackName, url]) => {
            const hasDrumSubtracks = trackName === 'drums' && parsedMidiStems[trackName]?.isAdtofDrum;
            const isDrumExpanded = hasDrumSubtracks && Boolean(expandedDrumTracks[trackName]);
            const stemRow = {
                id: trackName,
                trackName,
                url,
                kind: 'stem',
                hasDrumSubtracks,
                isDrumExpanded
            };
            if (!hasDrumSubtracks || !isDrumExpanded) {
                return [stemRow];
            }

            return [
                stemRow,
                ...ADTOF_DRUM_VOICES.map((drumVoice) => ({
                    id: `drums:${drumVoice.id}`,
                    trackName: 'drums',
                    kind: 'drum-lane',
                    drumVoice
                }))
            ];
        })
    ), [tracksToRender, parsedMidiStems, expandedDrumTracks]);

    const [pixelsPerBar, setPixelsPerBar] = React.useState(100);
    const parsedBeatsPerBar = parseInt(audioEngine.timeSignature.split('/')[0], 10) || 4;
    const activeBpm = audioEngine.bpm;
    const totalBars = audioEngine.duration > 0 ? Math.ceil((audioEngine.duration * (activeBpm / 60)) / parsedBeatsPerBar) : 20;

    const dynamicDuration = audioEngine.originalBpm && audioEngine.duration ? audioEngine.duration * (audioEngine.originalBpm / audioEngine.bpm) : audioEngine.duration;
    const dynamicProgress = audioEngine.originalBpm && audioEngine.progress ? audioEngine.progress * (audioEngine.originalBpm / audioEngine.bpm) : audioEngine.progress;
    const playheadX = Math.round((audioEngine.progress * (activeBpm / 60) / parsedBeatsPerBar) * pixelsPerBar);

    const [isPlayheadHovered, setIsPlayheadHovered] = React.useState(false);
    const playheadDragRef = React.useRef({ isDragging: false });
    const cycleDragRef = React.useRef({ isDragging: false, mode: 'move', initialX: 0, initialStart: 0, initialEnd: 0 });
    const timelineRef = React.useRef(null);
    const scrollContainerRef = React.useRef(null);

    React.useEffect(() => {
        const handleMouseMove = (e) => {
            if (playheadDragRef.current.isDragging) {
                const activeTimeline = playheadDragRef.current.timelineRef?.current || timelineRef.current;
                const activePixels = playheadDragRef.current.pixelsPerBar || pixelsPerBar;
                
                if (activeTimeline) {
                    const rect = activeTimeline.getBoundingClientRect();
                    const xOffset = e.clientX - rect.left;
                    
                    let newBar = xOffset / activePixels;
                    newBar = Math.max(0, Math.min(newBar, totalBars));
                    
                    const newProgress = (newBar * parsedBeatsPerBar) / (activeBpm / 60);
                    audioEngine.handleSeek({ target: { value: newProgress } });
                }
            } else if (cycleDragRef.current.isDragging) {
                const mode = cycleDragRef.current.mode;
                const activePixels = cycleDragRef.current.pixelsPerBar || pixelsPerBar;
                const deltaX = e.clientX - cycleDragRef.current.initialX;
                const deltaBars = deltaX / activePixels;
                const snappedDeltaBars = Math.round(deltaBars * parsedBeatsPerBar) / parsedBeatsPerBar;
                
                if (mode === 'move') {
                    let newStart = cycleDragRef.current.initialStart + snappedDeltaBars;
                    let newEnd = cycleDragRef.current.initialEnd + snappedDeltaBars;
                    const span = cycleDragRef.current.initialEnd - cycleDragRef.current.initialStart;
                    
                    if (newStart < 0) {
                        newStart = 0;
                        newEnd = span;
                    } else if (newEnd > totalBars) {
                        newEnd = totalBars;
                        newStart = totalBars - span;
                    }
                    audioEngine.setCycleRegion({ startBar: newStart, endBar: newEnd });
                } else if (mode === 'resize-left') {
                    let newStart = cycleDragRef.current.initialStart + snappedDeltaBars;
                    const minimumSpan = 1 / parsedBeatsPerBar;
                    if (newStart < 0) newStart = 0;
                    if (newStart > cycleDragRef.current.initialEnd - minimumSpan) {
                        newStart = cycleDragRef.current.initialEnd - minimumSpan;
                    }
                    audioEngine.setCycleRegion({ startBar: newStart, endBar: cycleDragRef.current.initialEnd });
                } else if (mode === 'resize-right') {
                    let newEnd = cycleDragRef.current.initialEnd + snappedDeltaBars;
                    const minimumSpan = 1 / parsedBeatsPerBar;
                    if (newEnd > totalBars) newEnd = totalBars;
                    if (newEnd < cycleDragRef.current.initialStart + minimumSpan) {
                        newEnd = cycleDragRef.current.initialStart + minimumSpan;
                    }
                    audioEngine.setCycleRegion({ startBar: cycleDragRef.current.initialStart, endBar: newEnd });
                }
            }
        };

        const handleMouseUp = () => {
            if (playheadDragRef.current.isDragging) {
                playheadDragRef.current.isDragging = false;
                document.body.style.cursor = '';
                setIsPlayheadHovered(false);
            }
            if (cycleDragRef.current.isDragging) {
                cycleDragRef.current.isDragging = false;
                document.body.style.cursor = '';
            }
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, [activeBpm, pixelsPerBar, totalBars, parsedBeatsPerBar, audioEngine.handleSeek, audioEngine.setCycleRegion]);

    usePlayheadScroll(scrollContainerRef, playheadX, audioEngine.isPlaying);

    return (
        <div style={{
            background: '#333',
            color: 'white',
            padding: '20px',
            borderRadius: '5px',
            width: '95vw',
            maxWidth: '1400px',
            margin: '0 auto 40px auto',
            boxSizing: 'border-box',
            boxShadow: '0 8px 16px rgba(0,0,0,0.5)',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px'
        }}>
            <h2 style={{ margin: 0, fontSize: '18px', borderBottom: '1px solid #555', paddingBottom: '10px' }}>
                Stem Splitting & Audio-to-MIDI
            </h2>
            
            <ControlBar 
                isSplitting={isSplitting}
                handleFileUpload={handleFileUpload}
                fileName={fileName}
                splitMode={splitMode}
                setSplitMode={setSplitMode}
                executeStemSplit={executeStemSplit}
                executeLinkExtraction={executeLinkExtraction}
                file={file}
                errorMsg={errorMsg}
            />

            {/* Dynamic Results Area */}
            <div style={{
                background: '#222',
                borderRadius: '4px',
                padding: '20px',
                minHeight: '200px',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: Object.keys(tracksToRender).length ? 'flex-start' : 'center',
                alignItems: Object.keys(tracksToRender).length ? 'stretch' : 'center',
                color: '#777',
                border: '1px dashed #444',
                gap: '15px'
            }}>
                {Object.keys(tracksToRender).length ? (
                    <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '3px' }}>
                        {renderActivityNotice()}
                        {!audioEngine.isAudioReady && (
                            <div style={{
                                display: 'flex', alignItems: 'center', gap: '9px',
                                background: 'rgba(85, 137, 198, 0.13)', color: '#bdd8f7',
                                border: '1px solid rgba(85, 137, 198, 0.42)', borderRadius: '4px',
                                padding: '9px 12px', fontSize: '13px', fontWeight: '600'
                            }}>
                                <span aria-hidden="true" style={{
                                    width: '10px', height: '10px', border: '2px solid rgba(189, 216, 247, 0.35)',
                                    borderTopColor: '#bdd8f7', borderRadius: '50%', animation: 'spin 1s linear infinite'
                                }} />
                                Preparing synchronized audio buffers. Playback will be available when every displayed track is ready.
                            </div>
                        )}
                        {backendMidiProcessingCount > 0 && isSplitting && (
                            <div style={{
                                display: 'flex', alignItems: 'center', gap: '9px',
                                background: 'rgba(224, 168, 0, 0.13)', color: '#f5c451',
                                border: '1px solid rgba(224, 168, 0, 0.4)', borderRadius: '4px',
                                padding: '9px 12px', fontSize: '13px', fontWeight: '600'
                            }}>
                                <span aria-hidden="true" style={{ color: '#f5c451', fontSize: '16px' }}>●</span>
                                MIDI extraction is still processing for {backendMidiProcessingCount} stem{backendMidiProcessingCount === 1 ? '' : 's'}. Tracks will populate as each result arrives.
                            </div>
                        )}
                        {midiDownloadCount > 0 && (
                            <div style={{
                                display: 'flex', alignItems: 'center', gap: '9px',
                                background: 'rgba(85, 137, 198, 0.13)', color: '#bdd8f7',
                                border: '1px solid rgba(85, 137, 198, 0.42)', borderRadius: '4px',
                                padding: '9px 12px', fontSize: '13px', fontWeight: '600'
                            }}>
                                <span aria-hidden="true" style={{ color: '#bdd8f7', fontSize: '16px' }}>●</span>
                                {isHistoryJob
                                    ? `Saved-job MIDI is downloading for ${midiDownloadCount} stem${midiDownloadCount === 1 ? '' : 's'}. Stems and MIDI will arrive shortly.`
                                    : `Generated MIDI is downloading for ${midiDownloadCount} stem${midiDownloadCount === 1 ? '' : 's'}. Tracks will populate as each file arrives.`}
                            </div>
                        )}
                        {/* Central Master Audio Control */}
                        <div style={{ 
                            background: '#333', padding: '15px 20px', borderRadius: '4px', 
                            display: 'flex', alignItems: 'center', gap: '20px'
                        }}>
                            <button title="Go to Beginning" onClick={audioEngine.handleGoToBeginning} style={{
                                background: 'transparent', color: 'white', border: 'none',
                                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                            }}>
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
                            </button>

                            <button title="Play/Pause" onClick={audioEngine.togglePlay} style={{
                                background: 'transparent', color: 'white', border: 'none',
                                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                            }}>
                                {audioEngine.isPlaying ? (
                                    <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                                ) : (
                                    <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                                )}
                            </button>

                            <button title="Toggle Cycle" onClick={() => audioEngine.setIsCycling(!audioEngine.isCycling)} style={{
                                background: audioEngine.isCycling ? '#8B6508' : 'transparent', 
                                color: audioEngine.isCycling ? '#fff' : 'white', 
                                border: 'none', borderRadius: '4px',
                                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '2px', 
                                opacity: 0.8,
                                transition: 'background-color 0.2s'
                            }}>
                                <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
                            </button>
                            
                            <div className="time-display" style={{ color: '#fff', fontSize: '14px', fontFamily: 'monospace', marginLeft: '10px', whiteSpace: 'nowrap' }}>
                                {isMidiLoading ? 
                                    `${audioEngine.formatTime(audioEngine.progress)} / ${audioEngine.formatTime(audioEngine.duration)}` : 
                                    `${audioEngine.formatTime(dynamicProgress)} / ${audioEngine.formatTime(dynamicDuration)}`
                                }
                            </div>

                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: '30px' }}>
                                <span className="bpm-label" style={{ color: '#fff', fontSize: '14px', fontWeight: 'bold' }}>BPM:</span>
                                <div style={{ 
                                    background: 'linear-gradient(180deg, #2A3644 0%, #1B232D 100%)',
                                    color: hasDeterminedTempo ? '#e2e8f0' : '#777',
                                    fontSize: '14px', fontFamily: 'monospace', fontWeight: 'bold',
                                    padding: '4px 8px', borderRadius: '4px', width: '55px', textAlign: 'center',
                                    border: '1px solid #0a0d12',
                                    borderTop: '1px solid #485c70',
                                    boxShadow: 'inset 0 2px 5px rgba(0,0,0,0.6), 0 1px 1px rgba(255,255,255,0.05)',
                                    textShadow: hasDeterminedTempo ? '0 0 6px rgba(226, 232, 240, 0.4)' : 'none',
                                    display: 'flex', justifyContent: 'center', userSelect: 'none'
                                }}>
                                    {hasDeterminedTempo ? (
                                        <>
                                            <span 
                                                onMouseDown={(e) => audioEngine.handleBpmMouseDown(e, 'int')}
                                                style={{ cursor: 'ns-resize', flexGrow: 1, textAlign: 'right' }}
                                            >{Math.floor(audioEngine.bpm)}</span>
                                            <span style={{ cursor: 'default' }}>.</span>
                                            <span 
                                                onMouseDown={(e) => audioEngine.handleBpmMouseDown(e, 'dec')}
                                                style={{ cursor: 'ns-resize', flexGrow: 1, textAlign: 'left' }}
                                            >{Math.round((audioEngine.bpm - Math.floor(audioEngine.bpm)) * 10)}</span>
                                        </>
                                    ) : (
                                        <span style={{ cursor: 'default' }}>---</span>
                                    )}
                                </div>
                            </div>
                            
                            <div style={{ flexGrow: 0.15, minWidth: '15px', maxWidth: '60px' }} className="dynamic-spacer-1" />
                            
                            {/* Time Signature Box */}
                            <div className="time-signature" style={{ position: 'relative' }}>
                                <div 
                                    onClick={() => setShowSigMenu(!showSigMenu)}
                                    style={{ 
                                        background: 'linear-gradient(180deg, #2A3644 0%, #1B232D 100%)',
                                        color: '#e2e8f0', 
                                        fontSize: '14px', fontFamily: 'monospace', fontWeight: 'bold',
                                        padding: '4px 8px', borderRadius: '4px', minWidth: '35px', textAlign: 'center',
                                        border: '1px solid #0a0d12',
                                        borderTop: '1px solid #485c70',
                                        boxShadow: 'inset 0 2px 5px rgba(0,0,0,0.6), 0 1px 1px rgba(255,255,255,0.05)',
                                        textShadow: '0 0 6px rgba(226, 232, 240, 0.4)',
                                        cursor: 'pointer',
                                        userSelect: 'none'
                                    }}
                                >
                                    {audioEngine.timeSignature}
                                </div>

                                {showSigMenu && (
                                    <>
                                        <div 
                                            style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, zIndex: 99 }} 
                                            onClick={() => setShowSigMenu(false)}
                                        />
                                        <div style={{ 
                                            position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)', 
                                            marginTop: '5px', background: '#1B232D', border: '1px solid #485c70', 
                                            borderRadius: '4px', zIndex: 100, display: 'flex', flexDirection: 'column',
                                            boxShadow: '0 4px 12px rgba(0,0,0,0.5)', overflow: 'hidden'
                                        }}>
                                            {['3/4', '4/4', '5/4', '6/8', '7/8'].map(sig => (
                                                <div 
                                                    key={sig}
                                                    onClick={() => { audioEngine.setTimeSignature(sig); setShowSigMenu(false); }}
                                                    onMouseEnter={(e) => e.target.style.background = '#2A3644'}
                                                    onMouseLeave={(e) => e.target.style.background = 'transparent'}
                                                    style={{ 
                                                        padding: '6px 12px', color: '#fff', fontSize: '14px', fontFamily: 'monospace',
                                                        cursor: 'pointer', textAlign: 'center', transition: 'background 0.1s'
                                                    }}
                                                >
                                                    {sig}
                                                </div>
                                            ))}
                                        </div>
                                    </>
                                )}
                            </div>

                            <div style={{ flexGrow: 1 }} /> {/* Pushes download button to the right */}

                            <button style={{
                                background: '#444', color: '#ccc', border: '1px solid #555', 
                                padding: '6px 14px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px',
                                fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '6px',
                                boxShadow: '0 2px 4px rgba(0,0,0,0.2)'
                            }}>
                                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                                    <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
                                </svg>
                                <span className="download-text">Download</span>
                            </button>
                            <style>{`
                                @media (max-width: 850px) {
                                    .time-display { display: none !important; }
                                    .download-text { display: none !important; }
                                }
                                @media (max-width: 750px){
                                    .time-signature { display: none !important; }
                                }
                                @media (max-width: 600px) {
                                    .bpm-label { display: none !important; }
                                    
                                }
                            `}</style>
                        </div>

                        {/* Split Workspace: Fixed Left Column + Scrollable Right Column */}
                        <div style={{ width: '100%', display: 'flex', gap: '3px', paddingBottom: '10px' }}>
                            
                            {/* LEFT COLUMN: Track Consoles (Fixed) */}
                            <TrackList 
                                pixelsPerBar={pixelsPerBar}
                                setPixelsPerBar={setPixelsPerBar}
                                timelineRows={timelineRows}
                                toggleMute={audioEngine.toggleMute}
                                mutedTracks={audioEngine.mutedTracks}
                                toggleSolo={audioEngine.toggleSolo}
                                soloedTracks={audioEngine.soloedTracks}
                                selectedTrack={selectedTrack}
                                setSelectedTrack={setSelectedTrack}
                                onDoubleClickTrack={handleOpenEditor}
                                activeMidiTracks={activeMidiTracks}
                                toggleMidiMode={toggleMidiMode}
                                toggleDrumSubtracks={toggleDrumSubtracks}
                                drumMutedVoices={drumMutedVoices}
                                drumSoloedVoices={drumSoloedVoices}
                                toggleDrumMute={toggleDrumMute}
                                toggleDrumSolo={toggleDrumSolo}
                            />
                            
                            {/* RIGHT COLUMN: Timeline Canvas (Scrollable) */}
                            <div ref={scrollContainerRef} style={{ flexGrow: 1, overflowX: 'auto', paddingBottom: '10px', scrollBehavior: 'auto' }}>
                                <div ref={timelineRef} style={{ minWidth: `${pixelsPerBar * totalBars}px`, display: 'flex', flexDirection: 'column', gap: '3px', position: 'relative' }}>
                                    
                                    {/* Time Indicator (Playhead) */}
                                    {audioEngine.duration > 0 && (
                                        <div style={{
                                            position: 'absolute',
                                            left: `${playheadX}px`,
                                            transform: 'translateX(-50%)',
                                            top: '15px',
                                            bottom: 0,
                                            width: '1px',
                                            backgroundColor: '#fff',
                                            zIndex: 10,
                                            pointerEvents: 'none',
                                            boxShadow: '0 0 4px rgba(255, 255, 255, 0.5)'
                                        }} />
                                    )}

                                    {/* Timeline Header Right (Time Bar) */}
                                    <TimelineRuler 
                                        duration={audioEngine.duration}
                                        pixelsPerBar={pixelsPerBar}
                                        cycleDragRef={cycleDragRef}
                                        cycleRegion={audioEngine.cycleRegion}
                                        isCycling={audioEngine.isCycling}
                                        totalBars={totalBars}
                                        timeSignature={audioEngine.timeSignature}
                                        timelineRef={timelineRef}
                                        playheadDragRef={playheadDragRef}
                                        setIsPlayheadHovered={setIsPlayheadHovered}
                                        isPlayheadHovered={isPlayheadHovered}
                                        playheadX={playheadX}
                                        activeBpm={activeBpm}
                                        parsedBeatsPerBar={parsedBeatsPerBar}
                                        handleSeek={audioEngine.handleSeek}
                                    />
                                    
                                    {/* Track Contents */}
                                    <TrackGrid 
                                        timelineRows={timelineRows}
                                        parsedMidiStems={parsedMidiStems}
                                        midiStatusByTrack={midiStatusByTrack}
                                        pixelsPerBar={pixelsPerBar}
                                        activeBpm={activeBpm}
                                        parsedBeatsPerBar={parsedBeatsPerBar}
                                        selectedTrack={selectedTrack}
                                        setSelectedTrack={setSelectedTrack}
                                        onDoubleClickTrack={handleOpenEditor}
                                    />
                                </div>
                            </div>
                        </div>
                </div>
                ) : (
                    renderActivityNotice() || <div>Stem extraction and MIDI results will appear here as downloadable multitracks</div>
                )}
                <style>{`
                    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                `}</style>
            </div>

            {/* Background MIDI Schedulers */}
            {Object.keys(parsedMidiStems).map(trackName => {
                let synthRefToUse = globalSynthRef;
                if (trackName === 'guitar') synthRefToUse = guitarSynthRef;
                if (trackName === 'bass') synthRefToUse = bassSynthRef;
                if (parsedMidiStems[trackName]?.isAdtofDrum) synthRefToUse = drumSynthRef;

                return (
                    <MidiScheduler
                        key={`midi-synth-${trackName}`}
                        trackName={trackName}
                        activeBpm={activeBpm}
                        originalBpm={audioEngine.originalBpm}
                        progress={audioEngine.progress}
                        isPlaying={audioEngine.isPlaying}
                        parsedMidiStems={parsedMidiStems}
                        audioCtxRef={audioEngine.audioCtxRef}
                        synthRef={synthRefToUse}
                        isMidiMode={!!activeMidiTracks[trackName]}
                        mutedTracks={audioEngine.mutedTracks}
                        soloedTracks={audioEngine.soloedTracks}
                        drumMutedVoices={drumMutedVoices}
                        drumSoloedVoices={drumSoloedVoices}
                        transportStartTime={audioEngine.transportStartTime}
                    />
                );
            })}

            {/* MIDI Editor Pop-up Window */}
            <MidiEditorPopup 
                trackName={editorOpenTrack} 
                onClose={handleCloseEditor} 
                duration={audioEngine.duration}
                pixelsPerBar={pixelsPerBar}
                totalBars={totalBars}
                playheadX={playheadX}
                cycleDragRef={cycleDragRef}
                cycleRegion={audioEngine.cycleRegion}
                isCycling={audioEngine.isCycling}
                timeSignature={audioEngine.timeSignature}
                playheadDragRef={playheadDragRef}
                setIsPlayheadHovered={setIsPlayheadHovered}
                isPlayheadHovered={isPlayheadHovered}
                handleGoToBeginning={audioEngine.handleGoToBeginning}
                isPlaying={audioEngine.isPlaying}
                togglePlay={audioEngine.togglePlay}
                toggleCycling={() => audioEngine.setIsCycling(!audioEngine.isCycling)}
                mutedTracks={audioEngine.mutedTracks}
                soloedTracks={audioEngine.soloedTracks}
                toggleMute={audioEngine.toggleMute}
                toggleSolo={audioEngine.toggleSolo}
                drumMutedVoices={drumMutedVoices}
                drumSoloedVoices={drumSoloedVoices}
                toggleDrumMute={toggleDrumMute}
                toggleDrumSolo={toggleDrumSolo}
                activeBpm={activeBpm}
                originalBpm={audioEngine.originalBpm}
                parsedBeatsPerBar={parsedBeatsPerBar}
                handleSeek={audioEngine.handleSeek}
                parsedMidiStems={parsedMidiStems}
                midiStatus={midiStatusByTrack[editorOpenTrack]}
                setParsedMidiStems={setParsedMidiStems}
                audioCtxRef={audioEngine.audioCtxRef}
                progress={audioEngine.progress}
                fileName={fileName}
                synthRef={
                    editorOpenTrack === 'guitar' ? guitarSynthRef :
                    editorOpenTrack === 'bass' ? bassSynthRef :
                    parsedMidiStems[editorOpenTrack]?.isAdtofDrum ? drumSynthRef :
                    globalSynthRef
                }
                isMidiMode={!!activeMidiTracks[editorOpenTrack]}
                setIsMidiMode={() => toggleMidiMode(editorOpenTrack)}
                handleRevertMidi={() => handleRevertMidi(editorOpenTrack)}
                handleUndoMidi={() => handleUndoMidi(editorOpenTrack)}
                pushUndoState={() => pushUndoState(editorOpenTrack)}
                undoStackLength={undoStacks[editorOpenTrack] ? undoStacks[editorOpenTrack].length : 0}
            />
        </div>
    );
}
