/**
 * TimelineRuler.jsx
 *
 * Renders the top 30px ruler of the timeline canvas. Static ruler marks are
 * memoised separately from the moving playhead so a transport frame only
 * updates a compositor-friendly transform on one DOM node.
 */
import React from 'react';

const DEFAULT_RULER_OVERSCAN_PX = 360;

function assignRef(ref, node) {
    if (typeof ref === 'function') {
        ref(node);
    } else if (ref) {
        ref.current = node;
    }
}

function normaliseVisibleRange(visibleRange) {
    const startPx = Number(visibleRange?.startPx);
    const endPx = Number(visibleRange?.endPx);
    const overscanPx = Number(visibleRange?.overscanPx);

    if (!Number.isFinite(startPx) || !Number.isFinite(endPx) || endPx <= startPx) {
        return { startPx: null, endPx: null, overscanPx: DEFAULT_RULER_OVERSCAN_PX };
    }

    return {
        startPx: Math.max(0, startPx),
        endPx,
        overscanPx: Number.isFinite(overscanPx) && overscanPx >= 0
            ? overscanPx
            : DEFAULT_RULER_OVERSCAN_PX,
    };
}

const RulerMarks = React.memo(function RulerMarks({
    totalBars,
    timeSignature,
    pixelsPerBar,
    visibleStartPx,
    visibleEndPx,
    overscanPx,
}) {
    const beatsPerBar = parseInt(timeSignature.split('/')[0], 10) || 4;
    const { firstBar, lastBar, beatSpacing } = React.useMemo(() => {
        if (totalBars <= 0 || pixelsPerBar <= 0) {
            return { firstBar: 0, lastBar: -1, beatSpacing: 0 };
        }

        if (visibleStartPx === null || visibleEndPx === null) {
            return { firstBar: 0, lastBar: totalBars - 1, beatSpacing: pixelsPerBar / beatsPerBar };
        }

        const start = Math.max(0, visibleStartPx - overscanPx);
        const end = visibleEndPx + overscanPx;
        return {
            firstBar: Math.max(0, Math.floor(start / pixelsPerBar)),
            lastBar: Math.min(totalBars - 1, Math.ceil(end / pixelsPerBar)),
            beatSpacing: pixelsPerBar / beatsPerBar,
        };
    }, [beatsPerBar, overscanPx, pixelsPerBar, totalBars, visibleEndPx, visibleStartPx]);

    const bars = [];
    for (let barIndex = firstBar; barIndex <= lastBar; barIndex += 1) {
        const barLeft = barIndex * pixelsPerBar;
        bars.push(
            <React.Fragment key={barIndex}>
                <div style={{
                    position: 'absolute',
                    left: `${barLeft}px`,
                    top: 0,
                    bottom: 0,
                    width: '1px',
                    background: 'rgba(255,255,255,0.18)',
                }}>
                    <div style={{
                        position: 'absolute',
                        top: '2px',
                        left: '4px',
                        color: 'rgba(255,255,255,0.4)',
                        fontSize: '10px',
                        fontFamily: 'monospace',
                        lineHeight: '1',
                    }}>
                        {barIndex + 1}
                    </div>
                </div>
                {Array.from({ length: beatsPerBar - 1 }, (_, beatIndex) => (
                    <div key={`beat-${barIndex}-${beatIndex}`} style={{
                        position: 'absolute',
                        left: `${barLeft + (beatIndex + 1) * beatSpacing}px`,
                        top: '50%',
                        bottom: 0,
                        width: '1px',
                        background: 'rgba(255,255,255,0.06)',
                    }} />
                ))}
            </React.Fragment>,
        );
    }

    return (
        <div style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            pointerEvents: 'none',
            zIndex: 30,
            contain: 'paint',
        }}>
            {bars}
        </div>
    );
});

