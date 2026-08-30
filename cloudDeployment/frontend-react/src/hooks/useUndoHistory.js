import React from 'react';
import { Midi } from '@tonejs/midi';

function restoreOriginalMidiSnapshot(snapshot) {
    if (!snapshot?.binarySnapshot) return null;
    const { binarySnapshot, ...metadata } = snapshot;
    return {
        ...metadata,
        // Midi parsing does not mutate its input, but make a fresh copy so an
        // editor operation can never share a mutable byte view with the
        // immutable revert source.
        midiData: new Midi(binarySnapshot.slice(0)),
    };
}

function originalMidiMetadata(snapshot) {
    if (!snapshot) return {};
    const { binarySnapshot: _binarySnapshot, ...metadata } = snapshot;
    return metadata;
}

/**
 * useUndoHistory Hook
 * 
 * Manages the snapshot-based undo and revert history for the MIDI editor. Because
 * `@tonejs/midi` instances are complex class objects, editing them directly is destructive.
 * This hook serializes the state to a binary ArrayBuffer snapshot before any edit occurs, 
 * pushing it to a stack (max 20 depth per track) to prevent memory leaks while allowing 
 * non-destructive experimentation.
 * 
 * @param {Object} parsedMidiStems - Dictionary of currently active MIDI stems
 * @param {Function} setParsedMidiStems - State setter to trigger React re-renders upon undo
 * @param {Object} originalMidiStems - Dictionary of compact immutable MIDI
 * binary snapshots used for full revert
 * @returns {Object} Object containing the undo stacks and controller functions
 */
export function useUndoHistory(parsedMidiStems, setParsedMidiStems, originalMidiStems) {
    const [undoStacks, setUndoStacks] = React.useState({});
    const MAX_UNDO_STEPS = 20;

    const pushUndoState = React.useCallback((trackName) => {
        if (!parsedMidiStems[trackName]) return;
        const binarySnapshot = parsedMidiStems[trackName].midiData.toArray();
        setUndoStacks(prev => {
            const trackStack = prev[trackName] ? [...prev[trackName]] : [];
            if (trackStack.length >= MAX_UNDO_STEPS) {
                trackStack.shift();
            }
            trackStack.push(binarySnapshot);
            return { ...prev, [trackName]: trackStack };
        });
    }, [parsedMidiStems]);

    const handleUndoMidi = React.useCallback((trackName) => {
        setUndoStacks(prev => {
            const trackStack = prev[trackName] ? [...prev[trackName]] : [];
            if (trackStack.length === 0) return prev; // nothing to undo
            
            const lastSnapshot = trackStack.pop();
            
            if (originalMidiStems[trackName]) {
                // An undo snapshot represents the already-edited MIDI state,
                // so preserve the stable metadata from the original snapshot
                // but restore the specific historical note data.
                const restoredData = {
                    ...originalMidiMetadata(originalMidiStems[trackName]),
                    midiData: new Midi(lastSnapshot),
                };
                
                setParsedMidiStems(prevStems => ({
                    ...prevStems,
                    [trackName]: restoredData
                }));
            }
            
            return { ...prev, [trackName]: trackStack };
        });
    }, [originalMidiStems, setParsedMidiStems]);

    const handleRevertMidi = React.useCallback((trackName) => {
        if (!originalMidiStems[trackName]) return;
        
        pushUndoState(trackName);
        
        // Reconstruct only on demand. Holding bytes instead of an additional
        // Tone.js graph keeps completed multi-stem jobs much smaller in memory.
        const restoredData = restoreOriginalMidiSnapshot(originalMidiStems[trackName]);
        if (!restoredData) return;
        
        setParsedMidiStems(prev => ({
            ...prev,
            [trackName]: restoredData
        }));
    }, [originalMidiStems, pushUndoState, setParsedMidiStems]);

    return { undoStacks, pushUndoState, handleUndoMidi, handleRevertMidi };
}
