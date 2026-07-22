import { useRef, useLayoutEffect } from 'react';

/**
 * usePlayheadScroll
 * 
 * A custom hook that manages the smart auto-scrolling state machine for a timeline canvas.
 * It enforces the following rules:
 * - If the playhead is in the left half of the viewport, it slides visually until it hits the center.
 * - Once in the center, it "rolls" the background to keep the playhead centered.
 * - If the user manually seeks to the right half, it "slides-right" until it hits the right edge,
 *   at which point it performs a page shift to snap the playhead back to the left edge.
 * - Safely handles out-of-bounds seeks by instantly snapping the viewport.
 * 
 * @param {React.MutableRefObject<HTMLElement>} scrollContainerRef - Ref to the scrollable container div.
 * @param {number} playheadX - The absolute pixel position of the playhead.
 * @param {boolean} isPlaying - Whether playback is currently active.
 */
export function usePlayheadScroll(scrollContainerRef, playheadX, isPlaying) {
    const scrollStateRef = useRef('roll');

    useLayoutEffect(() => {
        if (isPlaying && scrollContainerRef.current) {
            const container = scrollContainerRef.current;
            const halfWidth = container.clientWidth / 2;
            const width = container.clientWidth;
            
            let relativeX = playheadX - container.scrollLeft;

            // 1. Detect out-of-bounds (e.g. Go to Beginning or hitting the edge)
            if (relativeX < 0 || relativeX >= width) {
                if (scrollStateRef.current === 'slide-right' && relativeX >= width) {
                    // Intended page shift: playhead reached the right edge
                    container.scrollLeft = playheadX;
                    relativeX = playheadX - container.scrollLeft;
                    scrollStateRef.current = 'roll';
                } else {
                    // Unexpected jump out of bounds (e.g. seek off-screen), snap to center
                    container.scrollLeft = Math.max(0, playheadX - halfWidth);
                    relativeX = playheadX - container.scrollLeft;
                    scrollStateRef.current = 'roll';
                }
            }
            
            // 2. Detect a click/jump in the right section while we were rolling
            if (scrollStateRef.current === 'roll' && relativeX > halfWidth + 5) {
                scrollStateRef.current = 'slide-right';
            }
            
            // 3. Detect a click/jump to the left section
            if (relativeX < halfWidth - 5) {
                scrollStateRef.current = 'roll';
            }

            // 4. Execute behavior
            if (scrollStateRef.current === 'roll') {
                if (relativeX >= halfWidth) {
                    container.scrollLeft = playheadX - halfWidth;
                }
            }
        }
    }, [playheadX, isPlaying, scrollContainerRef]);
}
