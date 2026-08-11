import React, { useEffect, useRef, useState } from 'react';
import TimelineRuler from './TimelineRuler';
import TransportPlayheadLine from './TransportPlayheadLine';
import { useMidiEditorOperations } from '../../hooks/useMidiEditorOperations';
import { useMidiExport } from '../../hooks/useMidiExport';
import { useTransportPlayhead } from '../../hooks/useTransportPlayhead';
import { useTimelineViewport } from '../../hooks/useTimelineViewport';
import {
    ADTOF_DRUM_VOICES,
    getAdtofDrumVoice,
    getAdtofDrumVoiceIndex,
    getDrumPlaybackVelocity,
    getDrumVoiceTrackId,
    isDrumVoiceAudible
} from '../../utils/DrumMidi';
import { getMelodicPlaybackVelocity } from '../../utils/MidiPlayback';

const DRUM_EDITOR_ROW_HEIGHT = 56;

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

function lowerBoundByTime(notes, time) {
    let low = 0;
    let high = notes.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (notes[middle].time < time) low = middle + 1;
        else high = middle;
    }
    return low;
}

function upperBoundByTime(notes, time) {
    let low = 0;
    let high = notes.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (notes[middle].time <= time) low = middle + 1;
        else high = middle;
    }
    return low;
}

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
        // `midiRevision` changes whenever an editor operation replaces the
        // top-level parsed-MIDI state, even though the nested MIDI instance is
        // intentionally mutated in place.
        const notes = midiRevision ? (midiData?.tracks?.[0]?.notes || []) : [];
        let maxDuration = 0;
        const indexed = [];

        notes.forEach((note, index) => {
            const time = Number(note?.time);
            if (!Number.isFinite(time) || time < 0) return;
            const duration = Number(note?.duration);
            if (Number.isFinite(duration) && duration > maxDuration) {
                maxDuration = duration;
            }
            indexed.push({ note, index, time });
        });

        indexed.sort((left, right) => left.time - right.time || left.index - right.index);
        return { notes: indexed, maxDuration };
    // MIDI editor operations intentionally replace the top-level MIDI-state
    // object after mutating note instances. Depend on that revision so the
    // time index is rebuilt after an edit, undo, or revert.
    }, [midiData, midiRevision]);

    const visibleNotes = React.useMemo(() => {
        const { notes, maxDuration } = indexedNotes;
        if (notes.length === 0) return [];

        const pixelsPerSecond = (Number(activeBpm) / 60 / Number(parsedBeatsPerBar))
            * Number(popupPixelsPerBar);
        if (!Number.isFinite(pixelsPerSecond) || pixelsPerSecond <= 0) return notes;

        const startPx = Math.max(0, Number(visibleRange?.startPx) || 0);
        const endPx = Number(visibleRange?.endPx);
        const overscanPx = Math.max(0, Number(visibleRange?.overscanPx) || 0);
        if (!Number.isFinite(endPx) || endPx <= startPx) return notes;

        // Retain a long note which begins before the viewport but extends into
        // it. The maximum source duration turns that into one binary-search
        // range rather than a scan of every earlier note.
        const startTime = Math.max(0, ((startPx - overscanPx) / pixelsPerSecond) - maxDuration);
        const endTime = (endPx + overscanPx) / pixelsPerSecond;
        return notes.slice(
            lowerBoundByTime(notes, startTime),
            upperBoundByTime(notes, endTime),
        );
    }, [activeBpm, indexedNotes, parsedBeatsPerBar, popupPixelsPerBar, visibleRange]);

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
            ? '#555'
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
 * @param {Object} props.drumMutedVoices - Dictionary of muted ADTOF drum MIDI lanes
 * @param {Object} props.drumSoloedVoices - Dictionary of soloed ADTOF drum MIDI lanes
 * @param {Function} props.toggleDrumMute - Callback to toggle one ADTOF drum MIDI lane
 * @param {Function} props.toggleDrumSolo - Callback to solo one ADTOF drum MIDI lane
 * @param {Object} props.drumVoiceGainsDb - Per-voice ADTOF MIDI output gain in dB
 * @param {Function} props.setDrumVoiceGainDb - Setter for one ADTOF voice gain
 * @param {number} props.activeBpm - The current user-adjusted master BPM
 * @param {number} props.parsedBeatsPerBar - Derived variable representing number of beats in a bar
 * @param {Function} props.handleSeek - Callback to seek playback to a specific percentage (0-1)
 * @param {Object} props.parsedMidiStems - The master dictionary containing parsed `@tonejs/midi` class instances
 * @param {Function} props.setParsedMidiStems - State setter for the master MIDI dictionary, used to trigger re-renders
 * @param {React.MutableRefObject} props.audioCtxRef - Reference to the global Web Audio context
 * @param {React.MutableRefObject} props.transportRef - Shared audio-clock transport state
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
    const popupTimelineRef = useRef(null);
    const pianoScrollRef = useRef(null);
    const gridScrollRef = useRef(null);
    const popupTimelinePlayheadRef = useRef(null);
    const popupRulerPlayheadRef = useRef(null);
    const [popupVisibleTimelineRange, setPopupGridScrollContainer] = useTimelineViewport(gridScrollRef);
    const isAdtofDrum = parsedMidiStems?.[trackName]?.isAdtofDrum === true;
    const isMidiPending = midiStatus === 'processing' || midiStatus === 'loading';
    const isMidiFailed = midiStatus === 'failed';
    const midiPendingLabel = midiStatus === 'loading'
        ? 'Loading the generated MIDI into the editor…'
        : isMidiFailed
            ? 'MIDI extraction failed for this stem. You can retry the job from the workspace after inspecting its status.'
            : 'MIDI is still being generated for this stem…';

    // Track drag state


    // ADTOF uses General MIDI pitches as class labels. The drum sampler instead
    // expects its named one-shot sample (kick, snare, mid-tom, and so on).
    const auditionNote = (note) => {
        if (isMidiMode && synthRef && audioCtxRef.current) {
            const drumVoice = isAdtofDrum ? getAdtofDrumVoice(note.midi) : null;
            if (isAdtofDrum && !drumVoice) return;
            if (isAdtofDrum && !isDrumVoiceAudible(
                trackName,
                drumVoice,
                drumMutedVoices,
                drumSoloedVoices
            )) return;
            const noteSynthRef = drumVoice
                ? synthRef.get?.(drumVoice.id)
                : synthRef;
            if (!noteSynthRef?.current) return;
            noteSynthRef.current.start({
                note: drumVoice ? drumVoice.sample : note.midi,
                velocity: drumVoice
                    ? getDrumPlaybackVelocity(note, drumVoice)
                    : getMelodicPlaybackVelocity(note, trackName),
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
    const [showHintBox, setShowHintBox] = React.useState(false);
    const [isRevertConfirmationOpen, setIsRevertConfirmationOpen] = useState(false);
    
    const [contextMenu, setContextMenu] = React.useState(null);
    const closeContextMenu = () => { if (contextMenu) setContextMenu(null); };
    React.useEffect(() => {
        const handleClick = () => closeContextMenu();
        window.addEventListener('click', handleClick);
        return () => window.removeEventListener('click', handleClick);
    }, [contextMenu]);
    
    // Track if Cmd/Ctrl is held down
    const [isModifierHeld, setIsModifierHeld] = React.useState(false);
    React.useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.metaKey || e.ctrlKey) setIsModifierHeld(true);
        };
        const handleKeyUp = (e) => {
            if (!e.metaKey && !e.ctrlKey) setIsModifierHeld(false);
        };
        window.addEventListener('keydown', handleKeyDown);
        window.addEventListener('keyup', handleKeyUp);
        
        // Failsafe in case window loses focus while holding the key
        const handleBlur = () => setIsModifierHeld(false);
        window.addEventListener('blur', handleBlur);
        
        return () => {
            window.removeEventListener('keydown', handleKeyDown);
            window.removeEventListener('keyup', handleKeyUp);
            window.removeEventListener('blur', handleBlur);
        };
    }, []);

    useEffect(() => {
        if (!isRevertConfirmationOpen) return undefined;
        const handleKeyDown = (event) => {
            if (event.key === 'Escape') setIsRevertConfirmationOpen(false);
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isRevertConfirmationOpen]);

    const confirmRevertMidi = () => {
        if (handleRevertMidi) handleRevertMidi();
        setSelectedNoteIndices(new Set());
        setIsRevertConfirmationOpen(false);
    };
    
    // Center melodic editors on C4. ADTOF drum editors only have five lanes.
    React.useEffect(() => {
        if (trackName && gridScrollRef.current) {
            if (isAdtofDrum) {
                gridScrollRef.current.scrollTop = 0;
                if (pianoScrollRef.current) pianoScrollRef.current.scrollTop = 0;
                return;
            }
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
    }, [trackName, popupRowHeight, isAdtofDrum]); // Only run when popup opens (trackName changes)

    // Call the new operations hook
    const {
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
        handleNoteMouseDown
    } = useMidiEditorOperations({
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
        isDrumMidi: isAdtofDrum,
        drumRowHeight: DRUM_EDITOR_ROW_HEIGHT,
        auditionNote
    });

    const { handleExportMidi, handleExportCycleRange } = useMidiExport({
        parsedMidiStems,
        trackName,
        fileName,
        cycleRegion,
        pixelsPerBar,
        totalBars,
        duration,
        activeBpm,
        parsedBeatsPerBar
    });

    // Sync vertical scrolling
    const handleGridScroll = (e) => {
        if (pianoScrollRef.current) {
            pianoScrollRef.current.scrollTop = e.target.scrollTop;
        }
    };

    // Render the piano keyboard column with realistic styling
    const renderPianoKeys = () => {
        if (isAdtofDrum) {
            return ADTOF_DRUM_VOICES.map((voice) => {
                const voiceTrackId = getDrumVoiceTrackId(trackName, voice.id);
                const isMuted = Boolean(drumMutedVoices[voiceTrackId]);
                const isSoloed = Boolean(drumSoloedVoices[voiceTrackId]);

                return (
                    <div key={voice.id} style={{
                        height: `${DRUM_EDITOR_ROW_HEIGHT}px`, boxSizing: 'border-box', display: 'flex', flexDirection: 'column',
                        justifyContent: 'center', gap: '1px', padding: '4px 7px',
                        color: voice.color, backgroundColor: '#1a1a1a', borderBottom: '1px solid #3a3a3a',
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
                                        background: isMuted ? '#e53935' : '#444', color: '#fff', cursor: 'pointer',
                                        fontSize: '10px', fontWeight: 'bold'
                                    }}
                                    title={`Mute ${voice.label} MIDI`}
                                >M</button>
                                <button
                                    type="button"
                                    onClick={() => toggleDrumSolo?.(trackName, voice.id)}
                                    style={{
                                        width: '21px', height: '21px', padding: 0, border: 'none', borderRadius: '3px',
                                        background: isSoloed ? '#e0a800' : '#444', color: '#fff', cursor: 'pointer',
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
                        backgroundColor: '#ddd', 
                    }}>
                        <div style={{
                            position: 'absolute', top: '50%', left: '60%', right: 0,
                            height: '1px', backgroundColor: '#bbb', zIndex: 1
                        }} />
                        <div style={{
                            position: 'absolute', top: 0, left: 0, width: '60%', height: '100%',
                            backgroundColor: '#1a1a1a', borderBottom: '2px solid #000',
                            borderTop: '1px solid #333', borderRight: '2px solid #000',
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
                        backgroundColor: '#ddd', borderBottom: needsBottomBorder ? '1px solid #bbb' : 'none',
                        color: '#555', display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
                        paddingRight: '6px', fontSize: '10px', fontWeight: isC ? 'bold' : 'normal',
                        userSelect: 'none', position: 'relative', zIndex: 3
                    }}>
                        {isC && `C${octave}`}
                    </div>
                );
            }
        }
        return keys;
    };

    // `useMidiEditorOperations` intentionally returns current-state handlers.
    // Keep a stable bridge for the memoised virtual note layer so a throttled
    // transport/readout render does not invalidate every visible note.
    const noteMouseDownRef = useRef(handleNoteMouseDown);
    noteMouseDownRef.current = handleNoteMouseDown;
    const handleVisibleNoteMouseDown = React.useCallback((index, event, action) => {
        noteMouseDownRef.current?.(index, event, action);
    }, []);
    const handleVisibleNoteContextMenu = React.useCallback((index, event) => {
        event.preventDefault();
        event.stopPropagation();
        setSelectedNoteIndices((previous) => (
            previous.has(index) ? previous : new Set([index])
        ));
        setContextMenu({ x: event.clientX, y: event.clientY, isNoteSelected: true });
    }, []);

    const editorMidiData = parsedMidiStems?.[trackName]?.midiData;

    const gridHeight = isAdtofDrum
        ? ADTOF_DRUM_VOICES.length * DRUM_EDITOR_ROW_HEIGHT
        : 128 * popupRowHeight;
    
    // Scale the playhead position to match the popup's local zoom level
    const popupPlayheadX = (playheadX / pixelsPerBar) * popupPixelsPerBar;

    useTransportPlayhead({
        audioCtxRef,
        transportRef,
        isPlaying,
        pixelsPerBar: popupPixelsPerBar,
        bpm: activeBpm,
        beatsPerBar: parsedBeatsPerBar,
        playheadRefs: [popupTimelinePlayheadRef, popupRulerPlayheadRef],
        scrollContainerRef: gridScrollRef,
        enabled: Boolean(trackName) && duration > 0,
    });

    // Keep every hook above this point unconditional. The popup remains mounted
    // while closed, so returning earlier would change hook order as soon as a
    // double-click selects a track.
    if (!trackName) return null;

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
                    background: linear-gradient(90deg, #4f94d4 0%, #4f94d4 var(--drum-editor-gain-progress), #505a67 var(--drum-editor-gain-progress), #505a67 100%);
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
                    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.7);
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
                    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.7);
                }
                .drum-editor-gain-slider:focus-visible { outline: 2px solid #70b4ef; outline-offset: 2px; }
            `}</style>
            {isRevertConfirmationOpen && (
                <div
                    role="presentation"
                    onMouseDown={() => setIsRevertConfirmationOpen(false)}
                    style={{
                        position: 'fixed', inset: 0, zIndex: 10,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        backgroundColor: 'rgba(0, 0, 0, 0.55)', padding: '20px'
                    }}
                >
                    <section
                        role="alertdialog"
                        aria-modal="true"
                        aria-labelledby="revert-midi-title"
                        aria-describedby="revert-midi-description"
                        onMouseDown={(event) => event.stopPropagation()}
                        style={{
                            width: 'min(420px, 100%)', backgroundColor: '#202020', color: '#fff',
                            border: '1px solid #555', borderRadius: '8px', padding: '20px',
                            boxShadow: '0 18px 50px rgba(0, 0, 0, 0.6)'
                        }}
                    >
                        <h4 id="revert-midi-title" style={{ margin: '0 0 10px', fontSize: '18px' }}>
                            Revert {trackName} MIDI?
                        </h4>
                        <p id="revert-midi-description" style={{ margin: '0 0 20px', color: '#c8c8c8', fontSize: '14px', lineHeight: 1.45 }}>
                            This restores the generated MIDI for this track and discards the current edits. You can undo the revert afterward.
                        </p>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
                            <button
                                type="button"
                                onClick={() => setIsRevertConfirmationOpen(false)}
                                style={{
                                    padding: '7px 12px', background: 'transparent', color: '#fff', border: '1px solid #777',
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
            )}
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <h3 style={{ margin: 0, color: '#fff', textTransform: 'capitalize', fontSize: '24px' }}>
                        {isAdtofDrum ? 'Drum Editor' : 'MIDI Editor'}: {trackName}
                    </h3>
                    {isAdtofDrum && (
                        <span style={{ color: '#9fa8da', fontSize: '12px', fontWeight: '600' }}>
                            ADTOF kit: Kick · Snare · Tom · Hi-hat · Cymbal
                        </span>
                    )}
                    {(isMidiPending || isMidiFailed) && (
                        <span style={{
                            color: isMidiFailed ? '#ff9a9a' : '#f5c451', background: isMidiFailed ? 'rgba(155, 45, 45, 0.22)' : 'rgba(224, 168, 0, 0.16)',
                            border: `1px solid ${isMidiFailed ? 'rgba(255, 100, 100, 0.45)' : 'rgba(224, 168, 0, 0.4)'}`, borderRadius: '999px',
                            padding: '4px 9px', fontSize: '12px', fontWeight: '600'
                        }}>
                            {isMidiFailed ? 'MIDI failed' : 'MIDI processing'}
                        </span>
                    )}
                </div>
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
                    if (handleRevertMidi) setIsRevertConfirmationOpen(true);
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

                {/* Hint Button & Popup */}
                <div style={{ position: 'relative' }}>
                    <button onClick={() => setShowHintBox(!showHintBox)} style={{
                        height: '24px', width: '24px', padding: '0',
                        background: 'transparent',
                        color: '#aaa', 
                        border: 'none',
                        cursor: 'pointer',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'color 0.2s',
                        marginLeft: '4px'
                    }} title="Shortcuts & Controls" onMouseEnter={e => e.currentTarget.style.color = 'white'} onMouseLeave={e => e.currentTarget.style.color = '#aaa'}>
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
                                backgroundColor: '#111',
                                border: '1px solid #333',
                                borderRadius: '6px',
                                padding: '16px',
                                color: '#fff',
                                boxShadow: '0 8px 32px rgba(0,0,0,0.8)',
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
                                    borderBottom: '7px solid #333',
                                }} />
                                <div style={{
                                    position: 'absolute',
                                    top: '-6px',
                                    right: '19px',
                                    width: 0,
                                    height: 0,
                                    borderLeft: '6px solid transparent',
                                    borderRight: '6px solid transparent',
                                    borderBottom: '6px solid #111',
                                    zIndex: 1
                                }} />
                                
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px', borderBottom: '1px solid #222', paddingBottom: '8px' }}>
                                    <h3 style={{ margin: 0, fontSize: '14px' }}>Shortcuts & Controls</h3>
                                    <button onClick={() => setShowHintBox(false)} style={{ background: 'transparent', border: 'none', color: '#aaa', cursor: 'pointer', fontSize: '16px' }}>&times;</button>
                                </div>
                                
                                <div style={{ display: 'grid', gap: '8px', fontSize: '12px' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Add Note</span>
                                        <span><kbd>Cmd/Ctrl</kbd> + Click</span>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Lasso Select</span>
                                        <span>Drag Background</span>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Multi-select</span>
                                        <span><kbd>Shift</kbd> + Click</span>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Context Menu</span>
                                        <span>Right Click</span>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Replicate</span>
                                        <span><kbd>Shift</kbd> + Drag</span>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Join Notes</span>
                                        <span><kbd>J</kbd></span>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Disable / Restore</span>
                                        <span><kbd>D</kbd></span>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Delete Note(s)</span>
                                        <span><kbd>Backspace / Del</kbd></span>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <span style={{ color: '#aaa' }}>Undo</span>
                                        <span><kbd>Cmd/Ctrl</kbd> + <kbd>Z</kbd></span>
                                    </div>
                                </div>
                            </div>
                        </>
                    )}
                </div>

                {/* Spacer to push sliders to the right */}
                <div style={{ flexGrow: 1 }}></div>

                {/* Velocity Control */}
                {selectedNoteIndices.size > 0 && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginRight: '20px', borderRight: '1px solid #333', paddingRight: '20px' }}>
                        <span style={{ color: '#aaa', fontSize: '12px', fontWeight: 'bold' }}>
                            Velocity: {Math.round(commonVelocity * 100)}
                        </span>
                        <input 
                            type="range" 
                            min="0.05" max="1" step="0.01"
                            value={commonVelocity}
                            onChange={handleVelocityChange}
                            onPointerDown={pushUndoState}
                            style={{ width: '80px', cursor: 'pointer', accentColor: '#aaa' }}
                        />
                    </div>
                )}

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
                    {!isAdtofDrum && <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }} title="Vertical Zoom">
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
                    </div>}
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
                {/* Left Column: piano keys, or named ADTOF drum lanes */}
                <div style={{ width: isAdtofDrum ? '154px' : '60px', flexShrink: 0, display: 'flex', flexDirection: 'column', backgroundColor: '#111' }}>
                    {/* Empty top left corner to match the 30px TimelineRuler */}
                    <div style={{ height: '30px', backgroundColor: '#1a1a1a', borderBottom: '1px solid #333', borderRight: '1px solid #222', flexShrink: 0 }} />
                    
                    {/* The keys themselves (sync scrolled) */}
                    <div ref={pianoScrollRef} style={{ flexGrow: 1, overflow: 'hidden', opacity: isAdtofDrum ? 1 : 0.6 }}>
                        <div style={{ height: `${gridHeight}px` }}>
                            {renderPianoKeys()}
                        </div>
                    </div>
                </div>

                {/* Right Column: Scrollable Grid */}
                <div 
                    ref={setPopupGridScrollContainer}
                    style={{ flexGrow: 1, overflow: 'auto', position: 'relative' }}
                    onScroll={handleGridScroll}
                >
                    {(isMidiPending || isMidiFailed) && (
                        <div style={{
                            position: 'absolute', inset: 0, zIndex: 30, display: 'flex',
                            alignItems: 'center', justifyContent: 'center', textAlign: 'center',
                            color: isMidiFailed ? '#ff9a9a' : '#f5c451', backgroundColor: 'rgba(15, 15, 15, 0.76)',
                            padding: '24px', pointerEvents: 'auto'
                        }}>
                            <div>
                                <div aria-hidden="true" style={{ fontSize: '20px', marginBottom: '8px' }}>{isMidiFailed ? '!' : '●'}</div>
                                <strong style={{ display: 'block', color: '#fff', marginBottom: '4px' }}>{isMidiFailed ? 'MIDI unavailable' : 'MIDI not ready yet'}</strong>
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
                        backgroundSize: `${popupPixelsPerBar}px 100%, ${popupPixelsPerBar / parsedBeatsPerBar}px 100%, 100% ${isAdtofDrum ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight}px`,
                        backgroundImage: `
                            linear-gradient(to right, transparent ${popupPixelsPerBar - 1}px, rgba(255,255,255,0.1) ${popupPixelsPerBar}px),
                            linear-gradient(to right, transparent ${(popupPixelsPerBar / parsedBeatsPerBar) - 1}px, rgba(255,255,255,0.03) ${popupPixelsPerBar / parsedBeatsPerBar}px),
                            linear-gradient(to bottom, transparent ${(isAdtofDrum ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight) - 1}px, rgba(255,255,255,0.05) ${isAdtofDrum ? DRUM_EDITOR_ROW_HEIGHT : popupRowHeight}px)
                        `
                    }}>
                        {/* Drag Highlight Row or Replication Projections */}
                        {noteDragState && noteDragState.hasMoved && parsedMidiStems && parsedMidiStems[trackName] && (
                            (() => {
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
                                        const noteColor = isDisabled ? '#555' : `hsla(${Math.round(hue)}, ${saturation}%, ${lightness}%, 0.4)`;

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
                            })()
                        )}

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

            {/* Context Menu */}
            {contextMenu && (
                <div 
                    style={{
                        position: 'fixed',
                        left: `${contextMenu.x}px`,
                        top: `${contextMenu.y}px`,
                        backgroundColor: '#222',
                        border: '1px solid #444',
                        borderRadius: '4px',
                        padding: '4px 0',
                        boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                        zIndex: 10000,
                        color: '#fff',
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
                            color: undoStackLength > 0 ? '#fff' : '#aaa',
                            borderBottom: '1px solid #333',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center'
                        }}
                        onClick={() => {
                            if (undoStackLength > 0 && handleUndoMidi) handleUndoMidi();
                            closeContextMenu();
                        }}
                        onMouseEnter={(e) => { if (undoStackLength > 0) e.target.style.backgroundColor = '#333' }}
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
                                onMouseEnter={(e) => e.target.style.backgroundColor = '#333'}
                                onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                            >
                                <span>{allDisabled ? "Restore" : "Disable"}</span>
                                <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px', color: '#aaa' }}>D</span>
                            </div>
                            <div 
                                style={{ padding: '8px 16px', cursor: 'pointer', color: '#e53935', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                                onClick={() => { handleDeleteNotes(); closeContextMenu(); }}
                                onMouseEnter={(e) => e.target.style.backgroundColor = '#333'}
                                onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                            >
                                <span>Delete</span>
                                <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px', color: '#aaa' }}>⌫ / Del</span>
                            </div>
                            {canJoin && (
                                <div 
                                    style={{ padding: '8px 16px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                                    onClick={() => { handleJoinNotes(); closeContextMenu(); }}
                                    onMouseEnter={(e) => e.target.style.backgroundColor = '#333'}
                                    onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                                >
                                    <span>Join</span>
                                    <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px', color: '#aaa' }}>J</span>
                                </div>
                            )}
                            <div style={{ height: '1px', backgroundColor: '#333', margin: '4px 0' }} />
                        </>
                    )}

                    {selectedNoteIndices.size === 0 && contextMenu.gridX !== undefined && (
                        <>
                            <div 
                                style={{ padding: '8px 16px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                                onClick={() => { handleAddNote(contextMenu.gridX, contextMenu.gridY); closeContextMenu(); }}
                                onMouseEnter={(e) => e.target.style.backgroundColor = '#333'}
                                onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                            >
                                <span>Add Note</span>
                                <span style={{ fontSize: '11px', opacity: 0.5, marginLeft: '20px' }}>Cmd/Ctrl+Click</span>
                            </div>
                            <div style={{ height: '1px', backgroundColor: '#333', margin: '4px 0' }} />
                        </>
                    )}

                    <div 
                        style={{ padding: '8px 16px', cursor: 'pointer' }}
                        onClick={() => { handleExportMidi(); closeContextMenu(); }}
                        onMouseEnter={(e) => e.target.style.backgroundColor = '#333'}
                        onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                    >
                        Export MIDI
                    </div>
                    <div 
                        style={{ padding: '8px 16px', cursor: 'pointer' }}
                        onClick={() => { handleExportCycleRange(); closeContextMenu(); }}
                        onMouseEnter={(e) => e.target.style.backgroundColor = '#333'}
                        onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
                    >
                        Export Cycle Range
                    </div>
                </div>
            )}
        </div>
        </div>
    );
}
