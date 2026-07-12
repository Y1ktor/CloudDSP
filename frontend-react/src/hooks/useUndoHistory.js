import React from 'react';

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
 * @param {Object} originalMidiStems - Dictionary of immutable original MIDI parses used for full revert
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
                const restoredData = {
                    ...originalMidiStems[trackName],
                    midiData: new (originalMidiStems[trackName].midiData.constructor)(lastSnapshot)
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
        
        // Re-clone the original by serializing it back to binary and re-parsing
        const binaryBuffer = originalMidiStems[trackName].midiData.toArray();
        const restoredData = {
            ...originalMidiStems[trackName],
            midiData: new (originalMidiStems[trackName].midiData.constructor)(binaryBuffer) 
        };
        
        setParsedMidiStems(prev => ({
            ...prev,
            [trackName]: restoredData
        }));
    }, [originalMidiStems, pushUndoState, setParsedMidiStems]);

    return { undoStacks, pushUndoState, handleUndoMidi, handleRevertMidi };
}
