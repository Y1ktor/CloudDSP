import React from 'react';

/** Shortcut help anchored to its toolbar button; state remains with the popup. */
export function MidiEditorHint({ showHintBox, setShowHintBox }) {
    return (
        <div style={{ position: 'relative' }}>
            <button onClick={() => setShowHintBox(!showHintBox)} style={{
                height: '24px', width: '24px', padding: '0',
                background: 'transparent',
                color: 'var(--studio-text-muted)',
                border: 'none',
                cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'color 0.2s',
                marginLeft: '4px'
            }} title="Shortcuts & Controls" onMouseEnter={e => e.currentTarget.style.color = 'var(--studio-text)'} onMouseLeave={e => e.currentTarget.style.color = 'var(--studio-text-muted)'}>
                <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/>
                </svg>
            </button>

            {showHintBox && (
                <>
                    {/* Transparent overlay for clicking outside */}
                    <div
                        style={{ position: 'fixed', inset: 0, zIndex: 10999 }}
                        onClick={() => setShowHintBox(false)}
                    />

                    <div style={{
                        position: 'absolute',
                        top: '35px',
                        right: '-10px',
                        width: '280px',
                        backgroundColor: 'var(--studio-surface)',
                        border: '1px solid var(--studio-border)',
                        borderRadius: '6px',
                        padding: '16px',
                        color: 'var(--studio-text)',
                        boxShadow: '0 8px 32px rgba(44, 62, 80, 0.18)',
                        cursor: 'default',
                        zIndex: 11000
                    }} onClick={e => e.stopPropagation()}>
                        {/* Triangle pointer */}
                        <div style={{
                            position: 'absolute',
                            top: '-7px',
                            right: '18px',
                            width: 0,
                            height: 0,
                            borderLeft: '7px solid transparent',
                            borderRight: '7px solid transparent',
                            borderBottom: '7px solid var(--studio-border)',
                        }} />
                        <div style={{
                            position: 'absolute',
                            top: '-6px',
                            right: '19px',
                            width: 0,
                            height: 0,
                            borderLeft: '6px solid transparent',
                            borderRight: '6px solid transparent',
                            borderBottom: '6px solid var(--studio-surface)',
                            zIndex: 1
                        }} />

                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px', borderBottom: '1px solid var(--studio-border)', paddingBottom: '8px' }}>
                            <h3 style={{ margin: 0, fontSize: '14px' }}>Shortcuts & Controls</h3>
                            <button onClick={() => setShowHintBox(false)} style={{ background: 'transparent', border: 'none', color: 'var(--studio-text-muted)', cursor: 'pointer', fontSize: '16px' }}>&times;</button>
                        </div>

                        <div style={{ display: 'grid', gap: '8px', fontSize: '12px' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Add Note</span>
                                <span><kbd>Cmd/Ctrl</kbd> + Click</span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Lasso Select</span>
                                <span>Drag Background</span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Multi-select</span>
                                <span><kbd>Shift</kbd> + Click</span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Context Menu</span>
                                <span>Right Click</span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Replicate</span>
                                <span><kbd>Shift</kbd> + Drag</span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Join Notes</span>
                                <span><kbd>J</kbd></span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Disable / Restore</span>
                                <span><kbd>D</kbd></span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Delete Note(s)</span>
                                <span><kbd>Backspace / Del</kbd></span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span style={{ color: 'var(--studio-text-muted)' }}>Undo</span>
                                <span><kbd>Cmd/Ctrl</kbd> + <kbd>Z</kbd></span>
                            </div>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}

/** Confirmation only; the popup owns the revert, selection reset, and Escape handler. */
export function MidiEditorRevertDialog({
    isRevertConfirmationOpen,
    setIsRevertConfirmationOpen,
    trackName,
    confirmRevertMidi,
}) {
    return (
        isRevertConfirmationOpen && (
            <div
                role="presentation"
                onMouseDown={() => setIsRevertConfirmationOpen(false)}
                style={{
                    position: 'fixed', inset: 0, zIndex: 10,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    backgroundColor: 'rgba(37, 52, 70, 0.28)', padding: '20px'
                }}
            >
                <section
                    role="alertdialog"
                    aria-modal="true"
                    aria-labelledby="revert-midi-title"
                    aria-describedby="revert-midi-description"
                    onMouseDown={(event) => event.stopPropagation()}
                    style={{
                        width: 'min(420px, 100%)', backgroundColor: 'var(--studio-surface)', color: 'var(--studio-text)',
                        border: '1px solid var(--studio-border)', borderRadius: '8px', padding: '20px',
                        boxShadow: '0 18px 50px rgba(44, 62, 80, 0.22)'
                    }}
                >
                    <h4 id="revert-midi-title" style={{ margin: '0 0 10px', fontSize: '18px' }}>
                        Revert {trackName} MIDI?
                    </h4>
                    <p id="revert-midi-description" style={{ margin: '0 0 20px', color: 'var(--studio-text-secondary)', fontSize: '14px', lineHeight: 1.45 }}>
                        This restores the generated MIDI for this track and discards the current edits. You can undo the revert afterward.
                    </p>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
                        <button
                            type="button"
                            onClick={() => setIsRevertConfirmationOpen(false)}
                            style={{
                                padding: '7px 12px', background: 'transparent', color: 'var(--studio-text)', border: '1px solid var(--studio-border-strong)',
                                borderRadius: '4px', cursor: 'pointer', fontWeight: '600'
                            }}
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            onClick={confirmRevertMidi}
                            style={{
                                padding: '7px 12px', background: '#b63737', color: '#fff', border: '1px solid #dd5757',
                                borderRadius: '4px', cursor: 'pointer', fontWeight: '700'
                            }}
                        >
                            Revert MIDI
                        </button>
                    </div>
                </section>
            </div>
        )
    );
}
