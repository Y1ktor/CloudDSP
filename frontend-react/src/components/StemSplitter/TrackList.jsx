/**
 * TrackList.jsx
 *
 * Fixed timeline console. Drum lanes are MIDI-only child rows; their parent
 * drum stem retains the audio controls, while each lane has MIDI M/S and gain.
 */
import React from 'react';

function GainSlider({ value = 0, onChange, ariaLabel }) {
    const gainDb = Number.isFinite(Number(value)) ? Number(value) : 0;
    const progress = Math.max(0, Math.min(100, ((gainDb + 12) / 24) * 100));

    return (
        <label
            onClick={(event) => event.stopPropagation()}
            onPointerDown={(event) => event.stopPropagation()}
            style={{ display: 'flex', alignItems: 'center', gap: '7px', marginTop: '5px', cursor: 'pointer' }}
        >
            <input
                className="track-volume-slider"
                type="range"
                min="-12"
                max="12"
                step="0.1"
                value={gainDb}
                onChange={(event) => onChange?.(Number(event.target.value))}
                aria-label={ariaLabel}
                style={{
                    flexGrow: 1, minWidth: 0,
                    '--track-gain-progress': `${progress}%`,
                }}
            />
            <span style={{ color: '#bac4d2', width: '42px', textAlign: 'right', fontSize: '9px', fontFamily: 'monospace' }}>
                {`${gainDb >= 0 ? '+' : ''}${gainDb.toFixed(1)} dB`}
            </span>
        </label>
    );
}

