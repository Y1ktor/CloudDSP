/**
 * TimelineRuler.jsx
 * 
 * Renders the top 30px ruler of the timeline canvas.
 * Manages the playhead scrub zone, the cycle loop drag logic, and draws the measure/beat numbers.
 */
import React from 'react';

/**
 * TimelineRuler
 * 
 * @param {Object} props - Component props
 * @param {number} props.duration - Total duration of the track in seconds
 * @param {number} props.pixelsPerBar - Horizontal zoom scale
 * @param {React.MutableRefObject} props.cycleDragRef - Ref holding cycle drag interaction state
 * @param {Object} props.cycleRegion - The { startBar, endBar } state of the loop zone
 * @param {boolean} props.isCycling - Whether cycling is currently active
 * @param {number} props.totalBars - Total number of bars to draw
 * @param {string} props.timeSignature - e.g., "4/4"
 * @param {React.MutableRefObject} props.timelineRef - Ref to the entire timeline canvas
 * @param {React.MutableRefObject} props.playheadDragRef - Ref holding playhead scrub state
 * @param {Function} props.setIsPlayheadHovered - UI setter for playhead hover glow
 * @param {boolean} props.isPlayheadHovered - Current hover state
 * @param {number} props.playheadX - Calculated absolute pixel position of the playhead
 * @param {number} props.activeBpm - The current BPM for calculation
 * @param {number} props.parsedBeatsPerBar - The integer number of beats per measure
 * @param {Function} props.handleSeek - Callback to seek the audio engine
 */
export default function TimelineRuler({
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
    playheadX,
    activeBpm,
    parsedBeatsPerBar,
    handleSeek
}) {
    if (duration <= 0) return (
        <div style={{ height: '30px', borderRadius: '4px', overflow: 'hidden', position: 'relative' }}>
            <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
                <div style={{ flexGrow: 1, background: '#2a2a2a' }}></div>
                <div style={{ flexGrow: 1, background: '#333' }}></div>
            </div>
        </div>
    );

    return (
        <div style={{ height: '30px', borderRadius: '4px', overflow: 'hidden', position: 'relative' }}>
            <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
                <div style={{ flexGrow: 1, background: '#2a2a2a' }}></div>
                <div style={{ flexGrow: 1, background: '#333' }}></div>
            </div>

            {/* Cycle Region Header Bar */}
            <div 
                onMouseDown={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    cycleDragRef.current = {
                        isDragging: true,
                        mode: 'move',
                        initialX: e.clientX,
                        initialStart: cycleRegion.startBar,
                        initialEnd: cycleRegion.endBar,
                        pixelsPerBar: pixelsPerBar
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
                    zIndex: 25
                }}
            >
                {/* Left Edge Resize Handle */}
                <div 
                    onMouseDown={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        cycleDragRef.current = {
                            isDragging: true,
                            mode: 'resize-left',
                            initialX: e.clientX,
                            initialStart: cycleRegion.startBar,
                            initialEnd: cycleRegion.endBar,
                            pixelsPerBar: pixelsPerBar
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
                        zIndex: 26
                    }}
                />

                {/* Right Edge Resize Handle */}
                <div 
                    onMouseDown={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        cycleDragRef.current = {
                            isDragging: true,
                            mode: 'resize-right',
                            initialX: e.clientX,
                            initialStart: cycleRegion.startBar,
                            initialEnd: cycleRegion.endBar,
                            pixelsPerBar: pixelsPerBar
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
                        zIndex: 26
                    }}
                />
            </div>
            
            {/* Music Bars Overlay */}
            <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, pointerEvents: 'none', zIndex: 30 }}>
                {Array.from({ length: totalBars }).map((_, i) => {
                    const beatsPerBar = parseInt(timeSignature.split('/')[0], 10) || 4;
                    const beatSpacing = pixelsPerBar / beatsPerBar;
                    
                    return (
                        <React.Fragment key={i}>
                            {/* Main Bar Line (Stronger Opacity, spans full height) */}
                            <div style={{ 
                                position: 'absolute', 
                                left: `${i * pixelsPerBar}px`, 
                                top: 0, bottom: 0, 
                                width: '1px', 
                                background: 'rgba(255,255,255,0.18)' 
                            }}>
                                <div style={{ 
                                    position: 'absolute', 
                                    top: '2px', left: '4px', 
                                    color: 'rgba(255,255,255,0.4)', 
                                    fontSize: '10px', 
                                    fontFamily: 'monospace',
                                    lineHeight: '1'
                                }}>
                                    {i + 1}
                                </div>
                            </div>
                            
                            {/* Ruler Measure Lines (Fainter Opacity, bottom half only) */}
                            {Array.from({ length: beatsPerBar - 1 }).map((_, beatIndex) => (
                                <div key={`beat-${i}-${beatIndex}`} style={{ 
                                    position: 'absolute', 
                                    left: `${i * pixelsPerBar + (beatIndex + 1) * beatSpacing}px`, 
                                    top: '50%', bottom: 0, 
                                    width: '1px', 
                                    background: 'rgba(255,255,255,0.06)' 
                                }}></div>
                            ))}
                        </React.Fragment>
                    );
                })}
            </div>

            {/* Global Playhead Scrub Zone (Bottom Ruler) */}
            <div 
                onMouseEnter={() => setIsPlayheadHovered(true)}
                onMouseLeave={() => !playheadDragRef.current.isDragging && setIsPlayheadHovered(false)}
                onMouseDown={(e) => {
                    e.preventDefault();
                    playheadDragRef.current = {
                        isDragging: true,
                        timelineRef: timelineRef,
                        pixelsPerBar: pixelsPerBar
                    };
                    document.body.style.cursor = 'ew-resize';
                    
                    // Instantly jump playhead to the clicked location
                    const rect = timelineRef.current.getBoundingClientRect();
                    const xOffset = e.clientX - rect.left;
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
                    zIndex: 20
                }}
            />

            {/* Static Playhead Triangle (Visual Only) */}
            <div style={{
                position: 'absolute',
                left: `${playheadX}px`,
                transform: 'translateX(-50%)',
                top: 0,
                width: '20px',
                height: '30px',
                zIndex: 15,
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'flex-start',
                pointerEvents: 'none'
            }}>
                <div style={{
                    width: 0, 
                    height: 0, 
                    borderLeft: '4px solid transparent',
                    borderRight: '4px solid transparent',
                    borderTop: `6px solid rgba(255, 255, 255, ${(isPlayheadHovered || playheadDragRef.current.isDragging) ? 1 : 0.7})`,
                    transition: 'border-top-color 0.15s',
                    marginTop: '15px'
                }} />
            </div>
        </div>
    );
}