const TimelineRulerBody = React.memo(function TimelineRulerBody({
    duration,
    pixelsPerBar,
    cycleDragRef,
    cycleRegion,
    isCycling,
    totalBars,
    timeSignature,
    timelineRef,
    playheadDragRef,
    setIsPlayheadHovered,
    isPlayheadHovered,
    playheadElementRef,
    activeBpm,
    parsedBeatsPerBar,
    handleSeek,
    visibleStartPx,
    visibleEndPx,
    overscanPx,
}) {
    if (duration <= 0) return (
        <div style={{ height: '30px', borderRadius: '4px', overflow: 'hidden', position: 'relative' }}>
            <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
                <div style={{ flexGrow: 1, background: '#2a2a2a' }} />
                <div style={{ flexGrow: 1, background: '#333' }} />
            </div>
        </div>
    );

    return (
        <div style={{ height: '30px', borderRadius: '4px', overflow: 'hidden', position: 'relative', contain: 'paint' }}>
            <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
                <div style={{ flexGrow: 1, background: '#2a2a2a' }} />
                <div style={{ flexGrow: 1, background: '#333' }} />
            </div>

            {/* Cycle Region Header Bar */}
            <div
                onMouseDown={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    cycleDragRef.current = {
                        isDragging: true,
                        mode: 'move',
                        initialX: event.clientX,
                        initialStart: cycleRegion.startBar,
                        initialEnd: cycleRegion.endBar,
                        pixelsPerBar,
                    };
                    document.body.style.cursor = 'grab';
                }}
                style={{
                    position: 'absolute',
                    left: `${cycleRegion.startBar * pixelsPerBar}px`,
                    width: `${(cycleRegion.endBar - cycleRegion.startBar) * pixelsPerBar}px`,
                    top: 0,
                    bottom: '50%',
                    backgroundColor: isCycling ? '#8B6508' : 'rgba(255, 255, 255, 0.15)',
                    cursor: 'grab',
                    zIndex: 25,
                }}
            >
                {/* Left Edge Resize Handle */}
                <div
                    onMouseDown={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        cycleDragRef.current = {
                            isDragging: true,
                            mode: 'resize-left',
                            initialX: event.clientX,
                            initialStart: cycleRegion.startBar,
                            initialEnd: cycleRegion.endBar,
                            pixelsPerBar,
                        };
                        document.body.style.cursor = 'ew-resize';
                    }}
                    style={{
                        position: 'absolute',
                        left: 0,
                        top: 0,
                        bottom: 0,
                        width: '8px',
                        cursor: 'ew-resize',
                        zIndex: 26,
                    }}
                />

                {/* Right Edge Resize Handle */}
                <div
                    onMouseDown={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        cycleDragRef.current = {
                            isDragging: true,
                            mode: 'resize-right',
                            initialX: event.clientX,
                            initialStart: cycleRegion.startBar,
                            initialEnd: cycleRegion.endBar,
                            pixelsPerBar,
                        };
                        document.body.style.cursor = 'ew-resize';
                    }}
                    style={{
                        position: 'absolute',
                        right: 0,
                        top: 0,
                        bottom: 0,
                        width: '8px',
                        cursor: 'ew-resize',
                        zIndex: 26,
                    }}
                />
            </div>

            <RulerMarks
                totalBars={totalBars}
                timeSignature={timeSignature}
                pixelsPerBar={pixelsPerBar}
                visibleStartPx={visibleStartPx}
                visibleEndPx={visibleEndPx}
                overscanPx={overscanPx}
            />

            {/* Global Playhead Scrub Zone (Bottom Ruler) */}
            <div
                onMouseEnter={() => setIsPlayheadHovered(true)}
                onMouseLeave={() => !playheadDragRef.current.isDragging && setIsPlayheadHovered(false)}
                onMouseDown={(event) => {
                    event.preventDefault();
                    playheadDragRef.current = {
                        isDragging: true,
                        timelineRef,
                        pixelsPerBar,
                    };
                    document.body.style.cursor = 'ew-resize';

                    // Instantly jump playhead to the clicked location.
                    const rect = timelineRef.current.getBoundingClientRect();
                    const xOffset = event.clientX - rect.left;
                    let newBar = xOffset / pixelsPerBar;
                    newBar = Math.max(0, Math.min(newBar, totalBars));
                    const newProgress = (newBar * parsedBeatsPerBar) / (activeBpm / 60);
                    handleSeek({ target: { value: newProgress } });
                }}
                style={{
                    position: 'absolute',
                    left: 0,
                    right: 0,
                    top: '15px',
                    height: '15px',
                    cursor: 'ew-resize',
                    zIndex: 20,
                }}
            />

            {/*
              The outer wrapper updates --timeline-playhead-x directly from
              the transport. Keep left at zero so playhead motion is transform
              only; root callers can also hold this element through a ref.
            */}
            <div
                ref={playheadElementRef}
                data-timeline-playhead="ruler"
                style={{
                    position: 'absolute',
                    left: 0,
                    transform: 'translate3d(var(--timeline-playhead-x, 0px), 0, 0) translateX(-50%)',
                    top: 0,
                    width: '20px',
                    height: '30px',
                    zIndex: 15,
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'flex-start',
                    pointerEvents: 'none',
                    willChange: 'transform',
                }}
            >
                <div style={{
                    width: 0,
                    height: 0,
                    borderLeft: '4px solid transparent',
                    borderRight: '4px solid transparent',
                    borderTop: `6px solid rgba(255, 255, 255, ${(isPlayheadHovered || playheadDragRef.current.isDragging) ? 1 : 0.7})`,
                    transition: 'border-top-color 0.15s',
                    marginTop: '15px',
                }} />
            </div>
        </div>
    );
});

