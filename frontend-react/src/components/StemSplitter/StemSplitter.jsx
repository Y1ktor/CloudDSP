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

function MidiScheduler({ trackName, activeBpm, originalBpm, progress, isPlaying, parsedMidiStems, audioCtxRef, synthRef, isMidiMode }) {
    useMidiSynth(audioCtxRef, progress, isPlaying, parsedMidiStems, trackName, activeBpm, originalBpm, synthRef, isMidiMode);
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
 * @param {boolean} props.isSplitting - Tracks if AWS Batch is currently processing
 * @param {string} props.statusMessage - The dynamic loading text
 * @param {Object} props.stemUrls - Dictionary of pre-signed S3 URLs returned by the server
 * @param {string} props.errorMsg - Any error messages to display
 * @param {Function} props.setErrorMsg - State setter for errors
 * @param {Function} props.setStemUrls - State setter for the stem URLs
 * @param {Function} props.executeStemSplit - Master function in App.jsx to trigger AWS upload
 * @param {Function} props.connectWebSocket - Function to initiate the background WebSocket
 * @param {Function} props.closeWebSocket - Function to explicitly close the connection
 */
export default function StemSplitter({
    file, setFile,
    fileName, setFileName,
    splitMode, setSplitMode,
    isSplitting, statusMessage, stemUrls, errorMsg, setErrorMsg, setStemUrls,
    executeStemSplit, connectWebSocket, closeWebSocket
}) {
    const isSplittingRef = React.useRef(isSplitting);
    React.useEffect(() => {
        isSplittingRef.current = isSplitting;
    }, [isSplitting]);

    const [showSigMenu, setShowSigMenu] = React.useState(false);
    const [selectedTrack, setSelectedTrack] = React.useState(null);
    const [editorOpenTrack, setEditorOpenTrack] = React.useState(null);
    const [activeMidiTracks, setActiveMidiTracks] = React.useState({});
    const [midiStateBeforeEditor, setMidiStateBeforeEditor] = React.useState({});

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

    // 1. Instruments
    const { globalSynthRef, guitarSynthRef, bassSynthRef } = useInstruments();

    // 2. Audio Player
    const audioEngine = useAudioMultiTrackPlayer(stemUrls, file, activeMidiTracks);

    // 3. MIDI Manager
    const { parsedMidiStems, setParsedMidiStems, originalMidiStems, isMidiLoading } = useMidiManager(
        stemUrls, audioEngine.timeSignature, audioEngine.setBpm, audioEngine.setOriginalBpm, audioEngine.audioCtxRef, globalSynthRef, guitarSynthRef, bassSynthRef
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
        selectedTrack,
        handleUndoMidi
    });

    React.useEffect(() => {
        connectWebSocket();
        return () => {
            if (!isSplittingRef.current) closeWebSocket();
            if (globalSynthRef.current) {
                try { globalSynthRef.current.stop(); } catch (e) { }
            }
        };
    }, [connectWebSocket, closeWebSocket, globalSynthRef]);

    const handleFileUpload = (e) => {
        const uploadedFile = e.target.files[0];
        if (uploadedFile) {
            setFile(uploadedFile);
            setFileName(uploadedFile.name);
            setStemUrls(null);
            setErrorMsg("");
        }
    };

    const tracksToRender = React.useMemo(() => {
        const tr = {};
        if (file && audioEngine.originalUrl) tr['Original'] = audioEngine.originalUrl;
        else if (!file && stemUrls && Object.keys(stemUrls).length > 0) tr['Original'] = stemUrls[Object.keys(stemUrls)[0]];
        if (stemUrls) Object.assign(tr, stemUrls);
        return tr;
    }, [file, audioEngine.originalUrl, stemUrls]);

    const [pixelsPerBar, setPixelsPerBar] = React.useState(100);
    const parsedBeatsPerBar = parseInt(audioEngine.timeSignature.split('/')[0], 10) || 4;
    const activeBpm = audioEngine.originalBpm || audioEngine.bpm;
    const totalBars = audioEngine.duration > 0 ? Math.ceil((audioEngine.duration * (activeBpm / 60)) / parsedBeatsPerBar) : 20;

    const dynamicDuration = audioEngine.originalBpm && audioEngine.duration ? audioEngine.duration * (audioEngine.originalBpm / audioEngine.bpm) : audioEngine.duration;
    const dynamicProgress = audioEngine.originalBpm && audioEngine.progress ? audioEngine.progress * (audioEngine.originalBpm / audioEngine.bpm) : audioEngine.progress;
    const playheadX = (audioEngine.progress * (activeBpm / 60) / parsedBeatsPerBar) * pixelsPerBar;

    const [isPlayheadHovered, setIsPlayheadHovered] = React.useState(false);
    const playheadDragRef = React.useRef({ isDragging: false });
    const cycleDragRef = React.useRef({ isDragging: false, mode: 'move', initialX: 0, initialStart: 0, initialEnd: 0 });
    const timelineRef = React.useRef(null);

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
                justifyContent: stemUrls ? 'flex-start' : 'center',
                alignItems: stemUrls ? 'stretch' : 'center',
                color: '#777',
                border: '1px dashed #444',
                gap: '15px'
            }}>
                {isSplitting ? (
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '15px' }}>
                        <div style={{ 
                            width: '40px', height: '40px', 
                            border: '4px solid #444', borderTop: '4px solid #4CAF50', 
                            borderRadius: '50%', animation: 'spin 1s linear infinite' 
                        }} />
                        <div style={{ color: '#fff', fontWeight: 'bold' }}>{statusMessage}</div>
                        <div style={{ fontSize: '12px' }}>You can safely navigate to the EQ Canvas while this runs in the background.</div>
                        <style>{`
                            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                        `}</style>
                    </div>
                ) : stemUrls ? (
                    <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '3px' }}>
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
                                    color: isMidiLoading ? '#444' : '#e2e8f0', 
                                    fontSize: '14px', fontFamily: 'monospace', fontWeight: 'bold',
                                    padding: '4px 8px', borderRadius: '4px', width: '55px', textAlign: 'center',
                                    border: '1px solid #0a0d12',
                                    borderTop: '1px solid #485c70',
                                    boxShadow: 'inset 0 2px 5px rgba(0,0,0,0.6), 0 1px 1px rgba(255,255,255,0.05)',
                                    textShadow: isMidiLoading ? 'none' : '0 0 6px rgba(226, 232, 240, 0.4)',
                                    display: 'flex', justifyContent: 'center', userSelect: 'none'
                                }}>
                                    {isMidiLoading ? (
                                        <span style={{ cursor: 'default' }}>---</span>
                                    ) : (
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
                                tracksToRender={tracksToRender}
                                audioRefs={audioEngine.audioRefs}
                                duration={audioEngine.duration}
                                setDuration={audioEngine.setDuration}
                                toggleMute={audioEngine.toggleMute}
                                mutedTracks={audioEngine.mutedTracks}
                                toggleSolo={audioEngine.toggleSolo}
                                soloedTracks={audioEngine.soloedTracks}
                                selectedTrack={selectedTrack}
                                setSelectedTrack={setSelectedTrack}
                                onDoubleClickTrack={handleOpenEditor}
                                activeMidiTracks={activeMidiTracks}
                                toggleMidiMode={toggleMidiMode}
                            />
                            
                            {/* RIGHT COLUMN: Timeline Canvas (Scrollable) */}
                            <div style={{ flexGrow: 1, overflowX: 'auto', paddingBottom: '10px' }}>
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
                                        tracksToRender={tracksToRender}
                                        parsedMidiStems={parsedMidiStems}
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
                    <div>Stem extraction and MIDI results will appear here as downloadable multitracks</div>
                )}
            </div>

            {/* Background MIDI Schedulers */}
            {Object.keys(parsedMidiStems).map(trackName => {
                let synthRefToUse = globalSynthRef;
                if (trackName === 'guitar') synthRefToUse = guitarSynthRef;
                if (trackName === 'bass') synthRefToUse = bassSynthRef;

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
                activeBpm={activeBpm}
                originalBpm={audioEngine.originalBpm}
                parsedBeatsPerBar={parsedBeatsPerBar}
                handleSeek={audioEngine.handleSeek}
                parsedMidiStems={parsedMidiStems}
                setParsedMidiStems={setParsedMidiStems}
                audioCtxRef={audioEngine.audioCtxRef}
                progress={audioEngine.progress}
                synthRef={
                    editorOpenTrack === 'guitar' ? guitarSynthRef :
                    editorOpenTrack === 'bass' ? bassSynthRef :
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
