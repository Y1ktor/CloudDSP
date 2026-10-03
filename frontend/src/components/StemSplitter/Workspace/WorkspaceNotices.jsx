import React from 'react';

/** Read-only demo guidance shown above the upload controls. */
export function WorkspaceDemoNotice({ isDemo, onOpenExamples }) {
    return isDemo && (
        <div style={{
            display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap',
            padding: '10px 12px', border: '1px solid #adddc1', borderRadius: '5px',
            background: 'var(--studio-midi-soft)', color: '#235c40', fontSize: '12px', lineHeight: 1.45,
        }}>
            <strong style={{ color: '#176b45' }}>Demo mode</strong>
            <span style={{ flex: 1, minWidth: '240px' }}>
                Explore playback and edit MIDI locally. Public demo assets are read-only; sign in to process and save your own audio.
            </span>
            {onOpenExamples && (
                <button
                    type="button"
                    onClick={onOpenExamples}
                    style={{
                        padding: '6px 9px', border: '1px solid #8fc9a8', borderRadius: '4px',
                        background: 'var(--studio-surface)', color: '#176b45', cursor: 'pointer',
                        fontSize: '11px', fontWeight: '700',
                    }}
                >Choose example</button>
            )}
        </div>
    );
}

/** Ongoing submission or saved-job restore progress. */
export function WorkspaceActivityNotice({
    showActivityNotice,
    activityMessage,
    isSplitting,
    hasDeterminedTempo,
}) {
    return showActivityNotice && (
        <div style={{
            display: 'flex', alignItems: 'center', gap: '9px',
            background: 'var(--studio-midi-soft)', color: '#176b45',
            border: '1px solid #adddc1', borderRadius: '4px',
            padding: '9px 12px', fontSize: '13px', fontWeight: '600'
        }}>
            <span aria-hidden="true" style={{
                width: '10px', height: '10px', border: '2px solid rgba(37, 137, 92, 0.24)',
                borderTopColor: 'var(--studio-midi)', borderRadius: '50%', animation: 'spin 1s linear infinite'
            }} />
            {activityMessage}
            {isSplitting && !hasDeterminedTempo && <span style={{ color: '#3f7d5c', fontWeight: '500' }}>BPM pending</span>}
        </div>
    );
}

/** Independent readiness notices for audio and MIDI. */
export function WorkspaceReadinessNotices({
    isAudioReady,
    backendMidiProcessingCount,
    isSplitting,
    midiDownloadCount,
    isHistoryJob,
}) {
    return (
        <>
            {!isAudioReady && (
                <div style={{
                    display: 'flex', alignItems: 'center', gap: '9px',
                    background: 'var(--studio-accent-soft)', color: '#245b86',
                    border: '1px solid #b8d6ee', borderRadius: '4px',
                    padding: '9px 12px', fontSize: '13px', fontWeight: '600'
                }}>
                    <span aria-hidden="true" style={{
                        width: '10px', height: '10px', border: '2px solid rgba(47, 127, 184, 0.24)',
                        borderTopColor: 'var(--studio-accent)', borderRadius: '50%', animation: 'spin 1s linear infinite'
                    }} />
                    Preparing synchronized audio buffers. Playback will be available when every displayed track is ready.
                </div>
            )}
            {backendMidiProcessingCount > 0 && isSplitting && (
                <div style={{
                    display: 'flex', alignItems: 'center', gap: '9px',
                    background: 'var(--studio-warning-soft)', color: '#8b5a00',
                    border: '1px solid #eed49c', borderRadius: '4px',
                    padding: '9px 12px', fontSize: '13px', fontWeight: '600'
                }}>
                    <span aria-hidden="true" style={{ color: 'var(--studio-warning)', fontSize: '16px' }}>●</span>
                    MIDI extraction is still processing for {backendMidiProcessingCount} stem{backendMidiProcessingCount === 1 ? '' : 's'}. Tracks will populate as each result arrives.
                </div>
            )}
            {midiDownloadCount > 0 && (
                <div style={{
                    display: 'flex', alignItems: 'center', gap: '9px',
                    background: 'var(--studio-accent-soft)', color: '#245b86',
                    border: '1px solid #b8d6ee', borderRadius: '4px',
                    padding: '9px 12px', fontSize: '13px', fontWeight: '600'
                }}>
                    <span aria-hidden="true" style={{ color: 'var(--studio-accent)', fontSize: '16px' }}>●</span>
                    {isHistoryJob
                        ? `Saved-job MIDI is downloading for ${midiDownloadCount} stem${midiDownloadCount === 1 ? '' : 's'}. Stems and MIDI will arrive shortly.`
                        : `Generated MIDI is downloading for ${midiDownloadCount} stem${midiDownloadCount === 1 ? '' : 's'}. Tracks will populate as each file arrives.`}
                </div>
            )}
        </>
    );
}
