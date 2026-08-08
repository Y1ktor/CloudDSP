/**
 * TrackGrid.jsx
 *
 * Renders the MIDI portion of the timeline. ADTOF drum MIDI is deliberately
 * drawn as five named drum lanes rather than as one compressed piano roll.
 */
import React from 'react';
import { getAdtofDrumNotes } from '../../utils/DrumMidi';

export default function TrackGrid({
    timelineRows,
    parsedMidiStems,
    midiStatusByTrack = {},
    pixelsPerBar,
    activeBpm,
    parsedBeatsPerBar,
    selectedTrack,
    setSelectedTrack,
    onDoubleClickTrack
}) {
    const getNotesForRow = (row) => {
        const stemData = parsedMidiStems[row.trackName];
        if (!stemData?.midiData?.tracks?.length) return [];

        if (row.kind === 'drum-lane') {
            return getAdtofDrumNotes(stemData.midiData, row.drumVoice.id);
        }
        if (stemData.isAdtofDrum) return [];
        return stemData.midiData.tracks.flatMap((track) => track.notes || []);
    };

    const renderMidiNotes = (row) => {
        const notes = getNotesForRow(row);
        if (notes.length === 0) return null;

        if (row.kind === 'drum-lane') {
            return notes.map((note, index) => {
                const leftPx = (note.time * (activeBpm / 60) / parsedBeatsPerBar) * pixelsPerBar;
                const widthPx = Math.max(4, (note.duration * (activeBpm / 60) / parsedBeatsPerBar) * pixelsPerBar);
                const velocity = note.velocity !== undefined ? Math.max(0.25, note.velocity) : 0.8;
                return (
                    <div
                        key={`${row.id}-note-${index}`}
                        style={{
                            position: 'absolute', left: `${leftPx}px`, width: `${widthPx}px`,
                            top: '19px', height: '22px', borderRadius: '3px',
                            backgroundColor: row.drumVoice.color, opacity: velocity,
                            boxShadow: `0 0 5px ${row.drumVoice.color}70`, pointerEvents: 'none'
                        }}
                    />
                );
            });
        }

        let minPitch = 127;
        let maxPitch = 0;
        notes.forEach((note) => {
            minPitch = Math.min(minPitch, note.midi);
            maxPitch = Math.max(maxPitch, note.midi);
        });
        const pitchRange = Math.max(12, maxPitch - minPitch);
        const minBoundedPitch = minPitch - 2;

        return notes.map((note, index) => {
            const leftPx = (note.time * (activeBpm / 60) / parsedBeatsPerBar) * pixelsPerBar;
            const widthPx = Math.max(2, (note.duration * (activeBpm / 60) / parsedBeatsPerBar) * pixelsPerBar);
            const topPercent = Math.max(0, Math.min(1, 1 - ((note.midi - minBoundedPitch) / pitchRange))) * 100;
            const velocity = note.velocity !== undefined ? Math.max(0.25, note.velocity) : 0.8;
            return (
                <div
                    key={`${row.id}-note-${index}`}
                    style={{
                        position: 'absolute', left: `${leftPx}px`, width: `${widthPx}px`,
                        top: `calc(${topPercent}% - 2px)`, height: '4px', backgroundColor: '#4CAF50',
                        borderRadius: '2px', opacity: velocity, boxShadow: '0 0 2px rgba(0,0,0,0.5)',
                        pointerEvents: 'none'
                    }}
                />
            );
        });
    };

    return (
        <>
            {timelineRows.map((row) => {
                const midiStatus = row.kind === 'drum-lane' ? null : midiStatusByTrack[row.trackName];
                const isMidiPending = midiStatus === 'processing' || midiStatus === 'loading';
                const isMidiFailed = midiStatus === 'failed';
                const statusLabel = midiStatus === 'loading'
                    ? 'Loading MIDI…'
                    : isMidiFailed ? 'MIDI extraction failed' : 'MIDI processing…';

                return (
                    <div
                        key={row.id}
                        onClick={() => setSelectedTrack(selectedTrack === row.id ? null : row.id)}
                        onDoubleClick={() => onDoubleClickTrack(row.trackName)}
                        style={{
                            height: '60px', position: 'relative', overflow: 'hidden', boxSizing: 'border-box',
                            borderBottom: '1px solid rgba(255,255,255,0.05)',
                            backgroundColor: selectedTrack === row.id
                                ? 'rgba(255, 255, 255, 0.08)'
                                : row.kind === 'drum-lane' ? 'rgba(255, 255, 255, 0.025)' : 'transparent',
                            cursor: 'pointer', userSelect: 'none', transition: 'background-color 0.2s'
                        }}
                    >
                        {row.kind === 'stem' && parsedMidiStems[row.trackName]?.isAdtofDrum && !isMidiPending && (
                            <div style={{
                                position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                                paddingLeft: '14px', color: '#9fa8da', fontSize: '12px', fontWeight: '600',
                                pointerEvents: 'none'
                            }}>
                                ADTOF drum kit — edit or play the named lanes below
                            </div>
                        )}
                        {renderMidiNotes(row)}
                        {(isMidiPending || isMidiFailed) && (
                            <div style={{
                                position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                                justifyContent: 'center', gap: '7px', color: isMidiFailed ? '#ff9a9a' : '#e7bd47',
                                fontSize: '12px', fontWeight: '600',
                                backgroundColor: isMidiFailed ? 'rgba(75, 25, 25, 0.45)' : 'rgba(15, 15, 15, 0.38)',
                                pointerEvents: 'none'
                            }}>
                                <span aria-hidden="true">{isMidiFailed ? '!' : '●'}</span>
                                {statusLabel}
                            </div>
                        )}
                    </div>
                );
            })}
        </>
    );
}
