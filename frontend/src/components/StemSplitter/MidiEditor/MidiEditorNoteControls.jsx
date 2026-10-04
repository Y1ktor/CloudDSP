/**
 * Provides selected-note velocity adjustment and independent horizontal and
 * vertical MIDI editor zoom controls.
 */
import React from 'react';

/** Selection velocity and independent popup zoom controls. */
export default function MidiEditorNoteControls({
    selectedNoteIndices,
    commonVelocity,
    handleVelocityChange,
    pushUndoState,
    popupPixelsPerBar,
    setPopupPixelsPerBar,
    isAdtofDrum,
    popupRowHeight,
    setPopupRowHeight,
}) {
    return (
        <>
            {/* Velocity Control */}
            {selectedNoteIndices.size > 0 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginRight: '20px', borderRight: '1px solid var(--studio-border)', paddingRight: '20px' }}>
                    <span style={{ color: 'var(--studio-text-muted)', fontSize: '12px', fontWeight: 'bold' }}>
                        Velocity: {Math.round(commonVelocity * 100)}
                    </span>
                    <input
                        type="range"
                        min="0.05" max="1" step="0.01"
                        value={commonVelocity}
                        onChange={handleVelocityChange}
                        onPointerDown={pushUndoState}
                        style={{ width: '80px', cursor: 'pointer', accentColor: 'var(--studio-accent)' }}
                    />
                </div>
            )}

            {/* Zoom Controls */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }} title="Horizontal Zoom">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="var(--studio-text-muted)">
                        <path d="M22 12l-4-4v3H6V8l-4 4 4 4v-3h12v3z"/>
                    </svg>
                    <input
                        type="range"
                        min="20" max="400"
                        value={popupPixelsPerBar}
                        onChange={(e) => setPopupPixelsPerBar(Number(e.target.value))}
                        style={{ width: '80px', cursor: 'pointer' }}
                    />
                </div>
                {!isAdtofDrum && <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }} title="Vertical Zoom">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="var(--studio-text-muted)">
                        <path d="M12 2L8 6h3v12H8l4 4 4-4h-3V6h3z"/>
                    </svg>
                    <input
                        type="range"
                        min="8" max="32"
                        value={popupRowHeight}
                        onChange={(e) => setPopupRowHeight(Number(e.target.value))}
                        style={{ width: '80px', cursor: 'pointer' }}
                    />
                </div>}
            </div>
        </>
    );
}