/**
 * @param {Object} props
 * @param {number} props.duration - Total duration of the track in seconds.
 * @param {number} props.pixelsPerBar - Horizontal zoom scale.
 * @param {React.MutableRefObject} props.cycleDragRef - Cycle drag state.
 * @param {Object} props.cycleRegion - The { startBar, endBar } loop zone.
 * @param {boolean} props.isCycling - Whether cycling is active.
 * @param {number} props.totalBars - Total bars in the timeline.
 * @param {string} props.timeSignature - e.g. "4/4".
 * @param {React.MutableRefObject} props.timelineRef - Entire timeline canvas.
 * @param {React.MutableRefObject} props.playheadDragRef - Playhead drag state.
 * @param {Function} props.setIsPlayheadHovered - Hover UI setter.
 * @param {boolean} props.isPlayheadHovered - Current hover state.
 * @param {number} props.playheadX - Compatibility fallback playhead position.
 * @param {React.MutableRefObject|Function} props.playheadElementRef - Optional
 * direct reference to the triangle. Write `--timeline-playhead-x` on it from
 * a requestAnimationFrame transport loop to avoid React playback renders.
 * @param {{ startPx: number, endPx: number, overscanPx?: number }=} props.visibleRange
 * Optional absolute-pixel viewport window used to mount only visible marks.
 * @param {boolean} props.isPlayheadExternallyDriven - When true, do not apply
 * the compatibility playheadX value after mount.
 */
const TimelineRuler = React.forwardRef(function TimelineRuler({
    playheadX = 0,
    playheadElementRef,
    visibleRange,
    isPlayheadExternallyDriven = false,
    handleSeek,
    ...props
}, forwardedPlayheadRef) {
    const internalPlayheadRef = React.useRef(null);
    const seekHandlerRef = React.useRef(handleSeek);
    seekHandlerRef.current = handleSeek;
    const { startPx, endPx, overscanPx } = normaliseVisibleRange(visibleRange);

    const setPlayheadElementRef = React.useCallback((node) => {
        internalPlayheadRef.current = node;
        assignRef(playheadElementRef, node);
        assignRef(forwardedPlayheadRef, node);
    }, [forwardedPlayheadRef, playheadElementRef]);

    const stableHandleSeek = React.useCallback((event) => {
        seekHandlerRef.current?.(event);
    }, []);

    // Existing callers can continue supplying playheadX and still avoid a
    // ruler re-render. New transport code should pass
    // isPlayheadExternallyDriven and write the CSS variable itself each frame.
    React.useLayoutEffect(() => {
        if (isPlayheadExternallyDriven) return;
        const node = internalPlayheadRef.current;
        const position = Number(playheadX);
        if (node && Number.isFinite(position)) {
            node.style.setProperty('--timeline-playhead-x', `${position}px`);
        }
    }, [isPlayheadExternallyDriven, playheadX]);

    return (
        <TimelineRulerBody
            {...props}
            handleSeek={stableHandleSeek}
            playheadElementRef={setPlayheadElementRef}
            visibleStartPx={startPx}
            visibleEndPx={endPx}
            overscanPx={overscanPx}
        />
    );
});

export default TimelineRuler;
