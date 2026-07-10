import React, { useRef } from 'react';
import TimelineRuler from './TimelineRuler';
import { useMidiSynth } from '../hooks/useMidiSynth';

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
    handleGoToBeginning,
    isPlaying,
    togglePlay,
    toggleCycling,
    mutedTracks,
    soloedTracks,
    toggleMute,
    toggleSolo,
    activeBpm,
    parsedBeatsPerBar,
    handleSeek,
    parsedMidiStems,
    setParsedMidiStems,
    audioCtxRef,
    progress,
    globalSynthRef,
    isMidiMode,
    setIsMidiMode
}) {
    const popupTimelineRef = useRef(null);
    const pianoScrollRef = useRef(null);
    const gridScrollRef = useRef(null);

    // Track drag state


    // MIDI Play Mode hook
    const { auditionNote } = useMidiSynth(
        audioCtxRef,
        progress,
        isPlaying,
        parsedMidiStems,
        trackName,
        activeBpm,
        globalSynthRef,
        isMidiMode
    );
    
    // Local zoom states for the popup (independent of the main app)
    const [popupPixelsPerBar, setPopupPixelsPerBar] = React.useState(pixelsPerBar || 100);
    const [popupRowHeight, setPopupRowHeight] = React.useState(8); // Default to 8 (lowest)
    
    // Selection state for MIDI notes (single selection)
    const [selectedNoteIndex, setSelectedNoteIndex] = React.useState(null);
    


    // Center scroll on C4 (MIDI 60) when opened
    React.useEffect(() => {
        if (trackName && gridScrollRef.current) {
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
    }, [trackName]); // Only run when popup opens (trackName changes)

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
                        height: `${popupRowHeight}px`,
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
                        height: `${popupRowHeight}px`,
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

    // Velocity Control Logic
    let commonVelocity = 0.8;
    if (selectedNoteIndex !== null && parsedMidiStems && parsedMidiStems[trackName] && parsedMidiStems[trackName].midiData.tracks[0].notes.length > 0) {
        const note = parsedMidiStems[trackName].midiData.tracks[0].notes[selectedNoteIndex];
        if (note) {
            commonVelocity = note.velocity !== undefined ? Math.max(0.01, note.velocity) : 0.8;
        }
    }

    const handleVelocityChange = (e) => {
        const newVelocity = parseFloat(e.target.value);
        if (selectedNoteIndex === null || !parsedMidiStems) return;

        // CRITICAL: We must directly mutate the @tonejs/midi Note instance.
        // If we use spread operators like { ...note }, we strip away all of Tone.js's 
        // internal prototype getters (like .time, .duration) and the note disappears!
        const note = parsedMidiStems[trackName].midiData.tracks[0].notes[selectedNoteIndex];
        if (note) {
            note.velocity = newVelocity;
        }

        // Trigger a React re-render by shallow cloning the top-level dictionary
        setParsedMidiStems({ ...parsedMidiStems });
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

        const toggleNoteSelection = (index, e) => {
            e.stopPropagation();
            if (selectedNoteIndex === index) {
                setSelectedNoteIndex(null);
            } else {
                setSelectedNoteIndex(index);
                // Audition the note immediately!
                const note = parsedMidiStems[trackName].midiData.tracks[0].notes[index];
                if (note) {
                    auditionNote(note);
                }
            }
        };

        return notes.map((note, index) => {
            const noteStartBeats = note.time * (activeBpm / 60);
            const noteStartBars = noteStartBeats / parsedBeatsPerBar;
            const leftPx = noteStartBars * popupPixelsPerBar;

            const noteDurationBeats = note.duration * (activeBpm / 60);
            const noteDurationBars = noteDurationBeats / parsedBeatsPerBar;
            const widthPx = Math.max(2, noteDurationBars * popupPixelsPerBar);

            // Midi pitch 0-127. 127 is top (0px), 0 is bottom
            const topPx = (127 - note.midi) * popupRowHeight;

            // Map velocity (0-1) continuously across an HSL color spectrum (Muted / Greyish Heatmap)
            const v = note.velocity !== undefined ? Math.max(0.01, note.velocity) : 0.8;
            
            // Hue: Blue(200) -> Green -> Orange -> Red(15)
            const hue = 200 - (v * 185); 
            
            // Saturation: Stays low to prevent vibrance. 25% (pale blue) to 35% (brownish red)
            const saturation = Math.round(25 + (v * 10)); 
            
            // Lightness: 75% (light, pale blue) down to 35% (dark, brownish red)
            const lightness = Math.round(75 - (v * 40)); 
            
            const noteColor = `hsl(${Math.round(hue)}, ${saturation}%, ${lightness}%)`;

            const isSelected = selectedNoteIndex === index;

            return (
                <div 
                    key={`popup-note-${index}`}
                    onClick={(e) => toggleNoteSelection(index, e)}
                    style={{
                        position: 'absolute',
                        left: `${leftPx}px`,
                        width: `${widthPx}px`,
                        top: `${topPx}px`,
                        height: `${popupRowHeight}px`,
                        backgroundColor: noteColor,
                        borderRadius: '2px',
                        boxShadow: isSelected ? '0 0 0 1px rgba(255,255,255,0.6), 0 0 4px rgba(255,255,255,0.4)' : '0 0 2px rgba(0,0,0,0.5)',
                        border: isSelected ? '1px solid rgba(255,255,255,0.6)' : '1px solid rgba(255,255,255,0.2)',
                        boxSizing: 'border-box',
                        cursor: 'pointer',
                        zIndex: isSelected ? 10 : 1
                    }}
                    title={`Pitch: ${note.name} (${note.midi}) | Velocity: ${Math.round((note.velocity || 0) * 100)}%`}
                />
            );
        });
    };

    // 128 rows * popupRowHeight total height
    const gridHeight = 128 * popupRowHeight;
    
    // Scale the playhead position to match the popup's local zoom level
    const popupPlayheadX = (playheadX / pixelsPerBar) * popupPixelsPerBar;

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
            
            {/* Control Bar */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: '30px', 
                backgroundColor: '#1a1a1a', padding: '10px 15px', 
                borderRadius: '8px', marginBottom: '15px', border: '1px solid #333'
            }}>
                {/* Transport Controls */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                    <button title="Go to Beginning" onClick={handleGoToBeginning} style={{
                        background: 'transparent', color: 'white', border: 'none',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                    }}>
                        <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
                    </button>

                    <button title="Play/Pause" onClick={togglePlay} style={{
                        background: 'transparent', color: 'white', border: 'none',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
                    }}>
                        {isPlaying ? (
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                        ) : (
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                        )}
                    </button>

                    <button title="Toggle Cycle" onClick={toggleCycling} style={{
                        background: isCycling ? '#8B6508' : 'transparent', 
                        color: isCycling ? '#fff' : 'white', 
                        border: 'none', borderRadius: '4px',
                        cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '2px', 
                        opacity: 0.8,
                        transition: 'background-color 0.2s'
                    }}>
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
                    </button>
                </div>

                <div style={{ width: '1px', height: '24px', backgroundColor: '#333' }}></div>

                {/* Mute and Solo Controls */}
                <div style={{ display: 'flex', gap: '8px' }}>
                    <button onClick={() => toggleMute(trackName)} style={{
                        width: '24px', height: '24px',
                        background: mutedTracks[trackName] ? '#e53935' : '#555',
                        color: 'white', border: 'none', borderRadius: '4px', 
                        cursor: 'pointer', fontSize: '11px', fontWeight: 'bold',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'background-color 0.2s'
                    }} title="Mute Track">
                        M
                    </button>
                    <button onClick={() => toggleSolo(trackName)} style={{
                        width: '24px', height: '24px',
                        background: soloedTracks[trackName] ? '#e0a800' : '#555',
                        color: soloedTracks[trackName] ? '#fff' : 'white', 
                        border: 'none', borderRadius: '4px', 
                        cursor: 'pointer', fontSize: '11px', fontWeight: 'bold',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'background-color 0.2s'
                    }} title="Solo Track">
                        S
                    </button>
                </div>

                <div style={{ width: '1px', height: '24px', backgroundColor: '#333', marginLeft: '10px', marginRight: '10px' }}></div>

                {/* MIDI Play Mode Toggle */}
                <button onClick={() => setIsMidiMode(!isMidiMode)} style={{
                    height: '24px', padding: '0 10px',
                    background: 'transparent',
                    color: isMidiMode ? '#4CAF50' : '#aaa', 
                    border: `1px solid ${isMidiMode ? '#4CAF50' : '#555'}`, 
                    boxShadow: isMidiMode ? '0 0 8px rgba(76, 175, 80, 0.5)' : 'none',
                    borderRadius: '4px',
                    cursor: 'pointer', fontSize: '12px', fontWeight: 'bold',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.2s'
                }} title="Toggle MIDI Synthesis Playback">
                    MIDI
                </button>

                {/* Spacer to push sliders to the right */}
                <div style={{ flexGrow: 1 }}></div>

                {/* Velocity Control */}
                {selectedNoteIndex !== null && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginRight: '20px', borderRight: '1px solid #333', paddingRight: '20px' }}>
                        <span style={{ color: '#aaa', fontSize: '12px', fontWeight: 'bold' }}>
                            Velocity: {Math.round(commonVelocity * 100)}
                        </span>
                        <input 
                            type="range" 
                            min="0.01" max="1" step="0.01"
                            value={commonVelocity}
                            onChange={handleVelocityChange}
                            style={{ width: '80px', cursor: 'pointer', accentColor: '#aaa' }}
                        />
                    </div>
                )}

                {/* Zoom Controls */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }} title="Horizontal Zoom">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="#aaa">
                            <path d="M22 12l-4-4v3H6V8l-4 4 4 4v-3h12v3z"/>
                        </svg>
                        <input 
                            type="range" 
                            min="20" max="400" 
                            value={popupPixelsPerBar}
                            onChange={(e) => setPopupPixelsPerBar(Number(e.target.value))}
                            style={{ width: '80px', cursor: 'pointer' }}
                        />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }} title="Vertical Zoom">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="#aaa">
                            <path d="M12 2L8 6h3v12H8l4 4 4-4h-3V6h3z"/>
                        </svg>
                        <input 
                            type="range" 
                            min="8" max="32" 
                            value={popupRowHeight}
                            onChange={(e) => setPopupRowHeight(Number(e.target.value))}
                            style={{ width: '80px', cursor: 'pointer' }}
                        />
                    </div>
                </div>
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
                    ref={gridScrollRef}
                    style={{ flexGrow: 1, overflow: 'auto', position: 'relative' }}
                    onScroll={handleGridScroll}
                >
                    <div 
                        ref={popupTimelineRef} 
                        style={{ 
                            minWidth: `${popupPixelsPerBar * totalBars}px`, 
                            minHeight: `${gridHeight + 30}px`, // 30px for ruler
                            position: 'relative' 
                        }}
                    >
                        {/* Time Indicator (Playhead) */}
                    {duration > 0 && (
                        <div style={{
                            position: 'absolute',
                            left: `${popupPlayheadX}px`,
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
                    <div style={{ position: 'sticky', top: 0, zIndex: 20 }}>
                        <TimelineRuler 
                            duration={duration}
                            pixelsPerBar={popupPixelsPerBar}
                            cycleDragRef={cycleDragRef}
                            cycleRegion={cycleRegion}
                            isCycling={isCycling}
                            totalBars={totalBars}
                            timeSignature={timeSignature}
                            timelineRef={popupTimelineRef} // Use local ref!
                            playheadDragRef={playheadDragRef}
                            setIsPlayheadHovered={setIsPlayheadHovered}
                            isPlayheadHovered={isPlayheadHovered}
                            playheadX={popupPlayheadX}
                            activeBpm={activeBpm}
                            parsedBeatsPerBar={parsedBeatsPerBar}
                            handleSeek={handleSeek}
                        />
                    </div>

                    {/* MIDI Grid */}
                    <div 
                        onClick={() => setSelectedNoteIndex(null)}
                        style={{
                        position: 'relative',
                        width: '100%',
                        height: `${gridHeight}px`,
                        marginTop: '0px',
                        backgroundSize: `100% ${popupRowHeight}px`,
                        backgroundImage: `linear-gradient(to bottom, transparent ${popupRowHeight - 1}px, rgba(255,255,255,0.05) ${popupRowHeight}px)`
                    }}>
                        {renderFullMidiNotes()}
                    </div>
                </div>
            </div>
        </div>
        </div>
    );
}
