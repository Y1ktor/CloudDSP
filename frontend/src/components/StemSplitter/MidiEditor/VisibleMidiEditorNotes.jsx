import React from 'react';
import { getAdtofDrumVoice, getAdtofDrumVoiceIndex } from '../../../utils/DrumMidi';
import { DRUM_EDITOR_ROW_HEIGHT } from './midiEditorLayout';
import { getVisibleMidiEditorNotes, indexMidiEditorNotes } from './midiEditorNotes';

/**
 * Render only the note events that can intersect the popup viewport. The
 * source MIDI array deliberately remains in its original order: editing,
 * undo, and export all address notes by that array index. A separately sorted
 * index gives the renderer O(log n + visible notes) work instead of scanning a
 * whole song whenever an unrelated transport/readout render occurs.
 */
const VisibleMidiEditorNotes = React.memo(function VisibleMidiEditorNotes({
    midiData,
    midiRevision,
    isAdtofDrum,
    activeBpm,
    parsedBeatsPerBar,
    popupPixelsPerBar,
    popupRowHeight,
    visibleRange,
    selectedNoteIndices,
    noteDragState,
    onNoteMouseDown,
    onNoteContextMenu,
}) {
    const indexedNotes = React.useMemo(() => {
        // Editor operations mutate the nested MIDI instance, then replace the
        // top-level state. That revision rebuilds this index after edits/undo.
        const notes = midiRevision ? (midiData?.tracks?.[0]?.notes || []) : [];
        return indexMidiEditorNotes(notes);
    }, [midiData, midiRevision]);

    const visibleNotes = React.useMemo(() => getVisibleMidiEditorNotes(indexedNotes, {
        activeBpm,
        parsedBeatsPerBar,
        popupPixelsPerBar,
        visibleRange,
    }), [activeBpm, indexedNotes, parsedBeatsPerBar, popupPixelsPerBar, visibleRange]);

    if (visibleNotes.length === 0) return null;

    const pixelsPerSecond = (activeBpm / 60 / parsedBeatsPerBar) * popupPixelsPerBar;
    return visibleNotes.map(({ note, index }) => {
        const leftPx = (Number(note.time) || 0) * pixelsPerSecond;
        const widthPx = Math.max(2, (Number(note.duration) || 0) * pixelsPerSecond);
        const drumVoice = isAdtofDrum ? getAdtofDrumVoice(note.midi) : null;
        if (isAdtofDrum && !drumVoice) return null;

        const topPx = drumVoice
            ? getAdtofDrumVoiceIndex(note.midi) * DRUM_EDITOR_ROW_HEIGHT
            : (127 - note.midi) * popupRowHeight;
        const noteRowHeight = drumVoice ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight;
        const velocity = note.velocity !== undefined ? Math.max(0.01, note.velocity) : 0.8;
        const hue = 280 - (velocity * 280);
        const saturation = Math.round(35 + (velocity * 15));
        const lightness = Math.round(45 + (velocity * 10));
        const isDisabled = note.velocity !== undefined && note.velocity <= 0.015;
        const noteColor = isDisabled
            ? '#94a3b8'
            : drumVoice ? drumVoice.color : `hsl(${Math.round(hue)}, ${saturation}%, ${lightness}%)`;
        const isSelected = selectedNoteIndices.has(index);

        return (
            <div
                key={`popup-note-${index}`}
                draggable={false}
                onDragStart={(event) => event.preventDefault()}
                onMouseDown={(event) => onNoteMouseDown(index, event, 'move')}
                onContextMenu={(event) => onNoteContextMenu(index, event)}
                style={{
                    position: 'absolute',
                    left: `${leftPx}px`,
                    width: `${widthPx}px`,
                    top: `${topPx}px`,
                    height: `${noteRowHeight}px`,
                    backgroundColor: noteColor,
                    borderRadius: '2px',
                    // Box shadows force expensive paint for every note. Keep
                    // the stronger affordance only on the small selected set.
                    boxShadow: isSelected ? '0 0 0 1px rgba(255,255,255,0.6), 0 0 4px rgba(255,255,255,0.4)' : 'none',
                    border: isSelected ? '1px solid rgba(255,255,255,0.6)' : '1px solid rgba(255,255,255,0.2)',
                    boxSizing: 'border-box',
                    userSelect: 'none',
                    cursor: noteDragState && noteDragState.action !== 'move' ? 'ew-resize' : 'move',
                    opacity: isDisabled && !isSelected ? 0.5 : 1,
                    zIndex: isSelected ? 10 : 1,
                }}
                title={`${drumVoice?.label || `Pitch: ${note.name} (${note.midi})`} | Velocity: ${Math.round((note.velocity || 0) * 100)}%`}
            >
                <div
                    onMouseDown={(event) => {
                        event.stopPropagation();
                        onNoteMouseDown(index, event, 'resize-left');
                    }}
                    style={{
                        position: 'absolute', top: 0, bottom: 0, left: '-4px', width: '8px',
                        cursor: 'ew-resize', zIndex: 10,
                    }}
                />
                <div
                    onMouseDown={(event) => {
                        event.stopPropagation();
                        onNoteMouseDown(index, event, 'resize-right');
                    }}
                    style={{
                        position: 'absolute', top: 0, bottom: 0, right: '-4px', width: '8px',
                        cursor: 'ew-resize', zIndex: 10,
                    }}
                />
            </div>
        );
    });
});

export default VisibleMidiEditorNotes;
