import React from 'react';

/** Menu actions invoke the existing MIDI operations, then close the menu. */
export default function MidiEditorContextMenu({
    contextMenu,
    undoStackLength,
    handleUndoMidi,
    closeContextMenu,
    selectedNoteIndices,
    handleToggleDisable,
    allDisabled,
    handleDeleteNotes,
    canJoin,
    handleJoinNotes,
    handleAddNote,
    handleExportMidi,
    handleExportCycleRange,
}) {
    return (
        contextMenu && (
            <div
                style={{
                    position: 'fixed',
                    left: `${contextMenu.x}px`,
                    top: `${contextMenu.y}px`,
                    backgroundColor: 'var(--studio-surface)',
                    border: '1px solid var(--studio-border)',
                    borderRadius: '4px',
                    padding: '4px 0',
                    boxShadow: '0 4px 12px rgba(44, 62, 80, 0.18)',
                    zIndex: 10000,
                    color: 'var(--studio-text)',
                    minWidth: '150px',
                    fontSize: '13px'
                }}
                onContextMenu={(e) => e.preventDefault()}
            >
                <div
                    style={{
                        padding: '8px 16px',
                        cursor: undoStackLength > 0 ? 'pointer' : 'default',
                        opacity: undoStackLength > 0 ? 1 : 0.4,
                        color: undoStackLength > 0 ? 'var(--studio-text)' : 'var(--studio-text-muted)',
                        borderBottom: '1px solid var(--studio-border)',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center'
                    }}
                    onClick={() => {
                        if (undoStackLength > 0 && handleUndoMidi) handleUndoMidi();
                        closeContextMenu();
                    }}
                    onMouseEnter={(e) => { if (undoStackLength > 0) e.target.style.backgroundColor = 'var(--studio-accent-soft)' }}
                    onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                >
                    <span>Undo</span>
                    <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px' }}>Cmd/Ctrl+Z</span>
                </div>

                {selectedNoteIndices.size > 0 && (
                    <>
                        <div
                            style={{ padding: '8px 16px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                            onClick={() => { handleToggleDisable(); closeContextMenu(); }}
                            onMouseEnter={(e) => e.target.style.backgroundColor = 'var(--studio-accent-soft)'}
                            onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                        >
                            <span>{allDisabled ? "Restore" : "Disable"}</span>
                            <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px', color: 'var(--studio-text-muted)' }}>D</span>
                        </div>
                        <div
                            style={{ padding: '8px 16px', cursor: 'pointer', color: '#e53935', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                            onClick={() => { handleDeleteNotes(); closeContextMenu(); }}
                            onMouseEnter={(e) => e.target.style.backgroundColor = 'var(--studio-accent-soft)'}
                            onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                        >
                            <span>Delete</span>
                            <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px', color: 'var(--studio-text-muted)' }}>⌫ / Del</span>
                        </div>
                        {canJoin && (
                            <div
                                style={{ padding: '8px 16px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                                onClick={() => { handleJoinNotes(); closeContextMenu(); }}
                                onMouseEnter={(e) => e.target.style.backgroundColor = 'var(--studio-accent-soft)'}
                                onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                            >
                                <span>Join</span>
                                <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px', color: 'var(--studio-text-muted)' }}>J</span>
                            </div>
                        )}
                        <div style={{ height: '1px', backgroundColor: 'var(--studio-border)', margin: '4px 0' }} />
                    </>
                )}

                {selectedNoteIndices.size === 0 && contextMenu.gridX !== undefined && (
                    <>
                        <div
                            style={{ padding: '8px 16px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                            onClick={() => { handleAddNote(contextMenu.gridX, contextMenu.gridY); closeContextMenu(); }}
                            onMouseEnter={(e) => e.target.style.backgroundColor = 'var(--studio-accent-soft)'}
                            onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                        >
                            <span>Add Note</span>
                            <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px' }}>Cmd/Ctrl+Click</span>
                        </div>
                        <div style={{ height: '1px', backgroundColor: 'var(--studio-border)', margin: '4px 0' }} />
                    </>
                )}

                <div
                    style={{ padding: '8px 16px', cursor: 'pointer' }}
                    onClick={() => { handleExportMidi(); closeContextMenu(); }}
                    onMouseEnter={(e) => e.target.style.backgroundColor = 'var(--studio-accent-soft)'}
                    onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                >
                    Export MIDI
                </div>
                <div
                    style={{ padding: '8px 16px', cursor: 'pointer' }}
                    onClick={() => { handleExportCycleRange(); closeContextMenu(); }}
                    onMouseEnter={(e) => e.target.style.backgroundColor = 'var(--studio-accent-soft)'}
                    onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                >
                    Export Cycle Range
                </div>
            </div>
        )
    );
}
