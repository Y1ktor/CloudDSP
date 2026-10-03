import { useCallback, useEffect, useRef, useState } from 'react';
import { useMidiEditorOperations } from '../../../hooks/useMidiEditorOperations';
import { useMidiExport } from '../../../hooks/useMidiExport';
import { useTransportPlayhead } from '../../../hooks/useTransportPlayhead';
import { useTimelineViewport } from '../../../hooks/useTimelineViewport';
import {
    ADTOF_DRUM_VOICES,
    getAdtofDrumVoice,
    getDrumPlaybackVelocity,
    isDrumVoiceAudible,
} from '../../../utils/DrumMidi';
import { getMelodicPlaybackVelocity } from '../../../utils/MidiPlayback';
import { DRUM_EDITOR_ROW_HEIGHT } from './midiEditorLayout';

/**
 * Coordinate the popup's editing session independently of its presentation.
 * All hooks stay unconditional, and zoom/selection retain the same lifetime as
 * the containing popup. The shared audio clock writes playhead transforms;
 * local React state only describes edits, controls, and the visible viewport.
 */
export function useMidiEditorPopup({
    trackName,
    duration,
    pixelsPerBar,
    totalBars,
    playheadX,
    cycleRegion,
    isPlaying,
    drumMutedVoices = {},
    drumSoloedVoices = {},
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
    const [popupPixelsPerBar, setPopupPixelsPerBar] = useState(pixelsPerBar || 100);
    const [popupRowHeight, setPopupRowHeight] = useState(8); // Default to 8 (lowest)

    // Selection state for MIDI notes (multi selection)
    const [selectedNoteIndices, setSelectedNoteIndices] = useState(new Set());
    const [showHintBox, setShowHintBox] = useState(false);
    const [isRevertConfirmationOpen, setIsRevertConfirmationOpen] = useState(false);

    const [contextMenu, setContextMenu] = useState(null);
    const closeContextMenu = () => { if (contextMenu) setContextMenu(null); };
    useEffect(() => {
        const handleClick = () => { if (contextMenu) setContextMenu(null); };
        window.addEventListener('click', handleClick);
        return () => window.removeEventListener('click', handleClick);
    }, [contextMenu]);

    // Track if Cmd/Ctrl is held down
    const [isModifierHeld, setIsModifierHeld] = useState(false);
    useEffect(() => {
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
    useEffect(() => {
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
    }, [trackName, popupRowHeight, isAdtofDrum]);

    // Keep edit state and the active MIDI instance coordinated by the shared operations hook.
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

    // `useMidiEditorOperations` intentionally returns current-state handlers.
    // Keep a stable bridge for the memoised virtual note layer so a throttled
    // transport/readout render does not invalidate every visible note.
    const noteMouseDownRef = useRef(handleNoteMouseDown);
    noteMouseDownRef.current = handleNoteMouseDown;
    const handleVisibleNoteMouseDown = useCallback((index, event, action) => {
        noteMouseDownRef.current?.(index, event, action);
    }, []);
    const handleVisibleNoteContextMenu = useCallback((index, event) => {
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

    return {
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
    };
}
