import React from 'react';
import { useAudioMultiTrackPlayer } from '../hooks/AudioMultiTrackPlayer';

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

    // ==== MULTITRACK PLAYER STATE (Refactored to Hook) ====
    const {
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
        formatTime
    } = useAudioMultiTrackPlayer(stemUrls, file);

    // Instantly connect to the WebSocket in the background the moment this page loads
    React.useEffect(() => {
        connectWebSocket();
        
        // This cleanup function runs exactly when the user clicks away from the page
        return () => {
            if (!isSplittingRef.current) {
                // User navigated away without starting a job. Save money!
                closeWebSocket();
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
            
            {/* Top Control Bar */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '15px', flexWrap: 'wrap' }}>
                <label htmlFor="stem-upload" className="upload-btn" style={{ margin: 0, cursor: isSplitting ? 'not-allowed' : 'pointer', opacity: isSplitting ? 0.5 : 1 }}>Browse...</label>
                <input type="file" id="stem-upload" accept="audio/*" style={{ display: 'none' }} onChange={handleFileUpload} disabled={isSplitting} />
                
                <div id="file-name-container" style={{ flexGrow: 1, margin: 0 }}>
                    <div id="file-name-display" style={{ color: fileName === "No file loaded" ? '#aaa' : '#fff' }}>{fileName}</div>
                </div>

                <div style={{ width: 'auto', minWidth: '150px' }}>
                    <select 
                        value={splitMode} 
                        onChange={(e) => setSplitMode(e.target.value)}
                        disabled={isSplitting}
                        style={{
                            background: '#222',
                            color: '#fff',
                            border: '1px solid #444',
                            padding: '8px 10px',
                            borderRadius: '4px',
                            cursor: isSplitting ? 'not-allowed' : 'pointer',
                            outline: 'none',
                            width: '100%',
                            fontSize: '13px',
                            fontFamily: 'inherit',
                            opacity: isSplitting ? 0.5 : 1
                        }}
                    >
                        <option value="6-stems">6 Stems (Vocals / Drums / Bass / Piano / Guitar / Other)</option>
                        <option value="4-stems">4 Stems (Vocals / Drums / Bass / Other)</option>
                        <option value="2-stems">2 Stems (Vocals / Instrumental)</option>
                    </select>
                </div>

                <button 
                    onClick={executeStemSplit}
                    disabled={isSplitting || !file}
                    style={{
                        background: isSplitting ? '#555' : (!file ? '#555' : '#4CAF50'),
                        color: 'white',
                        border: 'none',
                        padding: '8px 16px',
                        borderRadius: '4px',
                        cursor: isSplitting || !file ? 'not-allowed' : 'pointer',
                        fontWeight: 'bold',
                        transition: 'background-color 0.2s',
                        fontSize: '13px'
                    }}
                >
                    {isSplitting ? 'Processing...' : 'Upload & Split'}
                </button>
            </div>
            
            {/* Error Message */}
            {errorMsg && (
                <div style={{ color: '#ff6b6b', background: '#3b2222', padding: '10px', borderRadius: '4px', border: '1px solid #ff4444' }}>
                    {errorMsg}
                </div>
            )}

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
                                {formatTime(progress)} / {formatTime(duration)}
                            </div>

                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: '30px' }}>
                                <span className="bpm-label" style={{ color: '#fff', fontSize: '14px', fontWeight: 'bold' }}>BPM:</span>
                                <div style={{ 
                                    background: 'linear-gradient(180deg, #2A3644 0%, #1B232D 100%)',
                                    color: '#e2e8f0', 
                                    fontSize: '14px', fontFamily: 'monospace', fontWeight: 'bold',
                                    padding: '4px 8px', borderRadius: '4px', width: '55px', textAlign: 'center',
                                    border: '1px solid #0a0d12',
                                    borderTop: '1px solid #485c70',
                                    boxShadow: 'inset 0 2px 5px rgba(0,0,0,0.6), 0 1px 1px rgba(255,255,255,0.05)',
                                    textShadow: '0 0 6px rgba(226, 232, 240, 0.4)',
                                    display: 'flex', justifyContent: 'center', userSelect: 'none'
                                }}>
                                    <span 
                                        onMouseDown={(e) => handleBpmMouseDown(e, 'int')}
                                        style={{ cursor: 'ns-resize', flexGrow: 1, textAlign: 'right' }}
                                    >{Math.floor(bpm)}</span>
                                    <span style={{ cursor: 'default' }}>.</span>
                                    <span 
                                        onMouseDown={(e) => handleBpmMouseDown(e, 'dec')}
                                        style={{ cursor: 'ns-resize', flexGrow: 1, textAlign: 'left' }}
                                    >{Math.round((bpm - Math.floor(bpm)) * 10)}</span>
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

                        {/* Timeline Header (Time Bar) */}
                        <div style={{ 
                            width: '100%', height: '30px', 
                            display: 'flex', gap: '3px'
                        }}>
                            {/* Left Section (Aligns with Track Headers) */}
                            <div style={{ 
                                width: '210px', flexShrink: 0, 
                                background: '#333', borderRadius: '4px' 
                            }}></div>
                            
                            {/* Right Section (Timeline Canvas) */}
                            <div style={{ 
                                flexGrow: 1, 
                                borderRadius: '4px',
                                overflow: 'hidden',
                                position: 'relative'
                            }}>
                                <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
                                    <div style={{ flexGrow: 1, background: '#2a2a2a' }}></div>
                                    <div style={{ flexGrow: 1, background: '#333' }}></div>
                                </div>
                                
                                {/* Music Bars Overlay */}
                                <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, pointerEvents: 'none' }}>
                                    {Array.from({ length: 100 }).map((_, i) => {
                                        const beatsPerBar = parseInt(timeSignature.split('/')[0], 10) || 4;
                                        const pixelsPerBar = 100;
                                        const beatSpacing = pixelsPerBar / beatsPerBar;
                                        
                                        return (
                                            <React.Fragment key={i}>
                                                {/* Main Bar Line (Stronger Opacity, spans full height) */}
                                                <div style={{ 
                                                    position: 'absolute', 
                                                    left: `${i * pixelsPerBar}px`, 
                                                    top: 0, bottom: 0, 
                                                    width: '1px', 
                                                    background: 'rgba(255,255,255,0.18)' 
                                                }}>
                                                    <div style={{ 
                                                        position: 'absolute', 
                                                        top: '2px', left: '4px', 
                                                        color: 'rgba(255,255,255,0.4)', 
                                                        fontSize: '10px', 
                                                        fontFamily: 'monospace',
                                                        lineHeight: '1'
                                                    }}>
                                                        {i + 1}
                                                    </div>
                                                </div>
                                                
                                                {/* Ruler Measure Lines (Fainter Opacity, bottom half only) */}
                                                {Array.from({ length: beatsPerBar - 1 }).map((_, beatIndex) => (
                                                    <div key={`beat-${i}-${beatIndex}`} style={{ 
                                                        position: 'absolute', 
                                                        left: `${i * pixelsPerBar + (beatIndex + 1) * beatSpacing}px`, 
                                                        top: '50%', bottom: 0, 
                                                        width: '1px', 
                                                        background: 'rgba(255,255,255,0.06)' 
                                                    }}></div>
                                                ))}
                                            </React.Fragment>
                                        );
                                    })}
                                </div>
                            </div>
                        </div>

                        {/* Stems List */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                            {(() => {
                                const tracksToRender = {};
                            if (file && originalUrl) {
                                tracksToRender['Original'] = originalUrl;
                            } else if (!file && stemUrls) {
                                // Fallback for MOCK_PAYLOAD UI testing
                                tracksToRender['Original'] = stemUrls[Object.keys(stemUrls)[0]];
                            }
                            Object.assign(tracksToRender, stemUrls);
                            
                            return Object.entries(tracksToRender).map(([trackName, url]) => (
                                <div key={trackName} style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                                    
                                    {/* Track Header Console */}
                                    <div style={{ 
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between', 
                                        background: '#333', padding: '10px 15px', borderRadius: '4px',
                                        width: '180px', flexShrink: 0
                                    }}>
                                        {/* Hidden audio element tied to the master control */}
                                        <audio 
                                            ref={el => audioRefs.current[trackName] = el}
                                            src={url}
                                            preload="auto"
                                            onLoadedMetadata={(e) => {
                                                // Capture duration if not yet set
                                                if (duration === 0) setDuration(e.target.duration);
                                            }}
                                        />
                                        
                                        <div style={{ color: '#fff', fontWeight: 'bold', textTransform: 'capitalize', width: '80px' }}>
                                            {trackName}
                                        </div>
                                        
                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <button onClick={() => toggleMute(trackName)} style={{
                                                width: '24px', height: '24px',
                                                background: mutedTracks[trackName] ? '#e53935' : '#555',
                                                color: 'white', border: 'none', borderRadius: '4px', 
                                                cursor: 'pointer', fontSize: '11px', fontWeight: 'bold',
                                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                                transition: 'background-color 0.2s'
                                            }} title="Mute">
                                                M
                                            </button>
                                            <button onClick={() => toggleSolo(trackName)} style={{
                                                width: '24px', height: '24px',
                                                background: soloedTracks[trackName] ? '#e0a800' : '#555',
                                                color: soloedTracks[trackName] ? '#fff' : 'white', 
                                                border: 'none', borderRadius: '4px', 
                                                cursor: 'pointer', fontSize: '11px', fontWeight: 'bold',
                                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                                transition: 'background-color 0.2s'
                                            }} title="Solo">
                                                S
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            ));
                        })()}
                        </div>
                    </div>
                ) : (
                    <div>Stem extraction and MIDI results will appear here as downloadable multitracks</div>
                )}
            </div>
        </div>
    );
}
