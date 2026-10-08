/** Shared Studio tempo drag control and meter menu, including MIDI-only results. */
import React, { useEffect, useRef, useState } from 'react';
import './WorkspaceTempoControls.css';

const TIME_SIGNATURES = ['3/4', '4/4', '5/4', '6/8', '7/8'];

export default function WorkspaceTempoControls({
    audioEngine,
    hasDeterminedTempo = true,
    showSigMenu,
    setShowSigMenu,
}) {
    const [localMenuOpen, setLocalMenuOpen] = useState(false);
    const menuOpen = showSigMenu ?? localMenuOpen;
    const setMenuOpen = setShowSigMenu ?? setLocalMenuOpen;
    const triggerRef = useRef(null);
    const [whole, decimal] = audioEngine.bpm.toFixed(1).split('.');

    useEffect(() => {
        if (!menuOpen) return undefined;
        const closeOnEscape = (event) => {
            if (event.key !== 'Escape') return;
            event.stopPropagation();
            setMenuOpen(false);
            triggerRef.current?.focus();
        };
        window.addEventListener('keydown', closeOnEscape);
        return () => window.removeEventListener('keydown', closeOnEscape);
    }, [menuOpen, setMenuOpen]);

    const changeBpmWithKeyboard = (event) => {
        if (!hasDeterminedTempo || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        const step = (event.shiftKey ? 0.1 : 1) * (event.key === 'ArrowUp' ? 1 : -1);
        audioEngine.setBpm(Math.round(Math.max(30, Math.min(300, audioEngine.bpm + step)) * 10) / 10);
    };

    return (
        <div className="workspace-tempo-controls">
            <div className="workspace-bpm-control">
                <span className="bpm-label">BPM:</span>
                <div
                    className="workspace-bpm-value"
                    role="spinbutton"
                    aria-label="BPM"
                    aria-valuemin={30}
                    aria-valuemax={300}
                    aria-valuenow={hasDeterminedTempo ? audioEngine.bpm : undefined}
                    aria-disabled={!hasDeterminedTempo}
                    tabIndex={hasDeterminedTempo ? 0 : -1}
                    onKeyDown={changeBpmWithKeyboard}
                    title="Drag up or down to adjust BPM. Arrow keys change whole beats; Shift changes tenths."
                >
                    {hasDeterminedTempo ? <>
                        <span className="workspace-bpm-whole" onMouseDown={(event) => audioEngine.handleBpmMouseDown(event, 'int')}>{whole}</span>
                        <span>.</span>
                        <span className="workspace-bpm-decimal" onMouseDown={(event) => audioEngine.handleBpmMouseDown(event, 'dec')}>{decimal}</span>
                    </> : '---'}
                </div>
            </div>
            <div className="time-signature workspace-meter-control">
                <button
                    ref={triggerRef}
                    className="workspace-meter-trigger"
                    type="button"
                    aria-label="Time signature"
                    aria-haspopup="menu"
                    aria-expanded={menuOpen}
                    onClick={() => setMenuOpen(!menuOpen)}
                >{audioEngine.timeSignature}</button>
                {menuOpen && <>
                    <div className="workspace-meter-dismiss" onClick={() => setMenuOpen(false)} aria-hidden="true" />
                    <div className="workspace-meter-menu" role="menu" aria-label="Time signatures">
                        {TIME_SIGNATURES.map((signature) => (
                            <button
                                type="button"
                                className="workspace-meter-option"
                                role="menuitemradio"
                                aria-checked={signature === audioEngine.timeSignature}
                                key={signature}
                                onClick={() => {
                                    audioEngine.setTimeSignature(signature);
                                    setMenuOpen(false);
                                    triggerRef.current?.focus();
                                }}
                            >{signature}</button>
                        ))}
                    </div>
                </>}
            </div>
        </div>
    );
}
