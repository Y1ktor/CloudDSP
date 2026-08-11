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
 * Once the transport crosses the viewport centre, the playhead stays there
 * and the scroll viewport follows the timeline. The follow calculation has no
 * heuristic "seek" state: explicit seeks update the transport position and
 * are rendered on the next frame just like all other transport changes.
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
    enabled = true,
}) {
    const configRef = React.useRef(null);
    const frameRef = React.useRef(null);

    configRef.current = {
        audioCtxRef,
        transportRef,
        pixelsPerBar,
        bpm,
        beatsPerBar,
        playheadRefs,
        scrollContainerRef,
        enabled,
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
            const halfWidth = container.clientWidth / 2;
            const maxScrollLeft = Math.max(0, container.scrollWidth - container.clientWidth);
            const targetScrollLeft = x > halfWidth
                ? clamp(x - halfWidth, 0, maxScrollLeft)
                : 0;

            // Avoid a layout-affecting programmatic scroll for sub-pixel
            // changes. This also prevents high-refresh displays from issuing
            // two expensive scroll writes for the same visual pixel.
            if (Math.abs(container.scrollLeft - targetScrollLeft) >= MIN_SCROLL_DELTA_PX) {
                container.scrollLeft = targetScrollLeft;
            }
        }

        return x;
    }, []);

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

    return renderTransportPosition;
}
