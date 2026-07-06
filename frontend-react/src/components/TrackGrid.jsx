/**
 * TrackGrid.jsx
 * 
 * Renders the main canvas area for the tracks.
 * This is where the MIDI notes will be rendered.
 */
import React from 'react';

/**
 * TrackGrid
 * 
 * @param {Object} props - Component props
 * @param {Object} props.tracksToRender - Dictionary of track names to URLs
 * @param {Object} props.parsedMidiStems - Dictionary of parsed MIDI data per track
 * @param {number} props.pixelsPerBar - Horizontal zoom scale
 * @param {number} props.activeBpm - The current BPM
 * @param {number} props.parsedBeatsPerBar - The integer number of beats per measure
 */
export default function TrackGrid({
    tracksToRender,
    parsedMidiStems,
    pixelsPerBar,
    activeBpm,
    parsedBeatsPerBar,
    selectedTrack,
    setSelectedTrack,
    onDoubleClickTrack
}) {
    // Helper function to render the notes for a specific track
    const renderMidiNotes = (trackName) => {
        const stemData = parsedMidiStems[trackName];
        if (!stemData || !stemData.midiData || !stemData.midiData.tracks || stemData.midiData.tracks.length === 0) {
            return null;
        }

        // We assume the first track in the MIDI file contains the notes (typical for basic-pitch output)
        const notes = stemData.midiData.tracks[0].notes;
        if (notes.length === 0) return null;

        // Find min and max pitch to dynamically scale vertically within the 60px container
        let minPitch = 127;
        let maxPitch = 0;
        notes.forEach(note => {
            if (note.midi < minPitch) minPitch = note.midi;
            if (note.midi > maxPitch) maxPitch = note.midi;
        });

        // Add a small buffer so notes don't touch the absolute top/bottom edge
        const pitchRange = Math.max(12, maxPitch - minPitch); 
        const minBoundedPitch = minPitch - 2; 

        return notes.map((note, index) => {
            // Calculate absolute position based on active BPM and Time Signature
            const noteStartBeats = note.time * (activeBpm / 60);
            const noteStartBars = noteStartBeats / parsedBeatsPerBar;
            const leftPx = noteStartBars * pixelsPerBar;

            const noteDurationBeats = note.duration * (activeBpm / 60);
            const noteDurationBars = noteDurationBeats / parsedBeatsPerBar;
            const widthPx = Math.max(2, noteDurationBars * pixelsPerBar); // Minimum 2px width

            // Calculate vertical position (higher pitch = lower Y value = top of container)
            const normalizedPitch = (note.midi - minBoundedPitch) / pitchRange;
            // Bound it between 0 and 1, invert it because top: 0 is highest visually
            const topPercent = Math.max(0, Math.min(1, 1 - normalizedPitch)) * 100;
            
            // Note height defaults to 4px
            const noteHeight = 4;
            
            // Map velocity (0-1) to opacity. Set a minimum of 0.25 so very quiet notes don't disappear completely.
            const noteOpacity = Math.max(0.25, note.velocity || 0.8);

            return (
                <div 
                    key={`note-${index}`}
                    style={{
                        position: 'absolute',
                        left: `${leftPx}px`,
                        width: `${widthPx}px`,
                        top: `calc(${topPercent}% - ${noteHeight/2}px)`,
                        height: `${noteHeight}px`,
                        backgroundColor: '#4CAF50',
                        borderRadius: '2px',
                        opacity: noteOpacity,
                        boxShadow: '0 0 2px rgba(0,0,0,0.5)',
                        pointerEvents: 'none' // Don't block interactions
                    }}
                />
            );
        });
    };

    return (
        <>
            {Object.keys(tracksToRender).map((trackName) => (
                <div 
                    key={trackName} 
                    onClick={() => setSelectedTrack(selectedTrack === trackName ? null : trackName)}
                    onDoubleClick={() => onDoubleClickTrack(trackName)}
                    style={{ 
                        height: '60px', 
                        position: 'relative', overflow: 'hidden', boxSizing: 'border-box',
                        borderBottom: '1px solid rgba(255,255,255,0.05)',
                        backgroundColor: selectedTrack === trackName ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
                        cursor: 'pointer',
                        userSelect: 'none',
                        transition: 'background-color 0.2s'
                    }}
                >
                    {renderMidiNotes(trackName)}
                </div>
            ))}
        </>
    );
}
