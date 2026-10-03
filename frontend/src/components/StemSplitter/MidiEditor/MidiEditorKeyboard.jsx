import React from 'react';
import { ADTOF_DRUM_VOICES, getDrumVoiceTrackId } from '../../../utils/DrumMidi';
import { DRUM_EDITOR_ROW_HEIGHT } from './midiEditorLayout';

function DrumVoiceGainSlider({ value = 0, onChange, ariaLabel }) {
    const gainDb = Number.isFinite(Number(value)) ? Number(value) : 0;
    const progress = Math.max(0, Math.min(100, ((gainDb + 12) / 24) * 100));

    return (
        <label style={{ display: 'flex', alignItems: 'center', gap: '4px', marginTop: '3px', cursor: 'pointer' }}>
            <input
                className="drum-editor-gain-slider"
                type="range"
                min="-12"
                max="12"
                step="0.1"
                value={gainDb}
                onChange={(event) => onChange?.(Number(event.target.value))}
                aria-label={ariaLabel}
                style={{ flexGrow: 1, minWidth: 0, '--drum-editor-gain-progress': `${progress}%` }}
            />
            <span style={{ width: '31px', color: '#bac4d2', fontFamily: 'monospace', fontSize: '8px', textAlign: 'right' }}>
                {`${gainDb >= 0 ? '+' : ''}${gainDb.toFixed(1)}`}
            </span>
        </label>
    );
}

