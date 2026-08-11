import React from 'react';

/**
 * The line counterpart to TimelineRuler's transform-driven triangle.
 *
 * While playback is active the parent still performs occasional UI renders
 * for the time readout. This memo boundary prevents those renders from
 * overwriting the rAF-written transform with an older throttled position.
 */
const TransportPlayheadLine = React.memo(function TransportPlayheadLine({
    playheadRef,
    fallbackX,
    top = '15px',
    bottom = 0,
}) {
    return (
        <div
            ref={playheadRef}
            data-timeline-playhead="line"
            style={{
                position: 'absolute',
                left: 0,
                transform: `translate3d(${Number(fallbackX || 0).toFixed(3)}px, 0, 0) translateX(-50%)`,
                top,
                bottom,
                width: '1px',
                backgroundColor: '#fff',
                zIndex: 10,
                pointerEvents: 'none',
                boxShadow: '0 0 4px rgba(255, 255, 255, 0.5)',
                willChange: 'transform',
            }}
        />
    );
}, (previous, next) => {
    // Direct transport animation owns all in-playback pixel movement.
    if (previous.isPlaying && next.isPlaying) return true;
    return previous.isPlaying === next.isPlaying
        && previous.fallbackX === next.fallbackX
        && previous.top === next.top
        && previous.bottom === next.bottom;
});

export default TransportPlayheadLine;
