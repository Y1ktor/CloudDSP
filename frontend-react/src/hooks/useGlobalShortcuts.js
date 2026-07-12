import React from 'react';

/**
 * useGlobalShortcuts Hook
 * 
 * Binds global keyboard shortcuts to the window object for transport controls and editing.
 * Ensures that shortcuts like Spacebar (Play/Pause) work regardless of which sub-component
 * is focused, while intelligently ignoring keystrokes when the user is typing in inputs or textareas.
 * 
 * Shortcuts:
 * - Space: Play/Pause
 * - Enter: Return to beginning
 * - C: Toggle cycle loop
 * - S: Toggle solo (targets editor track if open, otherwise selected track)
 * - M: Toggle mute (targets editor track if open, otherwise selected track)
 * - Cmd/Ctrl + Z: Undo last MIDI edit (only active when editor is open)
 * 
 * @param {Object} props - Hook dependencies
 * @param {Function} props.togglePlay - Toggles global playback
 * @param {Function} props.handleGoToBeginning - Seeks playback to 0
 * @param {Function} props.setIsCycling - Toggles loop mode
 * @param {Function} props.toggleSolo - Solos a specific track
 * @param {Function} props.toggleMute - Mutes a specific track
 * @param {string|null} props.editorOpenTrack - The name of the currently open track in the MIDI editor
 * @param {string|null} props.selectedTrack - The name of the currently selected track in the track list
 * @param {Function} props.handleUndoMidi - Executes a MIDI undo action
 */
export function useGlobalShortcuts({
    togglePlay,
    handleGoToBeginning,
    setIsCycling,
    toggleSolo,
    toggleMute,
    editorOpenTrack,
    selectedTrack,
    handleUndoMidi
}) {
    React.useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
                return;
            }

            const targetTrack = editorOpenTrack || selectedTrack;

            switch (e.key.toLowerCase()) {
                case ' ':
                    e.preventDefault();
                    togglePlay();
                    break;
                case 'enter':
                    e.preventDefault();
                    handleGoToBeginning();
                    break;
                case 'c':
                    e.preventDefault();
                    setIsCycling(prev => !prev);
                    break;
                case 's':
                    if (targetTrack) {
                        e.preventDefault();
                        toggleSolo(targetTrack);
                    }
                    break;
                case 'm':
                    if (targetTrack) {
                        e.preventDefault();
                        toggleMute(targetTrack);
                    }
                    break;
                case 'z':
                    if ((e.metaKey || e.ctrlKey) && editorOpenTrack) {
                        e.preventDefault();
                        handleUndoMidi(editorOpenTrack);
                    }
                    break;
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [togglePlay, handleGoToBeginning, setIsCycling, toggleSolo, toggleMute, editorOpenTrack, selectedTrack, handleUndoMidi]);
}
