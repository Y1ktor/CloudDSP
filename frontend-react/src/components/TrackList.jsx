/**
 * TrackList.jsx
 * 
 * Left column of the timeline.
 * Handles zoom control and track console rendering (Mute/Solo buttons and track names).
 */
import React from 'react';

/**
 * TrackList
 * 
 * @param {Object} props - Component props
 * @param {number} props.pixelsPerBar - Current zoom level
 * @param {Function} props.setPixelsPerBar - Zoom level setter
 * @param {Object} props.tracksToRender - Dictionary of track names to URLs
 * @param {React.MutableRefObject} props.audioRefs - Ref holding all HTML5 audio elements
 * @param {number} props.duration - Total track duration in seconds
 * @param {Function} props.setDuration - Setter for duration
 * @param {Function} props.toggleMute - Mute toggle handler
 * @param {Object} props.mutedTracks - Dictionary of muted tracks
 * @param {Function} props.toggleSolo - Solo toggle handler
 * @param {Object} props.soloedTracks - Dictionary of soloed tracks
 */
export default function TrackList({
    pixelsPerBar,
    setPixelsPerBar,
    tracksToRender,
    audioRefs,
    duration,
    setDuration,
    toggleMute,
    mutedTracks,
    toggleSolo,
    soloedTracks,
    selectedTrack,
    setSelectedTrack
}) {
    return (
        <div style={{ width: '210px', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '3px' }}>
            {/* Timeline Header Left Spacer (Zoom Control) */}
            <div style={{ 
                height: '30px', background: '#333', borderRadius: '4px',
                display: 'flex', alignItems: 'center', padding: '0 10px', gap: '8px'
            }}>
                <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: '12px', fontWeight: 'bold', fontFamily: 'monospace' }}>{'<>'}</span>
                <input 
                    type="range" 
                    min="30" max="300" 
                    value={pixelsPerBar} 
                    onChange={(e) => setPixelsPerBar(Number(e.target.value))}
                    style={{ flexGrow: 1, cursor: 'pointer', height: '2px', accentColor: '#4f94d4' }}
                />
            </div>
            
            {/* Track Consoles */}
            {Object.entries(tracksToRender).map(([trackName, url]) => (
                <div 
                    key={trackName} 
                    onClick={() => setSelectedTrack(trackName)}
                    style={{ 
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between', 
                        background: selectedTrack === trackName ? '#444' : '#333', 
                        padding: '0 15px', borderRadius: '4px',
                        height: '60px', boxSizing: 'border-box',
                        cursor: 'pointer',
                        transition: 'background-color 0.2s'
                    }}
                >
                    <audio 
                        ref={el => audioRefs.current[trackName] = el}
                        src={url}
                        preload="auto"
                        crossOrigin="anonymous"
                        onLoadedMetadata={(e) => {
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
            ))}
        </div>
    );
}
