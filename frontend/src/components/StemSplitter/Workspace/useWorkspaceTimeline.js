import { useCallback, useEffect, useRef, useState } from 'react';
import { useTransportPlayhead } from '../../../hooks/useTransportPlayhead';
import { useTimelineViewport } from '../../../hooks/useTimelineViewport';

/** Workspace timing, viewport, direct audio-clock playheads, and ruler drag interactions. */
export function useWorkspaceTimeline({ audioEngine, jobId, editorOpenTrack }) {
    const { handleSeek: handleAudioSeek, setCycleRegion } = audioEngine;

    const [pixelsPerBar, setPixelsPerBar] = useState(100);
    const parsedBeatsPerBar = parseInt(audioEngine.timeSignature.split('/')[0], 10) || 4;
    const activeBpm = audioEngine.bpm;
    const totalBars = audioEngine.duration > 0 ? Math.ceil((audioEngine.duration * (activeBpm / 60)) / parsedBeatsPerBar) : 20;

    const dynamicDuration = audioEngine.originalBpm && audioEngine.duration ? audioEngine.duration * (audioEngine.originalBpm / audioEngine.bpm) : audioEngine.duration;
    const dynamicProgress = audioEngine.originalBpm && audioEngine.progress ? audioEngine.progress * (audioEngine.originalBpm / audioEngine.bpm) : audioEngine.progress;
    // React only receives a throttled position for text/readout compatibility.
    // The transport hook below moves the visual playhead every frame directly
    // from AudioContext time, without rebuilding this component tree.
    const playheadX = (audioEngine.progress * (activeBpm / 60) / parsedBeatsPerBar) * pixelsPerBar;

    const [isPlayheadHovered, setIsPlayheadHovered] = useState(false);
    const playheadDragRef = useRef({ isDragging: false });
    const cycleDragRef = useRef({ isDragging: false, mode: 'move', initialX: 0, initialStart: 0, initialEnd: 0 });
    const timelineRef = useRef(null);
    const scrollContainerRef = useRef(null);
    const timelinePlayheadRef = useRef(null);
    const rulerPlayheadRef = useRef(null);
    const [visibleTimelineRange, setTimelineScrollContainer] = useTimelineViewport(scrollContainerRef);

    const { notifyManualSeek } = useTransportPlayhead({
        audioCtxRef: audioEngine.audioCtxRef,
        transportRef: audioEngine.transportRef,
        isPlaying: audioEngine.isPlaying,
        pixelsPerBar,
        bpm: activeBpm,
        beatsPerBar: parsedBeatsPerBar,
        playheadRefs: [timelinePlayheadRef, rulerPlayheadRef],
        scrollContainerRef,
        resetKey: jobId,
        // The modal owns its own direct playhead while open; do not animate
        // the fully occluded workspace timeline in parallel.
        enabled: audioEngine.duration > 0 && !editorOpenTrack,
    });

    const handleTimelineSeek = useCallback((event) => {
        handleAudioSeek(event);
        // Ruler scrubs carry viewport intent. Tell the transport whether the
        // chosen position is left or right of the current view before the
        // next animation frame decides whether to scroll the canvas.
        notifyManualSeek();
    }, [handleAudioSeek, notifyManualSeek]);

    useEffect(() => {
        const handleMouseMove = (e) => {
            if (playheadDragRef.current.isDragging) {
                const activeTimeline = playheadDragRef.current.timelineRef?.current || timelineRef.current;
                const activePixels = playheadDragRef.current.pixelsPerBar || pixelsPerBar;

                if (activeTimeline) {
                    const rect = activeTimeline.getBoundingClientRect();
                    const xOffset = e.clientX - rect.left;

                    let newBar = xOffset / activePixels;
                    newBar = Math.max(0, Math.min(newBar, totalBars));

                    const newProgress = (newBar * parsedBeatsPerBar) / (activeBpm / 60);
                    handleTimelineSeek({ target: { value: newProgress } });
                }
            } else if (cycleDragRef.current.isDragging) {
                const mode = cycleDragRef.current.mode;
                const activePixels = cycleDragRef.current.pixelsPerBar || pixelsPerBar;
                const deltaX = e.clientX - cycleDragRef.current.initialX;
                const deltaBars = deltaX / activePixels;
                const snappedDeltaBars = Math.round(deltaBars * parsedBeatsPerBar) / parsedBeatsPerBar;

                if (mode === 'move') {
                    let newStart = cycleDragRef.current.initialStart + snappedDeltaBars;
                    let newEnd = cycleDragRef.current.initialEnd + snappedDeltaBars;
                    const span = cycleDragRef.current.initialEnd - cycleDragRef.current.initialStart;

                    if (newStart < 0) {
                        newStart = 0;
                        newEnd = span;
                    } else if (newEnd > totalBars) {
                        newEnd = totalBars;
                        newStart = totalBars - span;
                    }
                    setCycleRegion({ startBar: newStart, endBar: newEnd });
                } else if (mode === 'resize-left') {
                    let newStart = cycleDragRef.current.initialStart + snappedDeltaBars;
                    const minimumSpan = 1 / parsedBeatsPerBar;
                    if (newStart < 0) newStart = 0;
                    if (newStart > cycleDragRef.current.initialEnd - minimumSpan) {
                        newStart = cycleDragRef.current.initialEnd - minimumSpan;
                    }
                    setCycleRegion({ startBar: newStart, endBar: cycleDragRef.current.initialEnd });
                } else if (mode === 'resize-right') {
                    let newEnd = cycleDragRef.current.initialEnd + snappedDeltaBars;
                    const minimumSpan = 1 / parsedBeatsPerBar;
                    if (newEnd > totalBars) newEnd = totalBars;
                    if (newEnd < cycleDragRef.current.initialStart + minimumSpan) {
                        newEnd = cycleDragRef.current.initialStart + minimumSpan;
                    }
                    setCycleRegion({ startBar: cycleDragRef.current.initialStart, endBar: newEnd });
                }
            }
        };

        const handleMouseUp = () => {
            if (playheadDragRef.current.isDragging) {
                playheadDragRef.current.isDragging = false;
                document.body.style.cursor = '';
                setIsPlayheadHovered(false);
            }
            if (cycleDragRef.current.isDragging) {
                cycleDragRef.current.isDragging = false;
                document.body.style.cursor = '';
            }
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, [activeBpm, pixelsPerBar, totalBars, parsedBeatsPerBar, handleTimelineSeek, setCycleRegion]);

    return {
        pixelsPerBar,
        setPixelsPerBar,
        parsedBeatsPerBar,
        activeBpm,
        totalBars,
        dynamicDuration,
        dynamicProgress,
        playheadX,
        isPlayheadHovered,
        setIsPlayheadHovered,
        playheadDragRef,
        cycleDragRef,
        timelineRef,
        timelinePlayheadRef,
        rulerPlayheadRef,
        visibleTimelineRange,
        setTimelineScrollContainer,
        handleTimelineSeek,
    };
}
