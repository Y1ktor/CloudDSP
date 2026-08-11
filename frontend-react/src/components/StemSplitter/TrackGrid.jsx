/**
 * TrackGrid.jsx
 *
 * Renders the MIDI portion of the timeline. ADTOF drums render as a normal
 * compact MIDI row while collapsed, then move into five named lanes on expand.
 *
 * Playback progress intentionally is not an input here. The grid is static
 * between MIDI, zoom, tempo, selection, or viewport-window changes; keeping
 * it independent from the transport frame loop prevents React from rebuilding
 * every MIDI note on every animation frame.
 */
import React from 'react';
import { getAdtofDrumNotes } from '../../utils/DrumMidi';

const DEFAULT_OVERSCAN_PX = 360;

function normaliseVisibleRange(visibleRange) {
    const startPx = Number(visibleRange?.startPx);
    const endPx = Number(visibleRange?.endPx);
    const overscanPx = Number(visibleRange?.overscanPx);

    // Omitting the optional range remains backwards compatible: render the
    // entire grid. A caller should quantise this window while auto-scrolling
    // (for example to 500–1000px tiles), rather than changing it per frame.
    if (!Number.isFinite(startPx) || !Number.isFinite(endPx) || endPx <= startPx) {
        return { startPx: null, endPx: null, overscanPx: DEFAULT_OVERSCAN_PX };
    }

    return {
        startPx: Math.max(0, startPx),
        endPx,
        overscanPx: Number.isFinite(overscanPx) && overscanPx >= 0
            ? overscanPx
            : DEFAULT_OVERSCAN_PX,
    };
}

function isVisibleNote(leftPx, widthPx, visibleStartPx, visibleEndPx, overscanPx) {
    if (visibleStartPx === null || visibleEndPx === null) return true;
    const start = Math.max(0, visibleStartPx - overscanPx);
    const end = visibleEndPx + overscanPx;
    return leftPx + widthPx >= start && leftPx <= end;
}

function getNotesForRow(row, stemData) {
    if (!stemData?.midiData?.tracks?.length) return [];

    if (row.kind === 'drum-lane') {
        return getAdtofDrumNotes(stemData.midiData, row.drumVoice.id)
            .sort((first, second) => (Number(first.time) || 0) - (Number(second.time) || 0));
    }

    // A collapsed Drums stack behaves like any other compact MIDI row. Once
    // expanded, the child lanes take over and the parent stays empty so no
    // drum notes are drawn twice.
    if (stemData.isAdtofDrum && row.isDrumExpanded) return [];
    return stemData.midiData.tracks
        .flatMap((track) => track.notes || [])
        .sort((first, second) => (Number(first.time) || 0) - (Number(second.time) || 0));
}

function midiPosition(note, activeBpm, parsedBeatsPerBar, pixelsPerBar) {
    const pixelsPerSecond = (activeBpm / 60 / parsedBeatsPerBar) * pixelsPerBar;
    return {
        leftPx: (Number(note.time) || 0) * pixelsPerSecond,
        widthPx: (Number(note.duration) || 0) * pixelsPerSecond,
    };
}

function lowerBoundByTime(notes, time) {
    let low = 0;
    let high = notes.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if ((Number(notes[middle].time) || 0) < time) low = middle + 1;
        else high = middle;
    }
    return low;
}

function upperBoundByTime(notes, time) {
    let low = 0;
    let high = notes.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if ((Number(notes[middle].time) || 0) <= time) low = middle + 1;
        else high = middle;
    }
    return low;
}

