import React, { useRef } from 'react';
import TimelineRuler from './TimelineRuler';

/**
 * MidiEditorPopup
 * 
 * @param {Object} props - Component props
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
    activeBpm,
    parsedBeatsPerBar,
    handleSeek,
    parsedMidiStems
}) {
    const popupTimelineRef = useRef(null);
    const pianoScrollRef = useRef(null);

    if (!trackName) return null;

    // Sync vertical scrolling
    const handleGridScroll = (e) => {
        if (pianoScrollRef.current) {
            pianoScrollRef.current.scrollTop = e.target.scrollTop;
        }
    };

    // Render the piano keyboard column with realistic styling
    const renderPianoKeys = () => {
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
                        height: '16px',
                        width: '100%',
                        boxSizing: 'border-box',
                        backgroundColor: '#ddd', // White key background for the right side
                    }}>
                        {/* Middle line to separate the adjacent white keys on the right */}
                        <div style={{
                            position: 'absolute',
                            top: '50%',
                            left: '60%',
                            right: 0,
                            height: '1px',
                            backgroundColor: '#bbb',
                            zIndex: 1
                        }} />
                        
                        {/* The black key itself */}
                        <div style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            width: '60%',
                            height: '100%',
                            backgroundColor: '#1a1a1a',
                            borderBottom: '2px solid #000',
                            borderTop: '1px solid #333',
                            borderRight: '2px solid #000',
                            borderBottomRightRadius: '3px',
                            borderTopRightRadius: '3px',
                            boxSizing: 'border-box',
                            zIndex: 2
                        }} />
                    </div>
                );
            } else {
                // White key row
                // Only C and F need a bottom border because they sit directly above B and E
                const needsBottomBorder = isC || isF;
                
                keys.push(
                    <div key={`key-${i}`} style={{
                        height: '16px',
                        width: '100%',
                        boxSizing: 'border-box',
                        backgroundColor: '#ddd',
                        borderBottom: needsBottomBorder ? '1px solid #bbb' : 'none',
                        color: '#555',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'flex-end',
                        paddingRight: '6px',
                        fontSize: '10px',
                        fontWeight: isC ? 'bold' : 'normal',
                        userSelect: 'none',
                        position: 'relative',
                        zIndex: 3
                    }}>
                        {isC && `C${octave}`}
                    </div>
                );
            }
        }
        return keys;
    };

    // Helper to render full 128 key space notes
    const renderFullMidiNotes = () => {
        if (!parsedMidiStems) return null;
        const stemData = parsedMidiStems[trackName];
        if (!stemData || !stemData.midiData || !stemData.midiData.tracks || stemData.midiData.tracks.length === 0) {
            return null;
        }

        const notes = stemData.midiData.tracks[0].notes;
        if (notes.length === 0) return null;

        const rowHeight = 16; // 16px per piano key

        return notes.map((note, index) => {
            const noteStartBeats = note.time * (activeBpm / 60);
            const noteStartBars = noteStartBeats / parsedBeatsPerBar;
            const leftPx = noteStartBars * pixelsPerBar;

            const noteDurationBeats = note.duration * (activeBpm / 60);
            const noteDurationBars = noteDurationBeats / parsedBeatsPerBar;
            const widthPx = Math.max(2, noteDurationBars * pixelsPerBar);

            // Midi pitch 0-127. 127 is top (0px), 0 is bottom
            const topPx = (127 - note.midi) * rowHeight;

            // Map velocity (0-1) continuously across an HSL color spectrum (Muted / Greyish Heatmap)
            const v = note.velocity || 0.8;
            
            // Hue: Blue(200) -> Green -> Orange -> Red(15)
            const hue = 200 - (v * 185); 
            
            // Saturation: Stays low to prevent vibrance. 25% (pale blue) to 35% (brownish red)
            const saturation = Math.round(25 + (v * 10)); 
            
            // Lightness: 75% (light, pale blue) down to 35% (dark, brownish red)
            const lightness = Math.round(75 - (v * 40)); 
            
            const noteColor = `hsl(${Math.round(hue)}, ${saturation}%, ${lightness}%)`;

            return (
                <div 
                    key={`popup-note-${index}`}
                    style={{
                        position: 'absolute',
                        left: `${leftPx}px`,
                        width: `${widthPx}px`,
                        top: `${topPx}px`,
                        height: `${rowHeight}px`,
                        backgroundColor: noteColor,
                        borderRadius: '2px',
                        boxShadow: '0 0 2px rgba(0,0,0,0.5)',
                        border: '1px solid rgba(255,255,255,0.2)',
                        boxSizing: 'border-box'
                    }}
                    title={`Pitch: ${note.name} (${note.midi}) | Velocity: ${Math.round((note.velocity || 0) * 100)}%`}
                />
            );
        });
    };

    // 128 rows * 16px = 2048px total height
    const gridHeight = 128 * 16;

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
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                <h3 style={{ margin: 0, color: '#fff', textTransform: 'capitalize', fontSize: '24px' }}>
                    MIDI Editor: {trackName}
                </h3>
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
                {/* Left Column: Piano Keys */}
                <div style={{ width: '60px', flexShrink: 0, display: 'flex', flexDirection: 'column', backgroundColor: '#111' }}>
                    {/* Empty top left corner to match the 30px TimelineRuler */}
                    <div style={{ height: '30px', backgroundColor: '#1a1a1a', borderBottom: '1px solid #333', borderRight: '1px solid #222', flexShrink: 0 }} />
                    
                    {/* The keys themselves (sync scrolled) */}
                    <div ref={pianoScrollRef} style={{ flexGrow: 1, overflow: 'hidden', opacity: 0.6 }}>
                        <div style={{ height: `${gridHeight}px` }}>
                            {renderPianoKeys()}
                        </div>
                    </div>
                </div>

                {/* Right Column: Scrollable Grid */}
                <div 
                    style={{ flexGrow: 1, overflow: 'auto', position: 'relative' }}
                    onScroll={handleGridScroll}
                >
                <div 
                    ref={popupTimelineRef} 
                    style={{ 
                        minWidth: `${pixelsPerBar * totalBars}px`, 
                        minHeight: `${gridHeight + 30}px`, // 30px for ruler
                        position: 'relative' 
                    }}
                >
                    {/* Time Indicator (Playhead) */}
                    {duration > 0 && (
                        <div style={{
                            position: 'absolute',
                            left: `${playheadX}px`,
                            transform: 'translateX(-50%)',
                            top: '15px',
                            bottom: 0,
                            width: '1px',
                            backgroundColor: '#fff',
                            zIndex: 10,
                            pointerEvents: 'none',
                            boxShadow: '0 0 4px rgba(255, 255, 255, 0.5)'
                        }} />
                    )}

                    {/* Ruler */}
                    <TimelineRuler 
                        duration={duration}
                        pixelsPerBar={pixelsPerBar}
                        cycleDragRef={cycleDragRef}
                        cycleRegion={cycleRegion}
                        isCycling={isCycling}
                        totalBars={totalBars}
                        timeSignature={timeSignature}
                        timelineRef={popupTimelineRef} // Use local ref!
                        playheadDragRef={playheadDragRef}
                        setIsPlayheadHovered={setIsPlayheadHovered}
                        isPlayheadHovered={isPlayheadHovered}
                        playheadX={playheadX}
                        activeBpm={activeBpm}
                        parsedBeatsPerBar={parsedBeatsPerBar}
                        handleSeek={handleSeek}
                    />

                    {/* MIDI Grid */}
                    <div style={{
                        position: 'relative',
                        width: '100%',
                        height: `${gridHeight}px`,
                        marginTop: '0px',
                        backgroundSize: `100% 16px`,
                        backgroundImage: `linear-gradient(to bottom, transparent 15px, rgba(255,255,255,0.05) 16px)`
                    }}>
                        {renderFullMidiNotes()}
                    </div>
                </div>
            </div>
        </div>
        </div>
    );
}
