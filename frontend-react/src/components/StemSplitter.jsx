import React from 'react';
import { SplendidGrandPiano } from 'smplr';
import { useAudioMultiTrackPlayer } from '../hooks/AudioMultiTrackPlayer';
import { parseMidiFile, determineMasterBpm } from '../utils/MidiParser';
import ControlBar from './ControlBar';
import TimelineRuler from './TimelineRuler';
import TrackList from './TrackList';
import TrackGrid from './TrackGrid';
import MidiEditorPopup from './MidiEditorPopup';
import { useMidiSynth } from '../hooks/useMidiSynth';

function MidiScheduler({ trackName, activeBpm, originalBpm, progress, isPlaying, parsedMidiStems, audioCtxRef, globalSynthRef, isMidiMode }) {
    useMidiSynth(audioCtxRef, progress, isPlaying, parsedMidiStems, trackName, activeBpm, originalBpm, globalSynthRef, isMidiMode);
    return null;
}

/**
 * StemSplitter Component
 * 
 * This UI component is responsible for handling the frontend interactions for uploading
 * audio files and rendering the resulting separated stems.
 * 
 * ARCHITECTURE NOTE:
 * This component is "stateless" regarding the heavy AWS WebSocket logic. All of its
 * state (isSplitting, stemUrls, statusMessage) is actually managed globally in `App.jsx`.
 * These values are passed down as props. This architectural choice ("State Hoisting") 
 * allows the user to start a 3-minute stem split, navigate away from this page (e.g., 
 * to the EQ Canvas), and not lose their WebSocket connection or data!
 * 
 * @param {Object} props - The hoisted state props provided by App.jsx
 * @param {File} props.file - The currently selected audio file
 * @param {Function} props.setFile - State setter for the file
 * @param {string} props.fileName - Display name of the file
 * @param {Function} props.setFileName - State setter for the filename
 * @param {string} props.splitMode - The selected Demucs mode (2, 4, or 6 stems)
 * @param {Function} props.setSplitMode - State setter for the mode
 * @param {boolean} props.isSplitting - Tracks if AWS Batch is currently processing
 * @param {string} props.statusMessage - The dynamic loading text (Connecting, Uploading, etc.)
 * @param {Object} props.stemUrls - Dictionary of pre-signed S3 URLs returned by the server
 * @param {string} props.errorMsg - Any error messages to display
 * @param {Function} props.setErrorMsg - State setter for errors
 * @param {Function} props.setStemUrls - State setter for the stem URLs
 * @param {Function} props.executeStemSplit - The master function in App.jsx that opens the WebSocket and triggers the S3 upload
 * @param {Function} props.connectWebSocket - Function to initiate the background WebSocket connection
 * @param {Function} props.closeWebSocket - Function to explicitly close the connection
 */