const MidiNotes = React.memo(function MidiNotes({
    row,
    notes,
    pixelsPerBar,
    activeBpm,
    parsedBeatsPerBar,
    visibleStartPx,
    visibleEndPx,
    overscanPx,
}) {
    const noteMetrics = React.useMemo(() => {
        let minPitch = 127;
        let maxPitch = 0;
        let maxDuration = 0;
        notes.forEach((note) => {
            minPitch = Math.min(minPitch, note.midi);
            maxPitch = Math.max(maxPitch, note.midi);
            maxDuration = Math.max(maxDuration, Number(note.duration) || 0);
        });
        return { minPitch, maxPitch, maxDuration };
    }, [notes]);

    const candidateNotes = React.useMemo(() => {
        if (notes.length === 0 || visibleStartPx === null || visibleEndPx === null) {
            return notes.map((note, index) => ({ note, index }));
        }

        const pixelsPerSecond = (activeBpm / 60 / parsedBeatsPerBar) * pixelsPerBar;
        if (!Number.isFinite(pixelsPerSecond) || pixelsPerSecond <= 0) {
            return notes.map((note, index) => ({ note, index }));
        }

        // Notes are sorted once when MIDI changes. The maximum duration lets
        // the lower bound retain a long note that began before the viewport.
        const startTime = Math.max(
            0,
            ((visibleStartPx - overscanPx) / pixelsPerSecond) - noteMetrics.maxDuration,
        );
        const endTime = (visibleEndPx + overscanPx) / pixelsPerSecond;
        const firstIndex = lowerBoundByTime(notes, startTime);
        const lastIndex = upperBoundByTime(notes, endTime);
        const candidates = [];
        for (let index = firstIndex; index < lastIndex; index += 1) {
            candidates.push({ note: notes[index], index });
        }
        return candidates;
    }, [
        activeBpm,
        noteMetrics.maxDuration,
        notes,
        overscanPx,
        parsedBeatsPerBar,
        pixelsPerBar,
        visibleEndPx,
        visibleStartPx,
    ]);

    const noteElements = React.useMemo(() => {
        if (notes.length === 0) return null;

        if (row.kind === 'drum-lane') {
            return candidateNotes.flatMap(({ note, index }) => {
                const { leftPx, widthPx: rawWidthPx } = midiPosition(
                    note,
                    activeBpm,
                    parsedBeatsPerBar,
                    pixelsPerBar,
                );
                const widthPx = Math.max(4, rawWidthPx);
                if (!isVisibleNote(leftPx, widthPx, visibleStartPx, visibleEndPx, overscanPx)) return [];

                const velocity = note.velocity !== undefined ? Math.max(0.25, note.velocity) : 0.8;
                return (
                    <div
                        key={`${row.id}-note-${index}`}
                        aria-hidden="true"
                        className="midi-grid-note midi-grid-drum-note"
                        style={{
                            left: `${leftPx}px`,
                            width: `${widthPx}px`,
                            '--midi-note-color': row.drumVoice.color,
                            '--midi-note-opacity': velocity,
                        }}
                    />
                );
            });
        }

        const pitchRange = Math.max(12, noteMetrics.maxPitch - noteMetrics.minPitch);
        const minBoundedPitch = noteMetrics.minPitch - 2;

        return candidateNotes.flatMap(({ note, index }) => {
            const { leftPx, widthPx: rawWidthPx } = midiPosition(
                note,
                activeBpm,
                parsedBeatsPerBar,
                pixelsPerBar,
            );
            const widthPx = Math.max(2, rawWidthPx);
            if (!isVisibleNote(leftPx, widthPx, visibleStartPx, visibleEndPx, overscanPx)) return [];

            const topPercent = Math.max(0, Math.min(1, 1 - ((note.midi - minBoundedPitch) / pitchRange))) * 100;
            const velocity = note.velocity !== undefined ? Math.max(0.25, note.velocity) : 0.8;
            return (
                <div
                    key={`${row.id}-note-${index}`}
                    aria-hidden="true"
                    className="midi-grid-note midi-grid-pitched-note"
                    style={{
                        left: `${leftPx}px`,
                        width: `${widthPx}px`,
                        top: `calc(${topPercent}% - 2px)`,
                        '--midi-note-opacity': velocity,
                    }}
                />
            );
        });
    }, [
        activeBpm,
        candidateNotes,
        noteMetrics.maxPitch,
        noteMetrics.minPitch,
        notes,
        overscanPx,
        parsedBeatsPerBar,
        pixelsPerBar,
        row,
        visibleEndPx,
        visibleStartPx,
    ]);

    return noteElements;
});

