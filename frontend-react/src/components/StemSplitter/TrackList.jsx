/**
 * TrackList.jsx
 *
 * Fixed timeline console. Drum lanes are MIDI-only child rows; their parent
 * drum stem retains the audio, mute, solo, and MIDI-playback controls.
 */
import React from 'react';

export default function TrackList({
    pixelsPerBar,
    setPixelsPerBar,
    timelineRows,
    audioRefs,
    duration,
    setDuration,
    toggleMute,
    mutedTracks,
    toggleSolo,
    soloedTracks,
    selectedTrack,
    setSelectedTrack,
    onDoubleClickTrack,
    activeMidiTracks = {},
    toggleMidiMode
}) {
    return (
        <div style={{ width: '210px', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <div style={{
                height: '30px', background: '#333', borderRadius: '4px', display: 'flex', alignItems: 'center',
                padding: '0 10px', gap: '8px'
            }}>
                <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: '12px', fontWeight: 'bold', fontFamily: 'monospace' }}>{'<>'}</span>
                <input
                    type="range" min="30" max="300" value={pixelsPerBar}
                    onChange={(event) => setPixelsPerBar(Number(event.target.value))}
                    style={{ flexGrow: 1, cursor: 'pointer', height: '2px', accentColor: '#4f94d4' }}
                />
            </div>

            {timelineRows.map((row) => {
                const isDrumLane = row.kind === 'drum-lane';
                const trackName = row.trackName;
                if (isDrumLane) {
                    return (
                        <div
                            key={row.id}
                            onClick={() => setSelectedTrack(selectedTrack === row.id ? null : row.id)}
                            onDoubleClick={() => onDoubleClickTrack(trackName)}
                            style={{
                                height: '60px', boxSizing: 'border-box', display: 'flex', alignItems: 'center',
                                padding: '0 15px 0 31px', cursor: 'pointer', userSelect: 'none',
                                background: selectedTrack === row.id ? '#3f4655' : '#2b2b2b',
                                borderLeft: `3px solid ${row.drumVoice.color}`, borderRadius: '4px',
                                transition: 'background-color 0.2s'
                            }}
                            title={`ADTOF ${row.drumVoice.label} lane — double-click to edit the drum kit MIDI`}
                        >
                            <span style={{ color: row.drumVoice.color, fontWeight: '700', fontSize: '13px' }}>
                                {row.drumVoice.label}
                            </span>
                        </div>
                    );
                }

                return (
                    <div
                        key={row.id}
                        onClick={() => setSelectedTrack(selectedTrack === row.id ? null : row.id)}
                        onDoubleClick={() => onDoubleClickTrack(trackName)}
                        style={{
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                            background: selectedTrack === row.id ? '#444' : '#333', padding: '0 15px',
                            borderRadius: '4px', height: '60px', boxSizing: 'border-box', cursor: 'pointer',
                            userSelect: 'none', transition: 'background-color 0.2s'
                        }}
                    >
                        <audio
                            ref={(element) => { audioRefs.current[trackName] = element; }}
                            src={row.url} preload="auto" crossOrigin="anonymous"
                            onLoadedMetadata={(event) => {
                                if (duration === 0) setDuration(event.target.duration);
                            }}
                        />
                        <div style={{ color: '#fff', fontWeight: 'bold', textTransform: 'capitalize', width: '80px' }}>
                            {trackName}
                        </div>
                        <div style={{ display: 'flex', gap: '8px' }}>
                            {trackName !== 'Original' && (
                                <button onClick={(event) => { event.stopPropagation(); toggleMidiMode?.(trackName); }} style={{
                                    height: '24px', padding: '0 8px', background: 'transparent',
                                    color: activeMidiTracks[trackName] ? '#4CAF50' : '#aaa',
                                    border: `1px solid ${activeMidiTracks[trackName] ? '#4CAF50' : '#555'}`,
                                    boxShadow: activeMidiTracks[trackName] ? '0 0 8px rgba(76, 175, 80, 0.5)' : 'none',
                                    borderRadius: '4px', cursor: 'pointer', fontSize: '11px', fontWeight: 'bold'
                                }} title="Toggle MIDI synthesis playback">MIDI</button>
                            )}
                            <button onClick={(event) => { event.stopPropagation(); toggleMute(trackName); }} style={{
                                width: '24px', height: '24px', background: mutedTracks[trackName] ? '#e53935' : '#555',
                                color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '11px', fontWeight: 'bold'
                            }} title="Mute">M</button>
                            <button onClick={(event) => { event.stopPropagation(); toggleSolo(trackName); }} style={{
                                width: '24px', height: '24px', background: soloedTracks[trackName] ? '#e0a800' : '#555',
                                color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '11px', fontWeight: 'bold'
                            }} title="Solo">S</button>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