export default function StemSplitter({
    file, setFile,
    fileName, setFileName,
    splitMode, setSplitMode,
    isSplitting, statusMessage, stemUrls, errorMsg, setErrorMsg, setStemUrls,
    executeStemSplit, connectWebSocket, closeWebSocket
}) {

    // We use a ref to track the LATEST isSplitting value so our unmount cleanup function 
    // can correctly determine if a job is actively running in the background.
    const isSplittingRef = React.useRef(isSplitting);
    React.useEffect(() => {
        isSplittingRef.current = isSplitting;
    }, [isSplitting]);

    // UI state for dropdown menus
    const [showSigMenu, setShowSigMenu] = React.useState(false);

    // Track selection state
    const [selectedTrack, setSelectedTrack] = React.useState(null);

    // MIDI Editor popup state
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

    // ==== MULTITRACK PLAYER STATE (Refactored to Hook) ====
    const {
        audioCtxRef,
        audioRefs,
        originalUrl,
        isPlaying,
        progress,
        duration,
        mutedTracks,
        soloedTracks,
        isCycling,
        setIsCycling,
        bpm,
        timeSignature,
        setTimeSignature,
        setDuration,
        togglePlay,
        handleGoToBeginning,
        handleSeek,
        toggleMute,
        toggleSolo,
        handleBpmMouseDown,
        formatTime,
        setBpm,
        originalBpm,
        setOriginalBpm,
        cycleRegion,
        setCycleRegion
    } = useAudioMultiTrackPlayer(stemUrls, file, activeMidiTracks);

    const [parsedMidiStems, setParsedMidiStems] = React.useState({});
    const [isMidiLoading, setIsMidiLoading] = React.useState(true);
    const hasFetchedMidi = React.useRef(false);
    const globalSynthRef = React.useRef(null);

    // MOCK: Fetch local MIDI files to test MIDI processing and smart BPM voting
    React.useEffect(() => {
        if (!stemUrls || hasFetchedMidi.current) return;
        
        const fetchAndParse = async () => {
            setIsMidiLoading(true);
            
            // Initialize AudioContext early so we can start downloading samples in the background!
            if (!audioCtxRef.current) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                audioCtxRef.current = new AudioContext();
            }
            if (!globalSynthRef.current) {
                globalSynthRef.current = new SplendidGrandPiano(audioCtxRef.current);
            }

            // MOCK DELAY: wait 3 seconds to simulate AWS Basic Pitch cold start/processing
            await new Promise(resolve => setTimeout(resolve, 3000));
            
            const mockMidiFiles = {
                'bass': '/mock-midi/yosemite-bass-midi.mid',
                'drums': '/mock-midi/yosemite-drums-midi.mid',
                'guitar': '/mock-midi/yosemite-guitar-midi.mid',
                'other': '/mock-midi/yosemite-other-midi.mid',
                'piano': '/mock-midi/yosemite-piano-midi.mid',
                'vocals': '/mock-midi/yosemite-vocals-midi.mid'
            };
            
            try {
                const parsed = {};
                for (const [track, url] of Object.entries(mockMidiFiles)) {
                    // Provide the active timeSignature so bars calculate correctly
                    const data = await parseMidiFile(url, timeSignature);
                    parsed[track] = data;
                }
                
                setParsedMidiStems(parsed);
                
                // Invoke our new hierarchy logic to find the best master BPM!
                const bestBpm = determineMasterBpm(parsed);
                
                // Update the hook state, causing the canvas to instantly recalculate!
                setOriginalBpm(bestBpm);
                setBpm(bestBpm);
                setIsMidiLoading(false);
                hasFetchedMidi.current = true;
                
                console.log("Mock MIDI loaded. Smart BPM chosen:", bestBpm);
            } catch (err) {
                console.error("Mock MIDI fetch failed:", err);
                setIsMidiLoading(false);
            }
        };

        fetchAndParse();
    }, [stemUrls, timeSignature, setBpm]);

    // Instantly connect to the WebSocket in the background the moment this page loads
    React.useEffect(() => {
        connectWebSocket();
        
        // This cleanup function runs exactly when the user clicks away from the page
        return () => {
            if (!isSplittingRef.current) {
                // User navigated away without starting a job. Save money!
                closeWebSocket();
            }
            if (globalSynthRef.current) {
                globalSynthRef.current.stop();
                globalSynthRef.current = null;
            }
        };
    }, [connectWebSocket, closeWebSocket]);

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
        if (file && originalUrl) tr['Original'] = originalUrl;
        else if (!file && stemUrls && Object.keys(stemUrls).length > 0) tr['Original'] = stemUrls[Object.keys(stemUrls)[0]];
        if (stemUrls) Object.assign(tr, stemUrls);
        return tr;
    }, [file, originalUrl, stemUrls]);

    const [pixelsPerBar, setPixelsPerBar] = React.useState(100);
    const parsedBeatsPerBar = parseInt(timeSignature.split('/')[0], 10) || 4;
    
    // We must calculate grid spacing using the track's original BPM, so the grid remains 
    // static and stable even when the user adjusts the playback speed (BPM slider).
    const activeBpm = originalBpm || bpm;
    
    // Calculate canvas size based on Master Audio duration. (Default to 20 bars if no audio loaded)
    const totalBars = duration > 0 ? Math.ceil((duration * (activeBpm / 60)) / parsedBeatsPerBar) : 20;

    // Calculate dynamic physical track length in seconds (adjusts when user changes BPM)
    const dynamicDuration = originalBpm && duration ? duration * (originalBpm / bpm) : duration;
    const dynamicProgress = originalBpm && progress ? progress * (originalBpm / bpm) : progress;
    
    const playheadX = (progress * (activeBpm / 60) / parsedBeatsPerBar) * pixelsPerBar;

    // Cycle Loop Region Logic is now managed globally by useAudioMultiTrackPlayer

    // Playhead Drag Logic
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
                    handleSeek({ target: { value: newProgress } });
                }
            } else if (cycleDragRef.current.isDragging) {
                const mode = cycleDragRef.current.mode;
                const activePixels = cycleDragRef.current.pixelsPerBar || pixelsPerBar;
                const deltaX = e.clientX - cycleDragRef.current.initialX;
                const deltaBars = deltaX / activePixels;
                // Snap delta to beats
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
                    
                    setCycleRegion({ startBar: newStart, endBar: newEnd });
                } else if (mode === 'resize-left') {
                    let newStart = cycleDragRef.current.initialStart + snappedDeltaBars;
                    const minimumSpan = 1 / parsedBeatsPerBar;
                    if (newStart < 0) newStart = 0;
                    if (newStart > cycleDragRef.current.initialEnd - minimumSpan) {
                        newStart = cycleDragRef.current.initialEnd - minimumSpan;
                    }
                    setCycleRegion({ startBar: newStart, endBar: cycleDragRef.current.initialEnd });
                } else if (mode === 'resize-right') {
                    let newEnd = cycleDragRef.current.initialEnd + snappedDeltaBars;
                    const minimumSpan = 1 / parsedBeatsPerBar;
                    if (newEnd > totalBars) newEnd = totalBars;
                    if (newEnd < cycleDragRef.current.initialStart + minimumSpan) {
                        newEnd = cycleDragRef.current.initialStart + minimumSpan;
                    }
                    setCycleRegion({ startBar: cycleDragRef.current.initialStart, endBar: newEnd });
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
    }, [activeBpm, pixelsPerBar, totalBars, parsedBeatsPerBar, handleSeek]);

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
                            <button title="Go to Beginning" onClick={handleGoToBeginning} style={{
                                background: 'transparent', color: 'white', border: 'none',
                                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                            }}>
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
                            </button>

                            <button title="Play/Pause" onClick={togglePlay} style={{
                                background: 'transparent', color: 'white', border: 'none',
                                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                            }}>
                                {isPlaying ? (
                                    <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                                ) : (
                                    <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                                )}
                            </button>

                            <button title="Toggle Cycle" onClick={() => setIsCycling(!isCycling)} style={{
                                background: isCycling ? '#8B6508' : 'transparent', 
                                color: isCycling ? '#fff' : 'white', 
                                border: 'none', borderRadius: '4px',
                                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '2px', 
                                opacity: 0.8,
                                transition: 'background-color 0.2s'
                            }}>
                                <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
                            </button>
                            
                            <div className="time-display" style={{ color: '#fff', fontSize: '14px', fontFamily: 'monospace', marginLeft: '10px', whiteSpace: 'nowrap' }}>
                                {isMidiLoading ? 
                                    `${formatTime(progress)} / ${formatTime(duration)}` : 
                                    `${formatTime(dynamicProgress)} / ${formatTime(dynamicDuration)}`
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
                                                onMouseDown={(e) => handleBpmMouseDown(e, 'int')}
                                                style={{ cursor: 'ns-resize', flexGrow: 1, textAlign: 'right' }}
                                            >{Math.floor(bpm)}</span>
                                            <span style={{ cursor: 'default' }}>.</span>
                                            <span 
                                                onMouseDown={(e) => handleBpmMouseDown(e, 'dec')}
                                                style={{ cursor: 'ns-resize', flexGrow: 1, textAlign: 'left' }}
                                            >{Math.round((bpm - Math.floor(bpm)) * 10)}</span>
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
                                    {timeSignature}
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
                                                    onClick={() => { setTimeSignature(sig); setShowSigMenu(false); }}
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
                                audioRefs={audioRefs}
                                duration={duration}
                                setDuration={setDuration}
                                toggleMute={toggleMute}
                                mutedTracks={mutedTracks}
                                toggleSolo={toggleSolo}
                                soloedTracks={soloedTracks}
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
                                    {duration > 0 && (
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
                                        duration={duration}
                                        pixelsPerBar={pixelsPerBar}
                                        cycleDragRef={cycleDragRef}
                                        cycleRegion={cycleRegion}
                                        isCycling={isCycling}
                                        totalBars={totalBars}
                                        timeSignature={timeSignature}
                                        timelineRef={timelineRef}
                                        playheadDragRef={playheadDragRef}
                                        setIsPlayheadHovered={setIsPlayheadHovered}
                                        isPlayheadHovered={isPlayheadHovered}
                                        playheadX={playheadX}
                                        activeBpm={activeBpm}
                                        parsedBeatsPerBar={parsedBeatsPerBar}
                                        handleSeek={handleSeek}
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
            {Object.keys(parsedMidiStems).map(trackName => (
                <MidiScheduler
                    key={`midi-synth-${trackName}`}
                    trackName={trackName}
                    activeBpm={activeBpm}
                    originalBpm={originalBpm}
                    progress={progress}
                    isPlaying={isPlaying}
                    parsedMidiStems={parsedMidiStems}
                    audioCtxRef={audioCtxRef}
                    globalSynthRef={globalSynthRef}
                    isMidiMode={!!activeMidiTracks[trackName]}
                />
            ))}

            {/* MIDI Editor Pop-up Window */}
            <MidiEditorPopup 
                trackName={editorOpenTrack} 
                onClose={handleCloseEditor} 
                duration={duration}
                pixelsPerBar={pixelsPerBar}
                totalBars={totalBars}
                playheadX={playheadX}
                cycleDragRef={cycleDragRef}
                cycleRegion={cycleRegion}
                isCycling={isCycling}
                timeSignature={timeSignature}
                playheadDragRef={playheadDragRef}
                setIsPlayheadHovered={setIsPlayheadHovered}
                isPlayheadHovered={isPlayheadHovered}
                handleGoToBeginning={handleGoToBeginning}
                isPlaying={isPlaying}
                togglePlay={togglePlay}
                toggleCycling={() => setIsCycling(!isCycling)}
                mutedTracks={mutedTracks}
                soloedTracks={soloedTracks}
                toggleMute={toggleMute}
                toggleSolo={toggleSolo}
                activeBpm={activeBpm}
                originalBpm={originalBpm}
                parsedBeatsPerBar={parsedBeatsPerBar}
                handleSeek={handleSeek}
                parsedMidiStems={parsedMidiStems}
                setParsedMidiStems={setParsedMidiStems}
                audioCtxRef={audioCtxRef}
                progress={progress}
                globalSynthRef={globalSynthRef}
                isMidiMode={!!activeMidiTracks[editorOpenTrack]}
                setIsMidiMode={() => toggleMidiMode(editorOpenTrack)}
            />
        </div>
    );
}
