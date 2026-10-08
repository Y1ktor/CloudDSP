/**
 * Renders workspace playback, cycle, MIDI mode, tempo, time signature, and
 * project download controls.
 */
import React from 'react';
import WorkspaceTempoControls from './WorkspaceTempoControls';

/** Master playback, MIDI, tempo, meter, and download controls. */
export default function WorkspaceTransportControls({
    audioEngine,
    isMidiLoading,
    dynamicProgress,
    dynamicDuration,
    toggleGlobalMidiMode,
    midiCapableTrackNames,
    isGlobalMidiEnabled,
    hasDeterminedTempo,
    showSigMenu,
    setShowSigMenu,
    openDownloadPopup,
    downloadArtifacts,
}) {
    return (
        <div style={{
            background: 'var(--studio-surface-raised)', padding: '15px 20px', borderRadius: '4px',
            display: 'flex', alignItems: 'center', gap: '20px'
        }}>
            <button title="Go to Beginning" onClick={audioEngine.handleGoToBeginning} style={{
                background: 'transparent', color: 'var(--studio-text)', border: 'none',
                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
            }}>
                <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
            </button>

            <button
                title="Play/Pause"
                onPointerDown={audioEngine.unlockAudio}
                onClick={audioEngine.togglePlay}
                style={{
                background: 'transparent', color: 'var(--studio-text)', border: 'none',
                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0', opacity: 0.8
            }}>
                {audioEngine.isPlaying ? (
                    <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                ) : (
                    <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                )}
            </button>

            <button title="Toggle Cycle" onClick={() => audioEngine.setIsCycling(!audioEngine.isCycling)} style={{
                background: audioEngine.isCycling ? '#a56a00' : 'transparent',
                color: audioEngine.isCycling ? 'white' : 'var(--studio-text)',
                border: 'none', borderRadius: '4px',
                cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '2px',
                opacity: 0.8,
                transition: 'background-color 0.2s'
            }}>
                <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
            </button>

            <div className="time-display" style={{ color: 'var(--studio-text)', fontSize: '14px', fontFamily: 'monospace', marginLeft: '10px', whiteSpace: 'nowrap' }}>
                {isMidiLoading ?
                    `${audioEngine.formatTime(audioEngine.progress)} / ${audioEngine.formatTime(audioEngine.duration)}` :
                    `${audioEngine.formatTime(dynamicProgress)} / ${audioEngine.formatTime(dynamicDuration)}`
                }
            </div>

            <button
                type="button"
                onClick={toggleGlobalMidiMode}
                disabled={midiCapableTrackNames.length === 0}
                aria-pressed={isGlobalMidiEnabled}
                title={midiCapableTrackNames.length === 0
                    ? 'MIDI playback becomes available as tracks finish processing'
                    : `${isGlobalMidiEnabled ? 'Disable' : 'Enable'} MIDI synthesis for all ready tracks`}
                style={{
                    height: '24px', padding: '0 8px', marginLeft: '12px',
                    background: 'transparent',
                    color: isGlobalMidiEnabled ? 'var(--studio-midi)' : 'var(--studio-text-muted)',
                    border: `1px solid ${isGlobalMidiEnabled ? 'var(--studio-midi)' : 'var(--studio-border-strong)'}`,
                    boxShadow: isGlobalMidiEnabled ? '0 0 8px rgba(37, 137, 92, 0.22)' : 'none',
                    borderRadius: '4px',
                    cursor: midiCapableTrackNames.length === 0 ? 'not-allowed' : 'pointer',
                    fontSize: '11px', fontWeight: 'bold',
                    opacity: midiCapableTrackNames.length === 0 ? 0.55 : 1,
                }}
            >MIDI</button>

            <WorkspaceTempoControls
                audioEngine={audioEngine}
                hasDeterminedTempo={hasDeterminedTempo}
                showSigMenu={showSigMenu}
                setShowSigMenu={setShowSigMenu}
            />

            <div style={{ flexGrow: 1 }} /> {/* Pushes download button to the right */}

            <button
                type="button"
                onClick={openDownloadPopup}
                disabled={downloadArtifacts.length === 0}
                title={downloadArtifacts.length === 0 ? 'No project files are available to download yet' : 'Choose project files to download'}
                style={{
                    background: downloadArtifacts.length === 0 ? 'var(--studio-surface-sunken)' : 'var(--studio-surface)', color: downloadArtifacts.length === 0 ? 'var(--studio-text-muted)' : 'var(--studio-text-secondary)', border: '1px solid var(--studio-border-strong)',
                    padding: '6px 14px', borderRadius: '4px', fontSize: '12px',
                    fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '6px',
                    boxShadow: '0 2px 5px rgba(44, 62, 80, 0.08)', opacity: downloadArtifacts.length === 0 ? 0.65 : 1,
                    cursor: downloadArtifacts.length === 0 ? 'not-allowed' : 'pointer',
                }}
            >
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                    <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
                </svg>
                <span className="download-text">Download</span>
            </button>
            <style>{`
                @media (max-width: 850px) {
                    .time-display { display: none !important; }
                    .download-text { display: none !important; }
                }
                @media (max-width: 750px){
                    .time-signature { display: none !important; }
                }
                @media (max-width: 600px) {
                    .bpm-label { display: none !important; }

                }
            `}</style>
        </div>
    );
}
