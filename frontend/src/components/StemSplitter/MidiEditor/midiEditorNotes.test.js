import assert from 'node:assert/strict';
import test from 'node:test';
import { getVisibleMidiEditorNotes, indexMidiEditorNotes } from './midiEditorNotes.js';

const timeline = {
    activeBpm: 120,
    parsedBeatsPerBar: 4,
    popupPixelsPerBar: 200,
    visibleRange: { startPx: 1000, endPx: 1100, overscanPx: 0 },
};

test('time indexing preserves unsorted source identities and equal-time order', () => {
    const notes = Object.freeze([
        Object.freeze({ time: 11, duration: 1, midi: 60 }),
        Object.freeze({ time: 2, duration: 0.5, midi: 64 }),
        Object.freeze({ time: 2, duration: 0.25, midi: 67 }),
        Object.freeze({ time: -1, duration: 1000 }),
        Object.freeze({ time: Number.NaN, duration: 1000 }),
    ]);
    const indexed = indexMidiEditorNotes(notes);

    assert.deepEqual(indexed.notes.map(({ index }) => index), [1, 2, 0]);
    assert.strictEqual(indexed.notes[0].note, notes[1]);
    assert.strictEqual(indexed.notes[1].note, notes[2]);
    assert.strictEqual(indexed.notes[2].note, notes[0]);
    assert.equal(indexed.maxDuration, 1);
    assert.deepEqual(notes.map(({ time }) => time), [11, 2, 2, -1, Number.NaN]);
});

test('viewport search retains sustained notes and both inclusive time boundaries', () => {
    const indexed = indexMidiEditorNotes([
        { time: 1, duration: 9 },
        { time: 0.99, duration: 0.1 },
        { time: 10, duration: 0.1 },
        { time: 11, duration: 0.1 },
        { time: 11.01, duration: 0.1 },
        { time: 200, duration: 0.1 },
    ]);

    assert.deepEqual(
        getVisibleMidiEditorNotes(indexed, timeline).map(({ index }) => index),
        [0, 2, 3],
    );
    assert.deepEqual(
        getVisibleMidiEditorNotes(indexed, {
            ...timeline,
            visibleRange: { ...timeline.visibleRange, overscanPx: 50 },
        }).map(({ index }) => index),
        [1, 0, 2, 3, 4],
    );
});

test('missing viewport or unusable timing keeps notes available before measurement', () => {
    const indexed = indexMidiEditorNotes([{ time: 2, duration: 1 }]);
    for (const changed of [
        { activeBpm: 0 },
        { parsedBeatsPerBar: 0 },
        { popupPixelsPerBar: Number.NaN },
        { visibleRange: undefined },
        { visibleRange: { startPx: 10, endPx: 10 } },
    ]) {
        assert.strictEqual(getVisibleMidiEditorNotes(indexed, { ...timeline, ...changed }), indexed.notes);
    }
    assert.deepEqual(getVisibleMidiEditorNotes(indexMidiEditorNotes([]), timeline), []);
});

test('rebuilding the index after a MIDI edit updates times without changing note identity', () => {
    const notes = [{ time: 12, duration: 0.1 }, { time: 10, duration: 0.1 }];
    const original = indexMidiEditorNotes(notes);
    assert.deepEqual(getVisibleMidiEditorNotes(original, timeline).map(({ index }) => index), [1]);

    notes[0].time = 10.5;
    const revised = indexMidiEditorNotes(notes);
    const visible = getVisibleMidiEditorNotes(revised, timeline);
    assert.deepEqual(visible.map(({ index }) => index), [1, 0]);
    assert.strictEqual(visible[1].note, notes[0]);
    assert.equal(original.notes.find(({ index }) => index === 0).time, 12);
});
