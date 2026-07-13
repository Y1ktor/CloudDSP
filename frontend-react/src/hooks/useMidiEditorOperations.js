import React, { useState, useEffect } from 'react';

export function useMidiEditorOperations({
    trackName,
    parsedMidiStems,
    setParsedMidiStems,
    selectedNoteIndices,
    setSelectedNoteIndices,
    pushUndoState,
    activeBpm,
    parsedBeatsPerBar,
    popupPixelsPerBar,
    popupRowHeight,
    auditionNote
}) {
    // Drag selection state
    const [isDraggingSelection, setIsDraggingSelection] = useState(false);
    const [selectionStart, setSelectionStart] = useState({ x: 0, y: 0 });
    const [selectionRect, setSelectionRect] = useState(null); // {x, y, w, h}
    const [preDragSelection, setPreDragSelection] = useState(new Set());
    const [noteDragState, setNoteDragState] = useState(null);

    // Note Deletion via Backspace/Delete
    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            if (e.key === 'Backspace' || e.key === 'Delete') {
                if (selectedNoteIndices.size > 0 && parsedMidiStems && parsedMidiStems[trackName]) {
                    e.preventDefault();
                    if (pushUndoState) pushUndoState();
                    const notes = parsedMidiStems[trackName].midiData.tracks[0].notes;
                    
                    const indicesToRemove = Array.from(selectedNoteIndices).sort((a, b) => b - a);
                    indicesToRemove.forEach(idx => notes.splice(idx, 1));
                    
                    setSelectedNoteIndices(new Set());
                    setParsedMidiStems({ ...parsedMidiStems });
                }
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [selectedNoteIndices, parsedMidiStems, trackName, pushUndoState, setParsedMidiStems, setSelectedNoteIndices]);

    // Velocity Control Logic
    let commonVelocity = 0.8;
    if (selectedNoteIndices.size === 1 && parsedMidiStems && parsedMidiStems[trackName] && parsedMidiStems[trackName].midiData.tracks[0].notes.length > 0) {
        const selectedIndex = Array.from(selectedNoteIndices)[0];
        const note = parsedMidiStems[trackName].midiData.tracks[0].notes[selectedIndex];
        if (note) {
            commonVelocity = note.velocity !== undefined ? Math.max(0.01, note.velocity) : 0.8;
        }
    }

    const handleVelocityChange = (e) => {
        const newVelocity = parseFloat(e.target.value);
        if (selectedNoteIndices.size !== 1 || !parsedMidiStems) return;

        const selectedIndex = Array.from(selectedNoteIndices)[0];
        const note = parsedMidiStems[trackName].midiData.tracks[0].notes[selectedIndex];
        if (note) {
            note.velocity = newVelocity;
        }

        setParsedMidiStems({ ...parsedMidiStems });
    };

    // Disable / Restore Notes Logic
    let allDisabled = false;
    let selectedNotesForDisable = [];
    if (parsedMidiStems && parsedMidiStems[trackName] && selectedNoteIndices.size > 0) {
        const notes = parsedMidiStems[trackName].midiData.tracks[0].notes;
        selectedNotesForDisable = Array.from(selectedNoteIndices).map(idx => notes[idx]);
        allDisabled = selectedNotesForDisable.every(note => note.velocity !== undefined && note.velocity <= 0.015);
    }

    const handleToggleDisable = () => {
        if (pushUndoState) pushUndoState();
        selectedNotesForDisable.forEach(note => {
            note.velocity = allDisabled ? 0.8 : 0.01;
        });
        setParsedMidiStems({ ...parsedMidiStems });
    };

    // Join Notes Logic
    const canJoinSelectedNotes = () => {
        if (!parsedMidiStems || !parsedMidiStems[trackName]) return false;
        if (selectedNoteIndices.size < 2) return false;

        const notes = parsedMidiStems[trackName].midiData.tracks[0].notes;
        const selected = Array.from(selectedNoteIndices).map(idx => {
            const note = notes[idx];
            return { ...note, time: note.time, duration: note.duration, index: idx };
        });
        selected.sort((a, b) => a.time - b.time);
        
        const firstMidi = selected[0].midi;
        if (!selected.every(n => n.midi === firstMidi)) return false;
        
        for (let i = 0; i < selected.length - 1; i++) {
            const currentEnd = selected[i].time + selected[i].duration;
            if (currentEnd < selected[i+1].time - 0.05) {
                return false;
            }
        }
        return true;
    };

    const canJoin = canJoinSelectedNotes();

    const handleJoinNotes = () => {
        if (pushUndoState) pushUndoState();
        const notes = parsedMidiStems[trackName].midiData.tracks[0].notes;
        const selected = Array.from(selectedNoteIndices).map(idx => {
            const note = notes[idx];
            return { ...note, time: note.time, duration: note.duration, index: idx };
        });
        selected.sort((a, b) => a.time - b.time);
        
        const firstNote = selected[0];
        const lastNote = selected[selected.length - 1];
        
        const noteToKeep = notes[firstNote.index];
        noteToKeep.duration = (lastNote.time + lastNote.duration) - firstNote.time;
        noteToKeep.velocity = Math.max(...selected.map(n => n.velocity !== undefined ? n.velocity : 0.8));
        
        const indicesToRemove = selected.slice(1).map(n => n.index).sort((a, b) => b - a);
        indicesToRemove.forEach(idx => notes.splice(idx, 1));
        
        setSelectedNoteIndices(new Set([notes.indexOf(noteToKeep)]));
        setParsedMidiStems({ ...parsedMidiStems });
    };

    // Note Selection & Dragging (Mouse Handlers)
    const getNoteLayout = (note, index) => {
        const noteStartBeats = note.time * (activeBpm / 60);
        const noteStartBars = noteStartBeats / parsedBeatsPerBar;
        const leftPx = noteStartBars * popupPixelsPerBar;

        const noteDurationBeats = note.duration * (activeBpm / 60);
        const noteDurationBars = noteDurationBeats / parsedBeatsPerBar;
        const widthPx = Math.max(2, noteDurationBars * popupPixelsPerBar);

        const topPx = (127 - note.midi) * popupRowHeight;
        
        return { index, left: leftPx, top: topPx, right: leftPx + widthPx, bottom: topPx + popupRowHeight };
    };

    const handleGridMouseDown = (e) => {
        if (e.target !== e.currentTarget) return; 
        
        const rect = e.currentTarget.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        setIsDraggingSelection(true);
        setSelectionStart({ x, y });
        setSelectionRect({ x, y, w: 0, h: 0 });
        
        if (e.shiftKey) {
            setPreDragSelection(new Set(selectedNoteIndices));
        } else {
            setPreDragSelection(new Set());
            setSelectedNoteIndices(new Set());
        }
    };

    const handleGridMouseMove = (e) => {
        if (noteDragState) {
            const deltaX = e.clientX - noteDragState.startX;
            const deltaY = e.clientY - noteDragState.startY;

            if (!noteDragState.hasMoved && (Math.abs(deltaX) > 2 || Math.abs(deltaY) > 2)) {
                if (pushUndoState) pushUndoState();
                setNoteDragState(prev => ({ ...prev, hasMoved: true }));
            }

            if (!parsedMidiStems || !parsedMidiStems[trackName]) return;
            const notes = parsedMidiStems[trackName].midiData.tracks[0].notes;

            const deltaBars = deltaX / popupPixelsPerBar;
            const deltaBeats = deltaBars * parsedBeatsPerBar;
            let deltaTime = deltaBeats / (activeBpm / 60);

            const deltaPitch = -Math.round(deltaY / popupRowHeight);

            const snapThresholdPx = 6;
            const snapThresholdTime = (snapThresholdPx / popupPixelsPerBar) * parsedBeatsPerBar / (activeBpm / 60);
            
            const clickedOriginal = noteDragState.originalNotes.find(n => n.index === noteDragState.clickedNoteIndex);
            if (clickedOriginal) {
                const targetPitch = clickedOriginal.originalMidi + deltaPitch;
                const rawNewTime = clickedOriginal.originalTime + deltaTime;
                const noteDuration = notes[clickedOriginal.index].duration;

                let closestSnapDeltaTime = null;
                let minDistanceTime = Infinity;

                notes.forEach((neighborNote, neighborIdx) => {
                    const isDragged = noteDragState.originalNotes.some(n => n.index === neighborIdx);
                    if (isDragged) return;
                    
                    if (neighborNote.midi === targetPitch) {
                        const neighborStart = neighborNote.time;
                        const neighborEnd = neighborNote.time + neighborNote.duration;

                        const distRightToLeft = Math.abs((rawNewTime + noteDuration) - neighborStart);
                        if (distRightToLeft < minDistanceTime && distRightToLeft < snapThresholdTime) {
                            minDistanceTime = distRightToLeft;
                            closestSnapDeltaTime = neighborStart - noteDuration - clickedOriginal.originalTime;
                        }

                        const distLeftToRight = Math.abs(rawNewTime - neighborEnd);
                        if (distLeftToRight < minDistanceTime && distLeftToRight < snapThresholdTime) {
                            minDistanceTime = distLeftToRight;
                            closestSnapDeltaTime = neighborEnd - clickedOriginal.originalTime;
                        }
                    }
                });

                if (closestSnapDeltaTime !== null) {
                    deltaTime = closestSnapDeltaTime;
                }
            }

            noteDragState.originalNotes.forEach(orig => {
                const note = notes[orig.index];
                if (note) {
                    note.time = Math.max(0, orig.originalTime + deltaTime);
                    note.midi = Math.max(0, Math.min(127, orig.originalMidi + deltaPitch));
                }
            });

            setParsedMidiStems({ ...parsedMidiStems });
            return;
        }

        if (!isDraggingSelection) return;
        const rect = e.currentTarget.getBoundingClientRect();
        const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
        const y = Math.max(0, Math.min(e.clientY - rect.top, rect.height));
        
        const sx = Math.min(selectionStart.x, x);
        const sy = Math.min(selectionStart.y, y);
        const sw = Math.abs(x - selectionStart.x);
        const sh = Math.abs(y - selectionStart.y);
        
        setSelectionRect({ x: sx, y: sy, w: sw, h: sh });
        
        if (!parsedMidiStems || !parsedMidiStems[trackName]) return;
        const notes = parsedMidiStems[trackName].midiData.tracks[0].notes;
        
        const newSelection = new Set(preDragSelection);
        notes.forEach((note, index) => {
            const layout = getNoteLayout(note, index);
            const overlapsX = layout.left < sx + sw && layout.right > sx;
            const overlapsY = layout.top < sy + sh && layout.bottom > sy;
            if (overlapsX && overlapsY) {
                newSelection.add(index);
            }
        });
        
        setSelectedNoteIndices(newSelection);
    };

    const handleGridMouseUp = () => {
        if (noteDragState) {
            setNoteDragState(null);
        }
        if (isDraggingSelection) {
            setIsDraggingSelection(false);
            setSelectionRect(null);
        }
    };

    const handleNoteMouseDown = (index, e) => {
        e.stopPropagation();
        let newSelection = new Set(selectedNoteIndices);
        
        if (!newSelection.has(index)) {
            if (!e.shiftKey) {
                newSelection.clear();
            }
            newSelection.add(index);
            setSelectedNoteIndices(newSelection);
            
            const stemData = parsedMidiStems[trackName];
            if (stemData && stemData.midiData && stemData.midiData.tracks && stemData.midiData.tracks[0].notes) {
                const note = stemData.midiData.tracks[0].notes[index];
                if (note && auditionNote) {
                    auditionNote(note);
                }
            }
        } else {
            if (e.shiftKey) {
                newSelection.delete(index);
                setSelectedNoteIndices(newSelection);
                return; 
            }
        }
        
        const stemData = parsedMidiStems[trackName];
        if (!stemData) return;
        const notes = stemData.midiData.tracks[0].notes;
        const originalNotes = Array.from(newSelection).map(idx => ({
            index: idx,
            originalTime: notes[idx].time,
            originalMidi: notes[idx].midi
        }));
        
        setNoteDragState({
            isDragging: true,
            hasMoved: false,
            startX: e.clientX,
            startY: e.clientY,
            originalNotes,
            clickedNoteIndex: index
        });
    };

    return {
        selectionRect,
        isDraggingSelection,
        noteDragState,
        commonVelocity,
        canJoin,
        allDisabled,
        handleVelocityChange,
        handleToggleDisable,
        handleJoinNotes,
        handleGridMouseDown,
        handleGridMouseMove,
        handleGridMouseUp,
        handleNoteMouseDown
    };
}
