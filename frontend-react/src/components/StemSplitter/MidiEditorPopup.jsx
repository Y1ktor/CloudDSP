import React, { useRef, useState, useEffect } from 'react';
import TimelineRuler from './TimelineRuler';

/**
 * MidiEditorPopup Component
 * 
 * This UI component provides a full-screen, modal piano roll editor for interacting with
 * and modifying parsed MIDI data for a specific track. It features multi-note drag selection, 
 * velocity editing, and real-time auditioning through the Web Audio API.
 * 
 * ARCHITECTURE NOTE:
 * This component utilizes a "State Hoisting" / "Dumb Component" architecture. It holds very little 
 * global state of its own. Global playback state (duration, playheadX, timeSignature), mute/solo 
 * status, and the underlying MIDI data dictionary are managed in `StemSplitter.jsx` and passed down. 
 * Local viewport logic (like horizontal zoom `popupPixelsPerBar`, vertical zoom `popupRowHeight`, 
 * and drag `selectedNoteIndices`) are isolated internally. To mutate MIDI, it mutates the 
 * underlying @tonejs/midi instance directly and forces a top-level React re-render.
 * 
 * @param {Object} props - Component props
 * @param {string} props.trackName - The name of the track currently being edited (e.g., 'piano')
 * @param {Function} props.onClose - Callback fired to close the modal
 * @param {number} props.duration - Total duration of the audio in seconds
 * @param {number} props.pixelsPerBar - The master timeline's horizontal zoom scale
 * @param {number} props.totalBars - Total number of bars calculated from the audio duration
 * @param {number} props.playheadX - The master timeline's playhead position in pixels
 * @param {React.MutableRefObject} props.cycleDragRef - Ref for the master timeline cycle drag interaction
 * @param {Object} props.cycleRegion - The {start, end} points of the master cycle loop in pixels
 * @param {boolean} props.isCycling - Whether master looping is currently active
 * @param {Array<number>} props.timeSignature - Global time signature (e.g., [4, 4])
 * @param {React.MutableRefObject} props.playheadDragRef - Ref for the master timeline playhead drag interaction
 * @param {boolean} props.isPlayheadHovered - Master timeline state indicating playhead hover
 * @param {Function} props.setIsPlayheadHovered - State setter for master playhead hover
 * @param {Function} props.handleGoToBeginning - Callback to seek playback to 0
 * @param {boolean} props.isPlaying - Global playback state
 * @param {Function} props.togglePlay - Callback to toggle global playback
 * @param {Function} props.toggleCycling - Callback to toggle global loop cycling
 * @param {Object} props.mutedTracks - Dictionary of muted track states
 * @param {Object} props.soloedTracks - Dictionary of soloed track states
 * @param {Function} props.toggleMute - Callback to toggle mute for a specific track
 * @param {Function} props.toggleSolo - Callback to toggle solo for a specific track
 * @param {number} props.activeBpm - The current user-adjusted master BPM
 * @param {number} props.originalBpm - The detected original master BPM of the song
 * @param {number} props.parsedBeatsPerBar - Derived variable representing number of beats in a bar
 * @param {Function} props.handleSeek - Callback to seek playback to a specific percentage (0-1)
 * @param {Object} props.parsedMidiStems - The master dictionary containing parsed `@tonejs/midi` class instances
 * @param {Function} props.setParsedMidiStems - State setter for the master MIDI dictionary, used to trigger re-renders
 * @param {React.MutableRefObject} props.audioCtxRef - Reference to the global Web Audio context
 * @param {number} props.progress - The current playback time in seconds
 * @param {React.MutableRefObject} props.synthRef - Reference to the `smplr` instrument for the current track to use for auditioning
 * @param {boolean} props.isMidiMode - Whether MIDI synthesis playback is active for this track
 * @param {Function} props.setIsMidiMode - Callback to toggle MIDI synthesis mode for this track
 * @param {Function} props.handleRevertMidi - Callback to restore the original un-edited MIDI data
 * @param {Function} props.handleUndoMidi - Callback to revert the last MIDI edit from the undo stack
 * @param {Function} props.pushUndoState - Callback to snapshot the current MIDI state onto the undo stack before editing
 * @param {number} props.undoStackLength - The number of snapshots currently in the undo stack for this track
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
    togglePlay,
    toggleCycling,
    mutedTracks,
    soloedTracks,
    toggleMute,
    toggleSolo,
    activeBpm,
    originalBpm,
    parsedBeatsPerBar,
    handleSeek,
    parsedMidiStems,
    setParsedMidiStems,
    audioCtxRef,
    progress,
    synthRef,
    isMidiMode,
    setIsMidiMode,
    handleRevertMidi,
    handleUndoMidi,
    pushUndoState,
    undoStackLength
}) {
    const popupTimelineRef = useRef(null);
    const pianoScrollRef = useRef(null);
    const gridScrollRef = useRef(null);

    // Track drag state


    // Local audition logic for piano keys/notes
    const auditionNote = (note) => {
        if (isMidiMode && synthRef && synthRef.current && audioCtxRef.current) {
            synthRef.current.start({
                note: note.midi,
                velocity: Math.round((note.velocity !== undefined ? note.velocity : 0.8) * 127),
                time: audioCtxRef.current.currentTime,
                duration: 0.5 // Short audition
            });
        }
    };
    
    // Local zoom states for the popup (independent of the main app)
    const [popupPixelsPerBar, setPopupPixelsPerBar] = React.useState(pixelsPerBar || 100);
    const [popupRowHeight, setPopupRowHeight] = React.useState(8); // Default to 8 (lowest)
    
    // Selection state for MIDI notes (multi selection)
    const [selectedNoteIndices, setSelectedNoteIndices] = React.useState(new Set());
    
    // Drag selection state
    const [isDraggingSelection, setIsDraggingSelection] = React.useState(false);
    const [selectionStart, setSelectionStart] = React.useState({ x: 0, y: 0 });
    const [selectionRect, setSelectionRect] = React.useState(null); // {x, y, w, h}
    const [preDragSelection, setPreDragSelection] = React.useState(new Set());
    const [noteDragState, setNoteDragState] = React.useState(null);
    


    // Center scroll on C4 (MIDI 60) when opened
    React.useEffect(() => {
        if (trackName && gridScrollRef.current) {
            // C4 is 67 rows down from the top (127 - 60)
            const c4TopPx = 67 * popupRowHeight;
            const containerHeight = gridScrollRef.current.clientHeight;
            
            // Calculate scroll position to center C4
            const targetScrollTop = Math.max(0, c4TopPx - (containerHeight / 2) + (popupRowHeight / 2));
            
            gridScrollRef.current.scrollTop = targetScrollTop;
            if (pianoScrollRef.current) {
                pianoScrollRef.current.scrollTop = targetScrollTop;
            }
        }
    }, [trackName]); // Only run when popup opens (trackName changes)

    // Handle note deletion via Backspace/Delete
    React.useEffect(() => {
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
    }, [selectedNoteIndices, parsedMidiStems, trackName, pushUndoState, setParsedMidiStems]);


    if (!trackName) return null;

    // Sync vertical scrolling
    const handleGridScroll = (e) => {
        if (pianoScrollRef.current) {
            pianoScrollRef.current.scrollTop = e.target.scrollTop;
        }
    };

    // Render the piano keyboard column with realistic styling
    const renderPianoKeys = () => {
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
                        backgroundColor: '#ddd', // White key background for the right side
                    }}>
                        {/* Middle line to separate the adjacent white keys on the right */}
                        <div style={{
                            position: 'absolute',
                            top: '50%',
                            left: '60%',
                            right: 0,
                            height: '1px',
                            backgroundColor: '#bbb',
                            zIndex: 1
                        }} />
                        
                        {/* The black key itself */}
                        <div style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            width: '60%',
                            height: '100%',
                            backgroundColor: '#1a1a1a',
                            borderBottom: '2px solid #000',
                            borderTop: '1px solid #333',
                            borderRight: '2px solid #000',
                            borderBottomRightRadius: '3px',
                            borderTopRightRadius: '3px',
                            boxSizing: 'border-box',
                            zIndex: 2
                        }} />
                    </div>
                );
            } else {
                // White key row
                // Only C and F need a bottom border because they sit directly above B and E
                const needsBottomBorder = isC || isF;
                
                keys.push(
                    <div key={`key-${i}`} style={{
                        height: `${popupRowHeight}px`,
                        width: '100%',
                        boxSizing: 'border-box',
                        backgroundColor: '#ddd',
                        borderBottom: needsBottomBorder ? '1px solid #bbb' : 'none',
                        color: '#555',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'flex-end',
                        paddingRight: '6px',
                        fontSize: '10px',
                        fontWeight: isC ? 'bold' : 'normal',
                        userSelect: 'none',
                        position: 'relative',
                        zIndex: 3
                    }}>
                        {isC && `C${octave}`}
                    </div>
                );
            }
        }
        return keys;
    };

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

        // CRITICAL: We must directly mutate the @tonejs/midi Note instance.
        const selectedIndex = Array.from(selectedNoteIndices)[0];
        const note = parsedMidiStems[trackName].midiData.tracks[0].notes[selectedIndex];
        if (note) {
            note.velocity = newVelocity;
        }

        // Trigger a React re-render by shallow cloning the top-level dictionary
        setParsedMidiStems({ ...parsedMidiStems });
    };

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

            // Convert deltaX (pixels) to time (seconds)
            const deltaBars = deltaX / popupPixelsPerBar;
            const deltaBeats = deltaBars * parsedBeatsPerBar;
            let deltaTime = deltaBeats / (activeBpm / 60);

            // Convert deltaY (pixels) to pitch
            const deltaPitch = -Math.round(deltaY / popupRowHeight);

            // Snapping Logic
            const snapThresholdPx = 6; // Reduced to 6 pixels of magnetic resistance
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

                        // 1. Snap Right Edge to neighbor's Left Edge
                        const distRightToLeft = Math.abs((rawNewTime + noteDuration) - neighborStart);
                        if (distRightToLeft < minDistanceTime && distRightToLeft < snapThresholdTime) {
                            minDistanceTime = distRightToLeft;
                            closestSnapDeltaTime = neighborStart - noteDuration - clickedOriginal.originalTime;
                        }

                        // 2. Snap Left Edge to neighbor's Right Edge
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

            // Trigger re-render
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

    // Helper to render full 128 key space notes
    const renderFullMidiNotes = () => {
        if (!parsedMidiStems) return null;
        const stemData = parsedMidiStems[trackName];
        if (!stemData || !stemData.midiData || !stemData.midiData.tracks || stemData.midiData.tracks.length === 0) {
            return null;
        }

        const notes = stemData.midiData.tracks[0].notes;
        if (notes.length === 0) return null;

        const handleNoteMouseDown = (index, e) => {
            e.stopPropagation();
            let newSelection = new Set(selectedNoteIndices);
            
            if (!newSelection.has(index)) {
                if (!e.shiftKey) {
                    newSelection.clear();
                }
                newSelection.add(index);
                setSelectedNoteIndices(newSelection);
                
                const note = stemData.midiData.tracks[0].notes[index];
                if (note) {
                    auditionNote(note);
                }
            } else {
                if (e.shiftKey) {
                    newSelection.delete(index);
                    setSelectedNoteIndices(newSelection);
                    return; // Don't initiate drag if just deselecting
                }
            }
            
            // Initiate note dragging
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

        return notes.map((note, index) => {
            const noteStartBeats = note.time * (activeBpm / 60);
            const noteStartBars = noteStartBeats / parsedBeatsPerBar;
            const leftPx = noteStartBars * popupPixelsPerBar;

            const noteDurationBeats = note.duration * (activeBpm / 60);
            const noteDurationBars = noteDurationBeats / parsedBeatsPerBar;
            const widthPx = Math.max(2, noteDurationBars * popupPixelsPerBar);

            // Midi pitch 0-127. 127 is top (0px), 0 is bottom
            const topPx = (127 - note.midi) * popupRowHeight;

            // Map velocity (0-1) continuously across an HSL color spectrum (Muted / Greyish Heatmap)
            const v = note.velocity !== undefined ? Math.max(0.01, note.velocity) : 0.8;
            
            // Hue: Blue(200) -> Green -> Orange -> Red(15)
            const hue = 200 - (v * 185); 
            
            // Saturation: Stays low to prevent vibrance. 25% (pale blue) to 35% (brownish red)
            const saturation = Math.round(25 + (v * 10)); 
            
            // Lightness: 75% (light, pale blue) down to 35% (dark, brownish red)
            const lightness = Math.round(75 - (v * 40)); 
            
            const noteColor = `hsl(${Math.round(hue)}, ${saturation}%, ${lightness}%)`;

            const isSelected = selectedNoteIndices.has(index);

            return (
                <div 
                    key={`popup-note-${index}`}
                    onMouseDown={(e) => handleNoteMouseDown(index, e)}
                    style={{
                        position: 'absolute',
                        left: `${leftPx}px`,
                        width: `${widthPx}px`,
                        top: `${topPx}px`,
                        height: `${popupRowHeight}px`,
                        backgroundColor: noteColor,
                        borderRadius: '2px',
                        boxShadow: isSelected ? '0 0 0 1px rgba(255,255,255,0.6), 0 0 4px rgba(255,255,255,0.4)' : '0 0 2px rgba(0,0,0,0.5)',
                        border: isSelected ? '1px solid rgba(255,255,255,0.6)' : '1px solid rgba(255,255,255,0.2)',
                        boxSizing: 'border-box',
                        cursor: 'pointer',
                        zIndex: isSelected ? 10 : 1
                    }}
                    title={`Pitch: ${note.name} (${note.midi}) | Velocity: ${Math.round((note.velocity || 0) * 100)}%`}
                />
            );
        });
    };

    // 128 rows * popupRowHeight total height
    const gridHeight = 128 * popupRowHeight;
    
    // Scale the playhead position to match the popup's local zoom level
    const popupPlayheadX = (playheadX / pixelsPerBar) * popupPixelsPerBar;

    return (
        <div style={{
            position: 'fixed',
            top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
            zIndex: 9999,
            display: 'flex',
            flexDirection: 'column',
            padding: '20px'
        }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                <h3 style={{ margin: 0, color: '#fff', textTransform: 'capitalize', fontSize: '24px' }}>
                    MIDI Editor: {trackName}
                </h3>
                <button 
                    onClick={onClose}
                    style={{
                        background: 'transparent', color: '#ccc', border: 'none', 
                        cursor: 'pointer', fontSize: '28px', lineHeight: 1
                    }}
                >
                    &times;
                </button>
            </div>
            
            {/* Control Bar */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: '30px', 
                backgroundColor: '#1a1a1a', padding: '10px 15px', 
                borderRadius: '8px', marginBottom: '15px', border: '1px solid #333'
            }}>
                {/* Transport Controls */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                    <button title="Go to Beginning" onClick={handleGoToBeginning} style={{
                        background: 'transparent', color: 'white', border: 'none',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                    }}>
                        <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
                    </button>

                    <button title="Play/Pause" onClick={togglePlay} style={{
                        background: 'transparent', color: 'white', border: 'none',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                    }}>
                        {isPlaying ? (
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                        ) : (
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                        )}
                    </button>

                    <button title="Toggle Cycle" onClick={toggleCycling} style={{
                        background: isCycling ? '#8B6508' : 'transparent', 
                        color: isCycling ? '#fff' : 'white', 
                        border: 'none', borderRadius: '4px',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '2px', 
                        opacity: 0.8,
                        transition: 'background-color 0.2s'
                    }}>
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
                    </button>
                </div>

                <div style={{ width: '1px', height: '24px', backgroundColor: '#333' }}></div>

                {/* Mute and Solo Controls */}
                <div style={{ display: 'flex', gap: '8px' }}>
                    <button onClick={() => toggleMute(trackName)} style={{
                        width: '24px', height: '24px',
                        background: mutedTracks[trackName] ? '#e53935' : '#555',
                        color: 'white', border: 'none', borderRadius: '4px', 
                        cursor: 'pointer', fontSize: '11px', fontWeight: 'bold',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'background-color 0.2s'
                    }} title="Mute Track">
                        M
                    </button>
                    <button onClick={() => toggleSolo(trackName)} style={{
                        width: '24px', height: '24px',
                        background: soloedTracks[trackName] ? '#e0a800' : '#555',
                        color: soloedTracks[trackName] ? '#fff' : 'white', 
                        border: 'none', borderRadius: '4px', 
                        cursor: 'pointer', fontSize: '11px', fontWeight: 'bold',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'background-color 0.2s'
                    }} title="Solo Track">
                        S
                    </button>
                </div>

                <div style={{ width: '1px', height: '24px', backgroundColor: '#333', marginLeft: '10px', marginRight: '10px' }}></div>

                {/* MIDI Play Mode Toggle */}
                <button onClick={() => setIsMidiMode(!isMidiMode)} style={{
                    height: '24px', padding: '0 10px',
                    background: 'transparent',
                    color: isMidiMode ? '#4CAF50' : '#aaa', 
                    border: `1px solid ${isMidiMode ? '#4CAF50' : '#555'}`, 
                    boxShadow: isMidiMode ? '0 0 8px rgba(76, 175, 80, 0.5)' : 'none',
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
                    color: undoStackLength > 0 ? 'white' : '#555', 
                    border: `1px solid ${undoStackLength > 0 ? 'white' : '#555'}`, 
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
                    if (handleRevertMidi) handleRevertMidi();
                }} style={{
                    height: '24px', padding: '0 10px',
                    background: 'transparent',
                    color: 'white', 
                    border: '1px solid white', 
                    borderRadius: '4px',
                    cursor: 'pointer', fontSize: '12px', fontWeight: 'bold',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.2s',
                    marginLeft: '10px',
                    opacity: 0.8
                }} title="Revert to Original MIDI">
                    Revert
                </button>

                {/* Spacer to push sliders to the right */}
                <div style={{ flexGrow: 1 }}></div>

                {/* Velocity Control */}
                {selectedNoteIndices.size === 1 && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginRight: '20px', borderRight: '1px solid #333', paddingRight: '20px' }}>
                        <span style={{ color: '#aaa', fontSize: '12px', fontWeight: 'bold' }}>
                            Velocity: {Math.round(commonVelocity * 100)}
                        </span>
                        <input 
                            type="range" 
                            min="0.01" max="1" step="0.01"
                            value={commonVelocity}
                            onChange={handleVelocityChange}
                            onPointerDown={pushUndoState}
                            style={{ width: '80px', cursor: 'pointer', accentColor: '#aaa' }}
                        />
                    </div>
                )}

                {/* Join Notes Button */}
                {(() => {
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
                                return false; // Gap detected
                            }
                        }
                        return true;
                    };

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

                    const canJoin = canJoinSelectedNotes();

                    return canJoin ? (
                        <div style={{ marginRight: '20px', borderRight: '1px solid #333', paddingRight: '20px' }}>
                            <button onClick={handleJoinNotes} style={{
                                height: '24px', padding: '0 10px',
                                background: 'transparent',
                                color: 'white', 
                                border: '1px solid white', 
                                borderRadius: '4px',
                                cursor: 'pointer', fontSize: '12px', fontWeight: 'bold',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                transition: 'all 0.2s',
                                opacity: 0.8
                            }} title="Join Selected Notes">
                                Join
                            </button>
                        </div>
                    ) : null;
                })()}

                {/* Zoom Controls */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }} title="Horizontal Zoom">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="#aaa">
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
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }} title="Vertical Zoom">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="#aaa">
                            <path d="M12 2L8 6h3v12H8l4 4 4-4h-3V6h3z"/>
                        </svg>
                        <input 
                            type="range" 
                            min="8" max="32" 
                            value={popupRowHeight}
                            onChange={(e) => setPopupRowHeight(Number(e.target.value))}
                            style={{ width: '80px', cursor: 'pointer' }}
                        />
                    </div>
                </div>
            </div>

            {/* Split Canvas Area */}
            <div style={{ 
                flexGrow: 1, 
                backgroundColor: '#222', 
                borderRadius: '8px', 
                border: '1px solid #444', 
                overflow: 'hidden',
                display: 'flex',
                flexDirection: 'row'
            }}>
                {/* Left Column: Piano Keys */}
                <div style={{ width: '60px', flexShrink: 0, display: 'flex', flexDirection: 'column', backgroundColor: '#111' }}>
                    {/* Empty top left corner to match the 30px TimelineRuler */}
                    <div style={{ height: '30px', backgroundColor: '#1a1a1a', borderBottom: '1px solid #333', borderRight: '1px solid #222', flexShrink: 0 }} />
                    
                    {/* The keys themselves (sync scrolled) */}
                    <div ref={pianoScrollRef} style={{ flexGrow: 1, overflow: 'hidden', opacity: 0.6 }}>
                        <div style={{ height: `${gridHeight}px` }}>
                            {renderPianoKeys()}
                        </div>
                    </div>
                </div>

                {/* Right Column: Scrollable Grid */}
                <div 
                    ref={gridScrollRef}
                    style={{ flexGrow: 1, overflow: 'auto', position: 'relative' }}
                    onScroll={handleGridScroll}
                >
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
                        <div style={{
                            position: 'absolute',
                            left: `${popupPlayheadX}px`,
                            transform: 'translateX(-50%)',
                            top: '15px',
                            bottom: 0,
                            width: '1px',
                            backgroundColor: '#fff',
                            zIndex: 10,
                            pointerEvents: 'none',
                            boxShadow: '0 0 4px rgba(255, 255, 255, 0.5)'
                        }} />
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
                        style={{
                        position: 'relative',
                        width: '100%',
                        height: `${gridHeight}px`,
                        marginTop: '0px',
                        backgroundSize: `${popupPixelsPerBar}px 100%, 100% ${popupRowHeight}px`,
                        backgroundImage: `
                            linear-gradient(to right, transparent ${popupPixelsPerBar - 1}px, rgba(255,255,255,0.05) ${popupPixelsPerBar}px),
                            linear-gradient(to bottom, transparent ${popupRowHeight - 1}px, rgba(255,255,255,0.05) ${popupRowHeight}px)
                        `
                    }}>
                        {/* Drag Highlight Row */}
                        {noteDragState && noteDragState.hasMoved && parsedMidiStems && parsedMidiStems[trackName] && (
                            (() => {
                                const notes = parsedMidiStems[trackName].midiData.tracks[0].notes;
                                const clickedNote = notes[noteDragState.clickedNoteIndex];
                                if (!clickedNote) return null;
                                
                                const topPx = (127 - clickedNote.midi) * popupRowHeight;
                                return (
                                    <div style={{
                                        position: 'absolute',
                                        left: 0,
                                        right: 0,
                                        top: `${topPx}px`,
                                        height: `${popupRowHeight}px`,
                                        backgroundColor: 'rgba(255, 255, 255, 0.1)',
                                        pointerEvents: 'none',
                                        zIndex: 0
                                    }} />
                                );
                            })()
                        )}

                        {renderFullMidiNotes()}
                        
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
                    </div>
                </div>
            </div>
        </div>
        </div>
    );
}
