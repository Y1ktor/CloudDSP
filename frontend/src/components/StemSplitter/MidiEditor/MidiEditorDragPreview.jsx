/**
 * Draws note replication previews, drag lane highlights, and the selection
 * rectangle in the MIDI editor.
 */
import React from 'react';
import { ADTOF_DRUM_VOICES, getAdtofDrumVoice, getAdtofDrumVoiceIndex } from '../../../utils/DrumMidi';
import { DRUM_EDITOR_ROW_HEIGHT } from './midiEditorLayout';

/** Preview a replicated note group or highlight the current drag lane. */
export default function MidiEditorDragPreview({
    noteDragState,
    parsedMidiStems,
    trackName,
    isAdtofDrum,
    activeBpm,
    parsedBeatsPerBar,
    popupPixelsPerBar,
    popupRowHeight,
}) {
    if (!noteDragState?.hasMoved || !parsedMidiStems?.[trackName]) return null;

    const notes = parsedMidiStems[trackName].midiData.tracks[0].notes;

    if (noteDragState.isReplicating) {
        return noteDragState.originalNotes.map(orig => {
            const noteToClone = notes[orig.index];
            if (!noteToClone) return null;

            const projTime = Math.max(0, orig.originalTime + noteDragState.deltaTime);
            const projMidi = isAdtofDrum
                ? (() => {
                    const sourceIndex = getAdtofDrumVoiceIndex(orig.originalMidi);
                    if (sourceIndex < 0) return orig.originalMidi;
                    const targetIndex = Math.max(0, Math.min(
                        ADTOF_DRUM_VOICES.length - 1,
                        sourceIndex + (noteDragState.drumVoiceDelta || 0)
                    ));
                    return ADTOF_DRUM_VOICES[targetIndex].midi;
                })()
                : Math.max(0, Math.min(127, orig.originalMidi + noteDragState.deltaPitch));

            const noteStartBeats = projTime * (activeBpm / 60);
            const noteStartBars = noteStartBeats / parsedBeatsPerBar;
            const leftPx = noteStartBars * popupPixelsPerBar;

            const noteDurationBeats = orig.originalDuration * (activeBpm / 60);
            const noteDurationBars = noteDurationBeats / parsedBeatsPerBar;
            const widthPx = Math.max(2, noteDurationBars * popupPixelsPerBar);

            const projectedVoice = isAdtofDrum ? getAdtofDrumVoice(projMidi) : null;
            const topPx = projectedVoice
                ? getAdtofDrumVoiceIndex(projMidi) * DRUM_EDITOR_ROW_HEIGHT
                : (127 - projMidi) * popupRowHeight;

            const v = noteToClone.velocity !== undefined ? Math.max(0.01, noteToClone.velocity) : 0.8;
            // Hue: Purple(280) -> Blue -> Cyan -> Green -> Yellow -> Red(0)
            const hue = 280 - (v * 280);
            const saturation = Math.round(35 + (v * 15));
            const lightness = Math.round(45 + (v * 10));
            const isDisabled = v <= 0.015;
            const noteColor = isDisabled ? '#94a3b8' : `hsla(${Math.round(hue)}, ${saturation}%, ${lightness}%, 0.4)`;

            return (
                <div key={`proj-${orig.index}`} style={{
                    position: 'absolute',
                    left: `${leftPx}px`,
                    width: `${widthPx}px`,
                    top: `${topPx}px`,
                    height: `${projectedVoice ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight}px`,
                    backgroundColor: projectedVoice ? `${projectedVoice.color}66` : noteColor,
                    border: '1px solid rgba(255,255,255,0.4)',
                    borderRadius: '2px',
                    boxSizing: 'border-box',
                    pointerEvents: 'none',
                    zIndex: 15
                }} />
            );
        });
    }

    const clickedNote = notes[noteDragState.clickedNoteIndex];
    if (!clickedNote) return null;

    const clickedVoice = isAdtofDrum ? getAdtofDrumVoice(clickedNote.midi) : null;
    const topPx = clickedVoice
        ? getAdtofDrumVoiceIndex(clickedNote.midi) * DRUM_EDITOR_ROW_HEIGHT
        : (127 - clickedNote.midi) * popupRowHeight;
    return (
        <div style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: `${topPx}px`,
            height: `${clickedVoice ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight}px`,
            backgroundColor: 'rgba(255, 255, 255, 0.1)',
            pointerEvents: 'none',
            zIndex: 0
        }} />
    );
}

export function MidiEditorSelectionRectangle({ isDraggingSelection, selectionRect }) {
    return (
        <>
            {/* Drag Selection Rectangle */}
            {isDraggingSelection && selectionRect && (
                <div style={{
                    position: 'absolute',
                    left: `${selectionRect.x}px`,
                    top: `${selectionRect.y}px`,
                    width: `${selectionRect.w}px`,
                    height: `${selectionRect.h}px`,
                    backgroundColor: 'rgba(255, 255, 255, 0.1)',
                    border: '1px solid rgba(255, 255, 255, 0.3)',
                    pointerEvents: 'none',
                    zIndex: 100
                }} />
            )}
        </>
    );
}
