import React from 'react';

// Let the playhead move smoothly while the expensive viewport scroll advances
// in tiny two-pixel steps. The slight centre tolerance is visually invisible
// but avoids a layout/paint-causing scroll write for every display frame at
// high zoom levels.
const MIN_SCROLL_DELTA_PX = 2;

function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
}

function positionFromTransport(transportRef, audioCtxRef) {
    const transport = transportRef?.current;
    if (!transport) return 0;

    const context = audioCtxRef?.current;
    if (
        transport.isPlaying
        && context
        && Number.isFinite(transport.startTime)
    ) {
        return Math.max(
            0,
            Number(transport.offset || 0)
                + Math.max(0, context.currentTime - transport.startTime) * Number(transport.rate || 1)
        );
    }

    return Math.max(0, Number(transport.position ?? transport.offset ?? 0));
}

function updateElement(element, x) {
    if (!element) return;
    // TimelineRuler's triangle keeps its base transform in CSS so paused
    // seeks can still use its compatibility --timeline-playhead-x path after
    // playback stops. Do not overwrite that transform with an inline value.
    if (element.dataset.timelinePlayhead === 'ruler') {
        element.style.setProperty('--timeline-playhead-x', `${x.toFixed(3)}px`);
        return;
    }
    // `transform` is isolated from document layout. The timeline's static
    // content therefore remains paintable independently from the playhead.
    element.style.transform = `translate3d(${x.toFixed(3)}px, 0, 0) translateX(-50%)`;
}

/**
 * Drive one or more timeline playhead elements directly from the Web Audio
 * clock. This deliberately avoids React state for per-frame movement: a
 * missed browser frame merely advances the next transform to the correct
 * audio-clock position instead of causing a React/scroll recovery cycle.
 *
 * A normal transport starts at the left and lets the playhead reach the
 * viewport centre before scrolling the timeline. A manual ruler seek follows
 * the same rule when it lands in the left half. When it lands in the right
 * half, the viewport is deliberately held still so the user can see the
 * playhead travel naturally to the right edge; it then makes one page shift
 * and resumes the normal left-to-centre behaviour. This avoids a ruler click
 * immediately snapping both canvas and playhead to the centre.
 */