export default function TrackList({
    pixelsPerBar,
    setPixelsPerBar,
    timelineRows,
    toggleMute,
    mutedTracks,
    toggleSolo,
    soloedTracks,
    trackGainsDb = {},
    setTrackGainDb,
    drumVoiceGainsDb = {},
    setDrumVoiceGainDb,
    selectedTrack,
    setSelectedTrack,
    onDoubleClickTrack,
    activeMidiTracks = {},
    toggleMidiMode,
    toggleDrumSubtracks,
    drumMutedVoices = {},
    drumSoloedVoices = {},
    toggleDrumMute,
    toggleDrumSolo
}) {
    return (
        <div style={{ width: '210px', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <style>{`
                .track-volume-slider {
                    --track-gain-progress: 50%;
                    appearance: none;
                    -webkit-appearance: none;
                    -moz-appearance: none;
                    width: 100%;
                    height: 4px;
                    margin: 0;
                    border: 0;
                    border-radius: 999px;
                    background: linear-gradient(
                        90deg,
                        #4f94d4 0%,
                        #4f94d4 var(--track-gain-progress),
                        #505a67 var(--track-gain-progress),
                        #505a67 100%
                    );
                    cursor: pointer;
                }
                .track-volume-slider::-webkit-slider-runnable-track {
                    height: 4px;
                    border-radius: 999px;
                    background: transparent;
                }
                .track-volume-slider::-webkit-slider-thumb {
                    appearance: none;
                    -webkit-appearance: none;
                    width: 12px;
                    height: 12px;
                    margin-top: -4px;
                    border: 1px solid #9fb7cd;
                    border-radius: 50%;
                    background: #e5edf5;
                    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.7);
                }
                .track-volume-slider::-moz-range-track {
                    height: 4px;
                    border: 0;
                    border-radius: 999px;
                    background: transparent;
                }
                .track-volume-slider::-moz-range-progress {
                    height: 4px;
                    border-radius: 999px;
                    background: transparent;
                }
                .track-volume-slider::-moz-range-thumb {
                    width: 10px;
                    height: 10px;
                    border: 1px solid #9fb7cd;
                    border-radius: 50%;
                    background: #e5edf5;
                    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.7);
                }
                .track-volume-slider:focus-visible {
                    outline: 2px solid #70b4ef;
                    outline-offset: 3px;
                }
            `}</style>
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
                    const isMuted = Boolean(drumMutedVoices[row.id]);
                    const isSoloed = Boolean(drumSoloedVoices[row.id]);
                    return (
                        <div
                            key={row.id}
                            onClick={() => setSelectedTrack(selectedTrack === row.id ? null : row.id)}
                            onDoubleClick={() => onDoubleClickTrack(trackName)}
                            style={{
                                height: '78px', boxSizing: 'border-box', display: 'flex', flexDirection: 'column', justifyContent: 'center',
                                padding: '6px 15px 6px 31px', cursor: 'pointer', userSelect: 'none',
                                background: selectedTrack === row.id ? '#3f4655' : '#2b2b2b',
                                borderLeft: `3px solid ${row.drumVoice.color}`, borderRadius: '4px',
                                transition: 'background-color 0.2s'
                            }}
                            title={`ADTOF ${row.drumVoice.label} lane — double-click to edit the drum kit MIDI`}
                        >
                            <div style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                                <span style={{ color: row.drumVoice.color, fontWeight: '700', fontSize: '13px', flexGrow: 1 }}>
                                    {row.drumVoice.label}
                                </span>
                                <div style={{ display: 'flex', gap: '5px' }}>
                                    <button
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            toggleDrumMute?.(trackName, row.drumVoice.id);
                                        }}
                                        style={{
                                            width: '24px', height: '24px', background: isMuted ? '#e53935' : '#555',
                                            color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer',
                                            fontSize: '11px', fontWeight: 'bold'
                                        }}
                                        title={`Mute ${row.drumVoice.label} MIDI`}
                                    >M</button>
                                    <button
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            toggleDrumSolo?.(trackName, row.drumVoice.id);
                                        }}
                                        style={{
                                            width: '24px', height: '24px', background: isSoloed ? '#e0a800' : '#555',
                                            color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer',
                                            fontSize: '11px', fontWeight: 'bold'
                                        }}
                                        title={`Solo ${row.drumVoice.label} MIDI`}
                                    >S</button>
                                </div>
                            </div>
                            <GainSlider
                                value={drumVoiceGainsDb[row.id] ?? 0}
                                onChange={(value) => setDrumVoiceGainDb?.(trackName, row.drumVoice.id, value)}
                                ariaLabel={`${row.drumVoice.label} MIDI gain in decibels`}
                            />
                        </div>
                    );
                }

                return (
                    <div
                        key={row.id}
                        onClick={() => setSelectedTrack(selectedTrack === row.id ? null : row.id)}
                        onDoubleClick={() => onDoubleClickTrack(trackName)}
                        style={{
                            display: 'flex', flexDirection: 'column', justifyContent: 'center',
                            background: selectedTrack === row.id ? '#444' : '#333', padding: '6px 15px',
                            borderRadius: '4px', height: '78px', boxSizing: 'border-box', cursor: 'pointer',
                            userSelect: 'none', transition: 'background-color 0.2s'
                        }}
                    >
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minHeight: '26px' }}>
                            <div style={{ color: '#fff', fontWeight: 'bold', textTransform: 'capitalize', width: '80px', display: 'flex', alignItems: 'center' }}>
                                {row.hasDrumSubtracks && (
                                    <button
                                        type="button"
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            toggleDrumSubtracks?.(trackName);
                                        }}
                                        style={{
                                            height: '26px', padding: 0, margin: 0, border: 'none',
                                            background: 'transparent',
                                            color: '#fff', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
                                            gap: '3px', fontSize: 'inherit', fontWeight: 'bold',
                                            textTransform: 'capitalize'
                                        }}
                                        title={`${row.isDrumExpanded ? 'Collapse' : 'Expand'} drum subtracks`}
                                        aria-label={`${row.isDrumExpanded ? 'Collapse' : 'Expand'} drum subtracks`}
                                    >
                                        <span>{trackName}</span>
                                        <span aria-hidden="true" style={{ color: '#b9c5d6', fontSize: '10px' }}>▼</span>
                                    </button>
                                )}
                                {!row.hasDrumSubtracks && <span>{trackName}</span>}
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
                        <GainSlider
                            value={trackGainsDb[trackName] ?? 0}
                            onChange={(value) => setTrackGainDb?.(trackName, value)}
                            ariaLabel={`${trackName} gain in decibels`}
                        />
                    </div>
                );
            })}
        </div>
    );
}
