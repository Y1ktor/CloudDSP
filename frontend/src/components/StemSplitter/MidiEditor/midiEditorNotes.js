function lowerBoundByTime(notes, time) {
    let low = 0;
    let high = notes.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (notes[middle].time < time) low = middle + 1;
        else high = middle;
    }
    return low;
}

function upperBoundByTime(notes, time) {
    let low = 0;
    let high = notes.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (notes[middle].time <= time) low = middle + 1;
        else high = middle;
    }
    return low;
}

/**
 * Keep source indices intact: editing, undo, and export address the original
 * MIDI array, while the renderer searches this separate time-sorted index.
 */
export function indexMidiEditorNotes(notes) {
    let maxDuration = 0;
    const indexed = [];

    notes.forEach((note, index) => {
        const time = Number(note?.time);
        if (!Number.isFinite(time) || time < 0) return;
        const duration = Number(note?.duration);
        if (Number.isFinite(duration) && duration > maxDuration) {
            maxDuration = duration;
        }
        indexed.push({ note, index, time });
    });

    indexed.sort((left, right) => left.time - right.time || left.index - right.index);
    return { notes: indexed, maxDuration };
}

/** Select viewport candidates without scanning every earlier note. */
export function getVisibleMidiEditorNotes(indexedNotes, {
    activeBpm,
    parsedBeatsPerBar,
    popupPixelsPerBar,
    visibleRange,
}) {
    const { notes, maxDuration } = indexedNotes;
    if (notes.length === 0) return [];

    const pixelsPerSecond = (Number(activeBpm) / 60 / Number(parsedBeatsPerBar))
        * Number(popupPixelsPerBar);
    if (!Number.isFinite(pixelsPerSecond) || pixelsPerSecond <= 0) return notes;

    const startPx = Math.max(0, Number(visibleRange?.startPx) || 0);
    const endPx = Number(visibleRange?.endPx);
    const overscanPx = Math.max(0, Number(visibleRange?.overscanPx) || 0);
    if (!Number.isFinite(endPx) || endPx <= startPx) return notes;

    // A note may start before the viewport and sustain into it. The maximum
    // duration expands one binary-search range to retain those earlier starts.
    const startTime = Math.max(0, ((startPx - overscanPx) / pixelsPerSecond) - maxDuration);
    const endTime = (endPx + overscanPx) / pixelsPerSecond;
    return notes.slice(
        lowerBoundByTime(notes, startTime),
        upperBoundByTime(notes, endTime),
    );
}
