import React from 'react';

const DEFAULT_TILE_PX = 720;
const DEFAULT_OVERSCAN_PX = 480;

function viewportSnapshot(container, tilePx, overscanPx) {
    if (!container) {
        return { startPx: 0, endPx: 0, overscanPx };
    }

    const startPx = Math.max(0, Math.floor(container.scrollLeft / tilePx) * tilePx);
    const endPx = Math.max(
        startPx + tilePx,
        Math.ceil((container.scrollLeft + container.clientWidth) / tilePx) * tilePx,
    );
    return { startPx, endPx, overscanPx };
}

/**
 * Keeps a tile-quantised viewport window for static timeline content.
 *
 * Native scroll events may occur at the display refresh rate. Quantising the
 * values means virtualised MIDI notes and ruler marks remount only when the
 * viewport crosses a tile boundary, rather than once per playhead frame.
 *
 * @returns {[{ startPx: number, endPx: number, overscanPx: number }, Function]}
 * The viewport window and a callback ref for the scroll container. The
 * callback is required because a conditional timeline mount does not notify a
 * plain mutable ref or rerun an already-completed layout effect.
 */
export function useTimelineViewport(scrollContainerRef, {
    tilePx = DEFAULT_TILE_PX,
    overscanPx = DEFAULT_OVERSCAN_PX,
} = {}) {
    const initialRange = React.useMemo(() => ({
        startPx: 0,
        endPx: tilePx,
        overscanPx,
    }), [overscanPx, tilePx]);
    const [visibleRange, setVisibleRange] = React.useState(initialRange);
    const visibleRangeRef = React.useRef(initialRange);
    // A timeline can be conditionally mounted after this hook first runs (the
    // main workspace is hidden while a job is still awaiting artifacts). A
    // mutable ref changing from null to an element does not cause React to
    // rerun an effect, so retain the actual node in state as well. Consumers
    // attach `setScrollContainer` to the scrollable element while continuing
    // to use their supplied ref for imperative transport scrolling.
    const [scrollContainer, setScrollContainerNode] = React.useState(null);

    const setScrollContainer = React.useCallback((node) => {
        scrollContainerRef.current = node;
        setScrollContainerNode((previousNode) => (
            previousNode === node ? previousNode : node
        ));
    }, [scrollContainerRef]);

    // Configuration changes are rare, but make the ref agree before a scroll
    // event compares against it.
    React.useEffect(() => {
        visibleRangeRef.current = visibleRange;
    }, [visibleRange]);

    const updateViewport = React.useCallback(() => {
        const next = viewportSnapshot(scrollContainer, tilePx, overscanPx);
        const previous = visibleRangeRef.current;
        // Programmatic transport scrolling may emit an event at every display
        // frame. Do not even enqueue a React update until the viewport crosses
        // a tile boundary.
        if (
            previous.startPx === next.startPx
            && previous.endPx === next.endPx
            && previous.overscanPx === next.overscanPx
        ) return;
        visibleRangeRef.current = next;
        setVisibleRange(next);
    }, [overscanPx, scrollContainer, tilePx]);

    React.useLayoutEffect(() => {
        const container = scrollContainer;
        if (!container) return undefined;

        updateViewport();
        container.addEventListener('scroll', updateViewport, { passive: true });
        const resizeObserver = typeof ResizeObserver === 'undefined'
            ? null
            : new ResizeObserver(updateViewport);
        resizeObserver?.observe(container);

        return () => {
            container.removeEventListener('scroll', updateViewport);
            resizeObserver?.disconnect();
        };
    }, [scrollContainer, updateViewport]);

    return [visibleRange, setScrollContainer];
}
