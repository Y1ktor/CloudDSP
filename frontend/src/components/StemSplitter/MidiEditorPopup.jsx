import React from 'react';
import TimelineRuler from './TimelineRuler';
import TransportPlayheadLine from './TransportPlayheadLine';
import MidiEditorToolbar from './MidiEditor/MidiEditorToolbar';
import { MidiEditorRevertDialog } from './MidiEditor/MidiEditorDialogs';
import MidiEditorContextMenu from './MidiEditor/MidiEditorContextMenu';
import MidiEditorKeyboard, { MidiEditorKeyboardStyles } from './MidiEditor/MidiEditorKeyboard';
import MidiEditorDragPreview, { MidiEditorSelectionRectangle } from './MidiEditor/MidiEditorDragPreview';
import VisibleMidiEditorNotes from './MidiEditor/VisibleMidiEditorNotes';
import { DRUM_EDITOR_ROW_HEIGHT } from './MidiEditor/midiEditorLayout';
import { useMidiEditorPopup } from './MidiEditor/useMidiEditorPopup';

/**
 * Compose the MIDI editor's toolbar, keyboard, timeline, notes, and dialogs.
 * The workspace mounts this popup only while a track is open. Session state,
 * editing handlers, and audio-clock playheads live in useMidiEditorPopup;
 * the public props and shared melodic/drum editing behavior stay unchanged.
 */