export function useTransportPlayhead({
    audioCtxRef,
    transportRef,
    isPlaying,
    pixelsPerBar,
    bpm,
    beatsPerBar,
    playheadRefs = [],
    scrollContainerRef,
    resetKey = null,
    enabled = true,
}) {
    const configRef = React.useRef(null);
    const frameRef = React.useRef(null);
    const followStateRef = React.useRef({
        mode: 'follow',
        rightHoldStartX: null,
    });

    configRef.current = {
        audioCtxRef,
        transportRef,
        pixelsPerBar,
        bpm,
        beatsPerBar,
        playheadRefs,
        scrollContainerRef,
        resetKey,
        enabled,
    };

    React.useEffect(() => {
        // A different durable job has a fresh timeline and scroll position;
        // it must never inherit a right-side manual seek from the previous
        // project.
        followStateRef.current = { mode: 'follow', rightHoldStartX: null };
    }, [resetKey]);

    const updatePlaybackViewport = (container, x) => {
        const viewportWidth = container.clientWidth;
        if (!Number.isFinite(viewportWidth) || viewportWidth <= 0) return;

        const maxScrollLeft = Math.max(0, container.scrollWidth - viewportWidth);
        const state = followStateRef.current;
        const currentScrollLeft = container.scrollLeft;
        const relativeX = x - currentScrollLeft;

        if (state.mode === 'slide-right') {
            // A separate seek (for example, Go to Beginning) moved transport
            // backwards while a prior right-side manual seek was pending.
            // Return to the normal follow mode rather than retaining stale
            // viewport intent.
            if (state.rightHoldStartX !== null && x < state.rightHoldStartX - 1) {
                state.mode = 'follow';
                state.rightHoldStartX = null;
            } else if (relativeX < viewportWidth) {
                // The user deliberately sought into the right half. Keep the
                // viewport stationary until the natural playhead motion uses
                // the remaining right-side space.
                return;
            } else {
                // The playhead reached the right edge. Page forward once so
                // it appears at the left edge, then let normal centre-follow
                // behaviour take over without a centre snap.
                const pageScrollLeft = clamp(x, 0, maxScrollLeft);
                if (Math.abs(currentScrollLeft - pageScrollLeft) >= MIN_SCROLL_DELTA_PX) {
                    container.scrollLeft = pageScrollLeft;
                }
                state.mode = 'follow';
                state.rightHoldStartX = null;
                return;
            }
        }

        // In normal left-to-centre mode, compare against the *current
        // viewport*. Comparing x only with halfWidth assumes scrollLeft is
        // zero and is the cause of the old post-seek centre jump.
        if (relativeX >= viewportWidth / 2) {
            const targetScrollLeft = clamp(x - viewportWidth / 2, 0, maxScrollLeft);
            if (Math.abs(currentScrollLeft - targetScrollLeft) >= MIN_SCROLL_DELTA_PX) {
                container.scrollLeft = targetScrollLeft;
            }
        }
    };

    const renderTransportPosition = React.useCallback(() => {
        const config = configRef.current;
        if (!config?.enabled) return 0;

        const safeBpm = Number(config.bpm) || 120;
        const safeBeatsPerBar = Number(config.beatsPerBar) || 4;
        const safePixelsPerBar = Number(config.pixelsPerBar) || 100;
        const position = positionFromTransport(config.transportRef, config.audioCtxRef);
        const x = position * (safeBpm / 60) / safeBeatsPerBar * safePixelsPerBar;

        config.playheadRefs.forEach((playheadRef) => updateElement(playheadRef?.current, x));

        const container = config.scrollContainerRef?.current;
        if (container && config.transportRef?.current?.isPlaying) {
            updatePlaybackViewport(container, x);
        }

        return x;
    }, []);

    const notifyManualSeek = React.useCallback(() => {
        const config = configRef.current;
        const container = config?.scrollContainerRef?.current;
        if (container) {
            const safeBpm = Number(config.bpm) || 120;
            const safeBeatsPerBar = Number(config.beatsPerBar) || 4;
            const safePixelsPerBar = Number(config.pixelsPerBar) || 100;
            const position = positionFromTransport(config.transportRef, config.audioCtxRef);
            const x = position * (safeBpm / 60) / safeBeatsPerBar * safePixelsPerBar;
            const relativeX = x - container.scrollLeft;
            const state = followStateRef.current;

            if (relativeX > container.clientWidth / 2) {
                state.mode = 'slide-right';
                state.rightHoldStartX = x;
                console.info('[CloudDSP] Manual timeline seek entered right-side slide mode.');
            } else {
                state.mode = 'follow';
                state.rightHoldStartX = null;
                console.info('[CloudDSP] Manual timeline seek entered left-to-centre follow mode.');
            }
        }

        // Update paused playheads immediately. If playing, the selected mode
        // above ensures this render does not recenter a right-side seek.
        return renderTransportPosition();
    }, [renderTransportPosition]);

    // Apply configuration, seeking, and paused-position changes immediately.
    // The animation loop below owns continuous playback updates.
    React.useLayoutEffect(() => {
        renderTransportPosition();
    }, [
        renderTransportPosition,
        isPlaying,
        pixelsPerBar,
        bpm,
        beatsPerBar,
        enabled,
    ]);

    React.useEffect(() => {
        if (!enabled || !isPlaying) return undefined;

        const renderFrame = () => {
            renderTransportPosition();
            frameRef.current = requestAnimationFrame(renderFrame);
        };

        frameRef.current = requestAnimationFrame(renderFrame);
        return () => {
            if (frameRef.current !== null) {
                cancelAnimationFrame(frameRef.current);
                frameRef.current = null;
            }
        };
    }, [enabled, isPlaying, renderTransportPosition]);

    return { renderTransportPosition, notifyManualSeek };
}
