import React from 'react';

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

    // ==== MULTITRACK PLAYER STATE ====
    const audioRefs = React.useRef({});
    const [isPlaying, setIsPlaying] = React.useState(false);
    const [progress, setProgress] = React.useState(0);
    const [duration, setDuration] = React.useState(0);
    const [mutedTracks, setMutedTracks] = React.useState({});
    const [soloedTracks, setSoloedTracks] = React.useState({});
    
    // Reset player state when new stems arrive
    React.useEffect(() => {
        if (stemUrls) {
            setIsPlaying(false);
            setProgress(0);
            setDuration(0);
            setMutedTracks({});
            setSoloedTracks({});
        }
    }, [stemUrls]);

    const togglePlay = () => {
        if (!stemUrls) return;
        const nextState = !isPlaying;
        setIsPlaying(nextState);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) {
                if (nextState) audio.play();
                else audio.pause();
            }
        });
    };

    const handleGoToBeginning = () => {
        setProgress(0);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) audio.currentTime = 0;
        });
    };

    const handleSeek = (e) => {
        const time = Number(e.target.value);
        setProgress(time);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) {
                audio.currentTime = time;
            }
        });
    };

    const toggleMute = (trackName) => {
        setMutedTracks(prev => ({ ...prev, [trackName]: !prev[trackName] }));
    };

    const toggleSolo = (trackName) => {
        setSoloedTracks(prev => ({ ...prev, [trackName]: !prev[trackName] }));
    };

    // Automatically apply mute/solo logic to the raw HTML audio elements
    React.useEffect(() => {
        if (!stemUrls) return;
        const isAnySoloed = Object.values(soloedTracks).some(isSoloed => isSoloed);
        
        Object.keys(stemUrls).forEach(trackName => {
            const audio = audioRefs.current[trackName];
            if (audio) {
                if (isAnySoloed) {
                    // If any track is soloed, this track is audible ONLY if it is also soloed
                    audio.muted = !soloedTracks[trackName];
                } else {
                    // Otherwise, just use the track's normal mute state
                    audio.muted = !!mutedTracks[trackName];
                }
            }
        });
    }, [mutedTracks, soloedTracks, stemUrls]);

    React.useEffect(() => {
        let interval;
        if (isPlaying && stemUrls) {
            const firstTrack = Object.keys(stemUrls)[0];
            const masterAudio = audioRefs.current[firstTrack];
            
            interval = setInterval(() => {
                if (masterAudio) {
                    setProgress(masterAudio.currentTime);
                    if (masterAudio.duration && masterAudio.duration !== duration) {
                        setDuration(masterAudio.duration);
                    }
                    if (masterAudio.ended) {
                        setIsPlaying(false);
                        setProgress(0);
                    }
                }
            }, 50);
        }
        return () => clearInterval(interval);
    }, [isPlaying, stemUrls, duration]);

    const formatTime = (seconds) => {
        if (!seconds || isNaN(seconds)) return "0:00";
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    };

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
            width: '90vw',
            maxWidth: '1200px',
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
                    <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '15px' }}>
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
                            
                            <input 
                                type="range" 
                                min={0} 
                                max={duration || 100} 
                                value={progress} 
                                onChange={handleSeek} 
                                style={{ flexGrow: 1, cursor: 'pointer' }} 
                            />
                            
                            <div style={{ color: '#fff', fontSize: '14px', minWidth: '80px', textAlign: 'right', fontFamily: 'monospace' }}>
                                {formatTime(progress)} / {formatTime(duration)}
                            </div>
                        </div>

                        {/* Stems List */}
                        {Object.entries(stemUrls).map(([trackName, url]) => (
                            <div key={trackName} style={{ 
                                display: 'flex', alignItems: 'center', justifyContent: 'space-between', 
                                background: '#333', padding: '10px 15px', borderRadius: '4px' 
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
                                
                                <div style={{ display: 'flex', alignItems: 'center', gap: '15px' }}>
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

                                <div style={{ flexGrow: 1 }} /> {/* Spacer */}

                                <button style={{
                                    background: '#2196F3', color: 'white', border: 'none', 
                                    padding: '6px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px',
                                    fontWeight: 'bold'
                                }}>
                                    Convert to MIDI
                                </button>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div>Stem extraction and MIDI results will appear here as downloadable multitracks</div>
                )}
            </div>
        </div>
    );
}
