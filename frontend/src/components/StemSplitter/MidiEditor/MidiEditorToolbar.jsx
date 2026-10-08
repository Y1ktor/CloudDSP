/**
 * Assembles MIDI editor track status, playback, audition, undo, revert, help,
 * and zoom controls.
 */
import React from 'react';
import MidiEditorNoteControls from './MidiEditorNoteControls';
import { MidiEditorHint } from './MidiEditorDialogs';

/** Track identity, transport, audition mode, edit history, help, and popup controls. */
export default function MidiEditorToolbar({
    tempoControls,
    isAdtofDrum,
    trackName,
    isMidiPending,
    isMidiFailed,
    onClose,
    handleGoToBeginning,
    unlockAudio,
    togglePlay,
    isPlaying,
    toggleCycling,
    isCycling,
    toggleMute,
    mutedTracks,
    toggleSolo,
    soloedTracks,
    setIsMidiMode,
    isMidiMode,
    handleUndoMidi,
    undoStackLength,
    handleRevertMidi,
    setIsRevertConfirmationOpen,
    showHintBox,
    setShowHintBox,
    selectedNoteIndices,
    commonVelocity,
    handleVelocityChange,
    pushUndoState,
    popupPixelsPerBar,
    setPopupPixelsPerBar,
    popupRowHeight,
    setPopupRowHeight,
}) {
    return (
        <>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <h3 style={{ margin: 0, color: 'var(--studio-text)', textTransform: 'capitalize', fontSize: '24px' }}>
                        {isAdtofDrum ? 'Drum Editor' : 'MIDI Editor'}: {trackName}
                    </h3>
                    {isAdtofDrum && (
                        <span style={{ color: '#9fa8da', fontSize: '12px', fontWeight: '600' }}>
                            ADTOF kit: Kick · Snare · Tom · Hi-hat · Cymbal
                        </span>
                    )}
                    {(isMidiPending || isMidiFailed) && (
                        <span style={{
                            color: isMidiFailed ? '#a93845' : '#8b5a00', background: isMidiFailed ? 'var(--studio-danger-soft)' : 'var(--studio-warning-soft)',
                            border: `1px solid ${isMidiFailed ? '#f0b4bb' : '#eed49c'}`, borderRadius: '999px',
                            padding: '4px 9px', fontSize: '12px', fontWeight: '600'
                        }}>
                            {isMidiFailed ? 'MIDI failed' : 'MIDI processing'}
                        </span>
                    )}
                </div>
                <button
                    onClick={onClose}
                    style={{
                        background: 'transparent', color: 'var(--studio-text-muted)', border: 'none',
                        cursor: 'pointer', fontSize: '28px', lineHeight: 1
                    }}
                >
                    &times;
                </button>
            </div>

            {/* Control Bar */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: tempoControls ? '12px' : '30px',
                flexWrap: tempoControls ? 'wrap' : undefined,
                backgroundColor: 'var(--studio-surface-raised)', padding: '10px 15px',
                borderRadius: '8px', marginBottom: '15px', border: '1px solid var(--studio-border)'
            }}>
                {/* Transport Controls */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                    <button title="Go to Beginning" onClick={handleGoToBeginning} style={{
                        background: 'transparent', color: 'var(--studio-text)', border: 'none',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                    }}>
                        <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
                    </button>

                    {/* Safari needs audio unlocking in this direct pointer gesture. */}
                    <button
                        title="Play/Pause"
                        onPointerDown={unlockAudio}
                        onClick={togglePlay}
                        style={{
                        background: 'transparent', color: 'var(--studio-text)', border: 'none',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                    }}>
                        {isPlaying ? (
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                        ) : (
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                        )}
                    </button>

                    <button title="Toggle Cycle" onClick={toggleCycling} style={{
                        background: isCycling ? '#a56a00' : 'transparent',
                        color: isCycling ? 'white' : 'var(--studio-text)',
                        border: 'none', borderRadius: '4px',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '2px',
                        opacity: 0.8,
                        transition: 'background-color 0.2s'
                    }}>
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
                    </button>
                </div>

                {tempoControls}

                <div style={{ width: '1px', height: '24px', backgroundColor: 'var(--studio-border)' }}></div>

                {/* Mute and Solo Controls */}
                <div style={{ display: 'flex', gap: '8px' }}>
                    <button onClick={() => toggleMute(trackName)} style={{
                        width: '24px', height: '24px',
                        background: mutedTracks[trackName] ? '#d44b55' : 'var(--studio-control)',
                        color: mutedTracks[trackName] ? 'white' : 'var(--studio-text-secondary)', border: 'none', borderRadius: '4px',
                        cursor: 'pointer', fontSize: '11px', fontWeight: 'bold',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'background-color 0.2s'
                    }} title="Mute Track">
                        M
                    </button>
                    <button onClick={() => toggleSolo(trackName)} style={{
                        width: '24px', height: '24px',
                        background: soloedTracks[trackName] ? '#c88a12' : 'var(--studio-control)',
                        color: soloedTracks[trackName] ? 'white' : 'var(--studio-text-secondary)',
                        border: 'none', borderRadius: '4px',
                        cursor: 'pointer', fontSize: '11px', fontWeight: 'bold',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'background-color 0.2s'
                    }} title="Solo Track">
                        S
                    </button>
                </div>

                <div style={{ width: '1px', height: '24px', backgroundColor: 'var(--studio-border)', marginLeft: '10px', marginRight: '10px' }}></div>

                {/* MIDI Play Mode Toggle */}
                <button onClick={() => setIsMidiMode(!isMidiMode)} style={{
                    height: '24px', padding: '0 10px',
                    background: 'transparent',
                    color: isMidiMode ? 'var(--studio-midi)' : 'var(--studio-text-muted)',
                    border: `1px solid ${isMidiMode ? 'var(--studio-midi)' : 'var(--studio-border-strong)'}`,
                    boxShadow: isMidiMode ? '0 0 8px rgba(37, 137, 92, 0.2)' : 'none',
                    borderRadius: '4px',
                    cursor: 'pointer', fontSize: '12px', fontWeight: 'bold',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.2s'
                }} title="Toggle MIDI Synthesis Playback">
                    MIDI
                </button>



                {/* Undo MIDI Button */}
                <button onClick={() => {
                    if (handleUndoMidi && undoStackLength > 0) handleUndoMidi();
                }} style={{
                    height: '24px', padding: '0 10px',
                    background: 'transparent',
                    color: undoStackLength > 0 ? 'var(--studio-text)' : 'var(--studio-text-muted)',
                    border: `1px solid ${undoStackLength > 0 ? 'var(--studio-border-strong)' : 'var(--studio-border)'}`,
                    borderRadius: '4px',
                    cursor: undoStackLength > 0 ? 'pointer' : 'default', fontSize: '12px', fontWeight: 'bold',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.2s',
                    marginLeft: '10px',
                    opacity: undoStackLength > 0 ? 0.8 : 0.5
                }} title="Undo last edit" disabled={undoStackLength === 0}>
                    Undo
                </button>

                {/* Revert MIDI Button */}
                <button onClick={() => {
                    if (handleRevertMidi) setIsRevertConfirmationOpen(true);
                }} style={{
                    height: '24px', padding: '0 10px',
                    background: 'transparent',
                    color: 'var(--studio-text)',
                    border: '1px solid var(--studio-border-strong)',
                    borderRadius: '4px',
                    cursor: 'pointer', fontSize: '12px', fontWeight: 'bold',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.2s',
                    marginLeft: '10px',
                    opacity: 0.8
                }} title="Revert to Original MIDI">
                    Revert
                </button>

                <MidiEditorHint showHintBox={showHintBox} setShowHintBox={setShowHintBox} />

                {/* Spacer to push sliders to the right */}
                <div style={{ flexGrow: 1 }}></div>

                <MidiEditorNoteControls
                    selectedNoteIndices={selectedNoteIndices}
                    commonVelocity={commonVelocity}
                    handleVelocityChange={handleVelocityChange}
                    pushUndoState={pushUndoState}
                    popupPixelsPerBar={popupPixelsPerBar}
                    setPopupPixelsPerBar={setPopupPixelsPerBar}
                    isAdtofDrum={isAdtofDrum}
                    popupRowHeight={popupRowHeight}
                    setPopupRowHeight={setPopupRowHeight}
                />
            </div>
        </>
    );
}