/** Piano pitches or the named drum lanes and their per-voice controls. */
export default function MidiEditorKeyboard({
    isAdtofDrum,
    trackName,
    popupRowHeight,
    drumMutedVoices,
    drumSoloedVoices,
    toggleDrumMute,
    toggleDrumSolo,
    drumVoiceGainsDb,
    setDrumVoiceGainDb,
}) {
    if (isAdtofDrum) {
        return ADTOF_DRUM_VOICES.map((voice) => {
            const voiceTrackId = getDrumVoiceTrackId(trackName, voice.id);
            const isMuted = Boolean(drumMutedVoices[voiceTrackId]);
            const isSoloed = Boolean(drumSoloedVoices[voiceTrackId]);

            return (
                <div key={voice.id} style={{
                    height: `${DRUM_EDITOR_ROW_HEIGHT}px`, boxSizing: 'border-box', display: 'flex', flexDirection: 'column',
                    justifyContent: 'center', gap: '1px', padding: '4px 7px',
                    color: voice.color, backgroundColor: 'var(--studio-surface-raised)', borderBottom: '1px solid var(--studio-border)',
                    borderLeft: `3px solid ${voice.color}`, fontSize: '12px', fontWeight: '700', userSelect: 'none'
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', width: '100%', minHeight: '21px' }}>
                        <span style={{ flexGrow: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{voice.label}</span>
                        <div style={{ display: 'flex', gap: '3px' }}>
                            <button
                                type="button"
                                onClick={() => toggleDrumMute?.(trackName, voice.id)}
                                style={{
                                    width: '21px', height: '21px', padding: 0, border: 'none', borderRadius: '3px',
                                    background: isMuted ? '#d44b55' : 'var(--studio-control)', color: isMuted ? 'white' : 'var(--studio-text-secondary)', cursor: 'pointer',
                                    fontSize: '10px', fontWeight: 'bold'
                                }}
                                title={`Mute ${voice.label} MIDI`}
                            >M</button>
                            <button
                                type="button"
                                onClick={() => toggleDrumSolo?.(trackName, voice.id)}
                                style={{
                                    width: '21px', height: '21px', padding: 0, border: 'none', borderRadius: '3px',
                                    background: isSoloed ? '#c88a12' : 'var(--studio-control)', color: isSoloed ? 'white' : 'var(--studio-text-secondary)', cursor: 'pointer',
                                    fontSize: '10px', fontWeight: 'bold'
                                }}
                                title={`Solo ${voice.label} MIDI`}
                            >S</button>
                        </div>
                    </div>
                    <DrumVoiceGainSlider
                        value={drumVoiceGainsDb[voiceTrackId] ?? 0}
                        onChange={(gainDb) => setDrumVoiceGainDb?.(trackName, voice.id, gainDb)}
                        ariaLabel={`${voice.label} MIDI gain in decibels`}
                    />
                </div>
            );
        });
    }

    const keys = [];
    for (let i = 127; i >= 0; i--) {
        const isBlackKey = [1, 3, 6, 8, 10].includes(i % 12);
        const isC = (i % 12) === 0;
        const isF = (i % 12) === 5;
        const octave = Math.floor(i / 12) - 1;

        if (isBlackKey) {
            keys.push(
                <div key={`key-${i}`} style={{
                    position: 'relative',
                    height: `${popupRowHeight}px`,
                    width: '100%',
                    boxSizing: 'border-box',
                    backgroundColor: '#cfd9e3',
                }}>
                    <div style={{
                        position: 'absolute', top: '50%', left: '60%', right: 0,
                        height: '1px', backgroundColor: '#9fb0c1', zIndex: 1
                    }} />
                    <div style={{
                        position: 'absolute', top: 0, left: 0, width: '60%', height: '100%',
                        backgroundColor: '#455a70', borderBottom: '2px solid #304255',
                        borderTop: '1px solid #73869a', borderRight: '2px solid #304255',
                        borderBottomRightRadius: '3px', borderTopRightRadius: '3px',
                        boxSizing: 'border-box', zIndex: 2
                    }} />
                </div>
            );
        } else {
            const needsBottomBorder = isC || isF;
            keys.push(
                <div key={`key-${i}`} style={{
                    height: `${popupRowHeight}px`, width: '100%', boxSizing: 'border-box',
                    backgroundColor: '#cfd9e3', borderBottom: needsBottomBorder ? '1px solid #9fb0c1' : 'none',
                    color: 'var(--studio-text-secondary)', display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
                    paddingRight: '6px', fontSize: '10px', fontWeight: isC ? 'bold' : 'normal',
                    userSelect: 'none', position: 'relative', zIndex: 3
                }}>
                    {isC && `C${octave}`}
                </div>
            );
        }
    }
    return keys;
}

export function MidiEditorKeyboardStyles() {
    return (
        <style>{`
                .drum-editor-gain-slider {
                    --drum-editor-gain-progress: 50%;
                    appearance: none;
                    -webkit-appearance: none;
                    -moz-appearance: none;
                    width: 100%;
                    height: 3px;
                    margin: 0;
                    border: 0;
                    border-radius: 999px;
                    background: linear-gradient(90deg, var(--studio-accent) 0%, var(--studio-accent) var(--drum-editor-gain-progress), #c6d3df var(--drum-editor-gain-progress), #c6d3df 100%);
                    cursor: pointer;
                }
                .drum-editor-gain-slider::-webkit-slider-runnable-track {
                    height: 3px;
                    border-radius: 999px;
                    background: transparent;
                }
                .drum-editor-gain-slider::-webkit-slider-thumb {
                    appearance: none;
                    -webkit-appearance: none;
                    width: 10px;
                    height: 10px;
                    margin-top: -3.5px;
                    border: 1px solid #9fb7cd;
                    border-radius: 50%;
                    background: #e5edf5;
                    box-shadow: 0 1px 2px rgba(44, 62, 80, 0.24);
                }
                .drum-editor-gain-slider::-moz-range-track {
                    height: 3px;
                    border: 0;
                    border-radius: 999px;
                    background: transparent;
                }
                .drum-editor-gain-slider::-moz-range-progress { background: transparent; }
                .drum-editor-gain-slider::-moz-range-thumb {
                    width: 8px;
                    height: 8px;
                    border: 1px solid #9fb7cd;
                    border-radius: 50%;
                    background: #e5edf5;
                    box-shadow: 0 1px 2px rgba(44, 62, 80, 0.24);
                }
                .drum-editor-gain-slider:focus-visible { outline: 2px solid #70b4ef; outline-offset: 2px; }
        `}</style>
    );
}