export default function MidiEditorPopup({
    trackName,
    onClose,
    duration,
    pixelsPerBar,
    totalBars,
    playheadX,
    cycleDragRef,
    cycleRegion,
    isCycling,
    timeSignature,
    playheadDragRef,
    isPlayheadHovered,
    setIsPlayheadHovered,
    handleGoToBeginning,
    isPlaying,
    unlockAudio,
    togglePlay,
    toggleCycling,
    mutedTracks,
    soloedTracks,
    toggleMute,
    toggleSolo,
    drumMutedVoices = {},
    drumSoloedVoices = {},
    toggleDrumMute,
    toggleDrumSolo,
    drumVoiceGainsDb = {},
    setDrumVoiceGainDb,
    activeBpm,
    parsedBeatsPerBar,
    handleSeek,
    parsedMidiStems,
    midiStatus,
    setParsedMidiStems,
    audioCtxRef,
    transportRef,
    synthRef,
    isMidiMode,
    setIsMidiMode,
    handleRevertMidi,
    handleUndoMidi,
    pushUndoState,
    undoStackLength,
    fileName
}) {
    const {
        popupTimelineRef,
        pianoScrollRef,
        popupTimelinePlayheadRef,
        popupRulerPlayheadRef,
        popupVisibleTimelineRange,
        setPopupGridScrollContainer,
        isAdtofDrum,
        isMidiPending,
        isMidiFailed,
        midiPendingLabel,
        popupPixelsPerBar,
        setPopupPixelsPerBar,
        popupRowHeight,
        setPopupRowHeight,
        selectedNoteIndices,
        showHintBox,
        setShowHintBox,
        isRevertConfirmationOpen,
        setIsRevertConfirmationOpen,
        contextMenu,
        setContextMenu,
        closeContextMenu,
        isModifierHeld,
        confirmRevertMidi,
        selectionRect,
        isDraggingSelection,
        noteDragState,
        commonVelocity,
        canJoin,
        allDisabled,
        handleVelocityChange,
        handleToggleDisable,
        handleJoinNotes,
        handleAddNote,
        handleDeleteNotes,
        handleGridMouseDown,
        handleGridMouseMove,
        handleGridMouseUp,
        handleExportMidi,
        handleExportCycleRange,
        handleGridScroll,
        handleVisibleNoteMouseDown,
        handleVisibleNoteContextMenu,
        editorMidiData,
        gridHeight,
        popupPlayheadX,
    } = useMidiEditorPopup({
        trackName,
        duration,
        pixelsPerBar,
        totalBars,
        playheadX,
        cycleRegion,
        isPlaying,
        drumMutedVoices,
        drumSoloedVoices,
        activeBpm,
        parsedBeatsPerBar,
        parsedMidiStems,
        midiStatus,
        setParsedMidiStems,
        audioCtxRef,
        transportRef,
        synthRef,
        isMidiMode,
        handleRevertMidi,
        pushUndoState,
        fileName,
    });

    if (!trackName) return null;

    return (
        <div style={{
            position: 'fixed',
            top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: 'var(--studio-page)',
            zIndex: 9999,
            display: 'flex',
            flexDirection: 'column',
            padding: '20px'
        }}>
            <MidiEditorKeyboardStyles />
            <MidiEditorRevertDialog
                isRevertConfirmationOpen={isRevertConfirmationOpen}
                setIsRevertConfirmationOpen={setIsRevertConfirmationOpen}
                trackName={trackName}
                confirmRevertMidi={confirmRevertMidi}
            />
            <MidiEditorToolbar
                isAdtofDrum={isAdtofDrum}
                trackName={trackName}
                isMidiPending={isMidiPending}
                isMidiFailed={isMidiFailed}
                onClose={onClose}
                handleGoToBeginning={handleGoToBeginning}
                unlockAudio={unlockAudio}
                togglePlay={togglePlay}
                isPlaying={isPlaying}
                toggleCycling={toggleCycling}
                isCycling={isCycling}
                toggleMute={toggleMute}
                mutedTracks={mutedTracks}
                toggleSolo={toggleSolo}
                soloedTracks={soloedTracks}
                setIsMidiMode={setIsMidiMode}
                isMidiMode={isMidiMode}
                handleUndoMidi={handleUndoMidi}
                undoStackLength={undoStackLength}
                handleRevertMidi={handleRevertMidi}
                setIsRevertConfirmationOpen={setIsRevertConfirmationOpen}
                showHintBox={showHintBox}
                setShowHintBox={setShowHintBox}
                selectedNoteIndices={selectedNoteIndices}
                commonVelocity={commonVelocity}
                handleVelocityChange={handleVelocityChange}
                pushUndoState={pushUndoState}
                popupPixelsPerBar={popupPixelsPerBar}
                setPopupPixelsPerBar={setPopupPixelsPerBar}
                popupRowHeight={popupRowHeight}
                setPopupRowHeight={setPopupRowHeight}
            />

            {/* Split Canvas Area */}
            <div style={{
                flexGrow: 1,
                backgroundColor: 'var(--studio-surface-muted)',
                borderRadius: '8px',
                border: '1px solid var(--studio-border)',
                overflow: 'hidden',
                display: 'flex',
                flexDirection: 'row'
            }}>
                {/* Left Column: piano keys, or named ADTOF drum lanes */}
                <div style={{ width: isAdtofDrum ? '154px' : '60px', flexShrink: 0, display: 'flex', flexDirection: 'column', backgroundColor: 'var(--studio-surface-sunken)' }}>
                    {/* Empty top left corner to match the 30px TimelineRuler */}
                    <div style={{ height: '30px', backgroundColor: 'var(--studio-surface-raised)', borderBottom: '1px solid var(--studio-border)', borderRight: '1px solid var(--studio-border)', flexShrink: 0 }} />

                    {/* The keys themselves (sync scrolled) */}
                    <div ref={pianoScrollRef} style={{ flexGrow: 1, overflow: 'hidden', opacity: isAdtofDrum ? 1 : 0.6 }}>
                        <div style={{ height: `${gridHeight}px` }}>
                            <MidiEditorKeyboard
                                isAdtofDrum={isAdtofDrum}
                                trackName={trackName}
                                popupRowHeight={popupRowHeight}
                                drumMutedVoices={drumMutedVoices}
                                drumSoloedVoices={drumSoloedVoices}
                                toggleDrumMute={toggleDrumMute}
                                toggleDrumSolo={toggleDrumSolo}
                                drumVoiceGainsDb={drumVoiceGainsDb}
                                setDrumVoiceGainDb={setDrumVoiceGainDb}
                            />
                        </div>
                    </div>
                </div>

                {/* Right Column: Scrollable Grid */}
                <div
                    ref={setPopupGridScrollContainer}
                    style={{ flexGrow: 1, overflow: 'auto', position: 'relative', backgroundColor: 'var(--studio-surface-raised)' }}
                    onScroll={handleGridScroll}
                >
                    {(isMidiPending || isMidiFailed) && (
                        <div style={{
                            position: 'absolute', inset: 0, zIndex: 30, display: 'flex',
                            alignItems: 'center', justifyContent: 'center', textAlign: 'center',
                            color: isMidiFailed ? 'var(--studio-danger)' : 'var(--studio-warning)',
                            backgroundColor: 'rgba(233, 239, 244, 0.94)',
                            padding: '24px', pointerEvents: 'auto'
                        }}>
                            <div>
                                <div aria-hidden="true" style={{ fontSize: '20px', marginBottom: '8px' }}>{isMidiFailed ? '!' : '●'}</div>
                                <strong style={{ display: 'block', color: 'var(--studio-text)', marginBottom: '4px' }}>{isMidiFailed ? 'MIDI unavailable' : 'MIDI not ready yet'}</strong>
                                <span style={{ fontSize: '13px' }}>{midiPendingLabel}</span>
                            </div>
                        </div>
                    )}
                    <div
                        ref={popupTimelineRef}
                        style={{
                            minWidth: `${popupPixelsPerBar * totalBars}px`,
                            minHeight: `${gridHeight + 30}px`, // 30px for ruler
                            position: 'relative'
                        }}
                    >
                        {/* Time Indicator (Playhead) */}
                        {duration > 0 && (
                            <TransportPlayheadLine
                                playheadRef={popupTimelinePlayheadRef}
                                fallbackX={popupPlayheadX}
                                isPlaying={isPlaying}
                            />
                        )}

                        {/* Ruler */}
                        <div style={{ position: 'sticky', top: 0, zIndex: 20 }}>
                            <TimelineRuler
                                duration={duration}
                                pixelsPerBar={popupPixelsPerBar}
                                cycleDragRef={cycleDragRef}
                                cycleRegion={cycleRegion}
                                isCycling={isCycling}
                                totalBars={totalBars}
                                timeSignature={timeSignature}
                                timelineRef={popupTimelineRef} // Use local ref!
                                playheadDragRef={playheadDragRef}
                                setIsPlayheadHovered={setIsPlayheadHovered}
                                isPlayheadHovered={isPlayheadHovered}
                                playheadX={popupPlayheadX}
                                playheadElementRef={popupRulerPlayheadRef}
                                isPlayheadExternallyDriven={isPlaying}
                                visibleRange={popupVisibleTimelineRange}
                                activeBpm={activeBpm}
                                parsedBeatsPerBar={parsedBeatsPerBar}
                                handleSeek={handleSeek}
                            />
                        </div>

                        {/* MIDI Grid */}
                        <div
                            onMouseDown={handleGridMouseDown}
                            onMouseMove={handleGridMouseMove}
                            onMouseUp={handleGridMouseUp}
                            onMouseLeave={handleGridMouseUp}
                            onContextMenu={(e) => {
                                e.preventDefault();
                                const rect = e.currentTarget.getBoundingClientRect();
                                const gridX = e.clientX - rect.left;
                                const gridY = e.clientY - rect.top;
                                setContextMenu({ x: e.clientX, y: e.clientY, gridX, gridY, isNoteSelected: false });
                            }}
                            style={{
                            position: 'relative',
                            width: '100%',
                            height: `${gridHeight}px`,
                            marginTop: '0px',
                            cursor: isModifierHeld ? 'crosshair' : 'default',
                            backgroundColor: 'var(--studio-surface-raised)',
                            backgroundSize: `${popupPixelsPerBar}px 100%, ${popupPixelsPerBar / parsedBeatsPerBar}px 100%, 100% ${isAdtofDrum ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight}px`,
                            backgroundImage: `
                                linear-gradient(to right, transparent ${popupPixelsPerBar - 1}px, var(--studio-grid-major) ${popupPixelsPerBar}px),
                                linear-gradient(to right, transparent ${(popupPixelsPerBar / parsedBeatsPerBar) - 1}px, var(--studio-grid-minor) ${popupPixelsPerBar / parsedBeatsPerBar}px),
                                linear-gradient(to bottom, transparent ${(isAdtofDrum ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight) - 1}px, var(--studio-grid-row) ${isAdtofDrum ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight}px)
                            `
                        }}>
                            <MidiEditorDragPreview
                                noteDragState={noteDragState}
                                parsedMidiStems={parsedMidiStems}
                                trackName={trackName}
                                isAdtofDrum={isAdtofDrum}
                                activeBpm={activeBpm}
                                parsedBeatsPerBar={parsedBeatsPerBar}
                                popupPixelsPerBar={popupPixelsPerBar}
                                popupRowHeight={popupRowHeight}
                            />

                            <VisibleMidiEditorNotes
                                midiData={editorMidiData}
                                midiRevision={parsedMidiStems}
                                isAdtofDrum={isAdtofDrum}
                                activeBpm={activeBpm}
                                parsedBeatsPerBar={parsedBeatsPerBar}
                                popupPixelsPerBar={popupPixelsPerBar}
                                popupRowHeight={popupRowHeight}
                                visibleRange={popupVisibleTimelineRange}
                                selectedNoteIndices={selectedNoteIndices}
                                noteDragState={noteDragState}
                                onNoteMouseDown={handleVisibleNoteMouseDown}
                                onNoteContextMenu={handleVisibleNoteContextMenu}
                            />

                            <MidiEditorSelectionRectangle
                                isDraggingSelection={isDraggingSelection}
                                selectionRect={selectionRect}
                            />
                        </div>
                    </div>
                </div>

                <MidiEditorContextMenu
                    contextMenu={contextMenu}
                    undoStackLength={undoStackLength}
                    handleUndoMidi={handleUndoMidi}
                    closeContextMenu={closeContextMenu}
                    selectedNoteIndices={selectedNoteIndices}
                    handleToggleDisable={handleToggleDisable}
                    allDisabled={allDisabled}
                    handleDeleteNotes={handleDeleteNotes}
                    canJoin={canJoin}
                    handleJoinNotes={handleJoinNotes}
                    handleAddNote={handleAddNote}
                    handleExportMidi={handleExportMidi}
                    handleExportCycleRange={handleExportCycleRange}
                />
            </div>
        </div>
    );
}