const TrackRow = React.memo(function TrackRow({
    row,
    stemData,
    midiStatus,
    pixelsPerBar,
    activeBpm,
    parsedBeatsPerBar,
    visibleStartPx,
    visibleEndPx,
    overscanPx,
    selectedTrack,
    setSelectedTrack,
    onDoubleClickTrack,
}) {
    const notes = React.useMemo(() => getNotesForRow(row, stemData), [row, stemData]);
    const isMidiPending = midiStatus === 'processing' || midiStatus === 'loading';
    const isMidiFailed = midiStatus === 'failed';
    const statusLabel = midiStatus === 'loading'
        ? 'Loading MIDI…'
        : isMidiFailed ? 'MIDI extraction failed' : 'MIDI processing…';

    return (
        <div
            onClick={() => setSelectedTrack(selectedTrack === row.id ? null : row.id)}
            onDoubleClick={() => onDoubleClickTrack(row.trackName)}
            style={{
                height: '78px',
                position: 'relative',
                overflow: 'hidden',
                boxSizing: 'border-box',
                contain: 'paint',
                borderBottom: '1px solid rgba(255,255,255,0.05)',
                backgroundColor: selectedTrack === row.id
                    ? 'rgba(255, 255, 255, 0.08)'
                    : row.kind === 'drum-lane' ? 'rgba(255, 255, 255, 0.025)' : 'transparent',
                cursor: 'pointer',
                userSelect: 'none',
                transition: 'background-color 0.2s',
            }}
        >
            <MidiNotes
                row={row}
                notes={notes}
                pixelsPerBar={pixelsPerBar}
                activeBpm={activeBpm}
                parsedBeatsPerBar={parsedBeatsPerBar}
                visibleStartPx={visibleStartPx}
                visibleEndPx={visibleEndPx}
                overscanPx={overscanPx}
            />
            {(isMidiPending || isMidiFailed) && (
                <div style={{
                    position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                    justifyContent: 'center', gap: '7px', color: isMidiFailed ? '#ff9a9a' : '#e7bd47',
                    fontSize: '12px', fontWeight: '600',
                    backgroundColor: isMidiFailed ? 'rgba(75, 25, 25, 0.45)' : 'rgba(15, 15, 15, 0.38)',
                    pointerEvents: 'none',
                }}>
                    <span aria-hidden="true">{isMidiFailed ? '!' : '●'}</span>
                    {statusLabel}
                </div>
            )}
        </div>
    );
});

const MemoizedTrackGrid = React.memo(function MemoizedTrackGrid({
    timelineRows,
    parsedMidiStems,
    midiStatusByTrack = {},
    pixelsPerBar,
    activeBpm,
    parsedBeatsPerBar,
    visibleStartPx,
    visibleEndPx,
    overscanPx,
    selectedTrack,
    setSelectedTrack,
    onDoubleClickTrack,
}) {
    return (
        <>
            <style>{`
                .midi-grid-note {
                    position: absolute;
                    pointer-events: none;
                    opacity: var(--midi-note-opacity, 0.8);
                }
                .midi-grid-pitched-note {
                    height: 4px;
                    border-radius: 2px;
                    background: #4CAF50;
                }
                .midi-grid-drum-note {
                    top: 19px;
                    height: 22px;
                    border-radius: 3px;
                    background: var(--midi-note-color, #9fa8da);
                }
            `}</style>
            {timelineRows.map((row) => {
                const midiStatus = row.kind === 'drum-lane' ? null : midiStatusByTrack[row.trackName];
                return (
                    <TrackRow
                        key={row.id}
                        row={row}
                        stemData={parsedMidiStems[row.trackName]}
                        midiStatus={midiStatus}
                        pixelsPerBar={pixelsPerBar}
                        activeBpm={activeBpm}
                        parsedBeatsPerBar={parsedBeatsPerBar}
                        visibleStartPx={visibleStartPx}
                        visibleEndPx={visibleEndPx}
                        overscanPx={overscanPx}
                        selectedTrack={selectedTrack}
                        setSelectedTrack={setSelectedTrack}
                        onDoubleClickTrack={onDoubleClickTrack}
                    />
                );
            })}
        </>
    );
});

/**
 * @param {{ startPx: number, endPx: number, overscanPx?: number }=} visibleRange
 * Optional absolute-pixel window used to avoid mounting off-screen notes.
 * Keep the supplied values stable or tile-quantised while playback scrolls.
 */
export default function TrackGrid({ onDoubleClickTrack, visibleRange, ...props }) {
    // StemSplitter currently creates its double-click handler inline. Keep its
    // latest behavior without allowing that transient function identity to
    // invalidate the memoised static MIDI grid on every transport render.
    const onDoubleClickTrackRef = React.useRef(onDoubleClickTrack);
    onDoubleClickTrackRef.current = onDoubleClickTrack;
    const stableOnDoubleClickTrack = React.useCallback((trackName) => {
        onDoubleClickTrackRef.current?.(trackName);
    }, []);
    const { startPx, endPx, overscanPx } = normaliseVisibleRange(visibleRange);

    return (
        <MemoizedTrackGrid
            {...props}
            visibleStartPx={startPx}
            visibleEndPx={endPx}
            overscanPx={overscanPx}
            onDoubleClickTrack={stableOnDoubleClickTrack}
        />
    );
}
