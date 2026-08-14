import React from 'react';

function formatUpdatedAt(value) {
    if (!value) return 'Saved job';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Saved job';
    return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function statusLabel(status) {
    return String(status || 'unknown').replaceAll('_', ' ');
}

/**
 * Modal job library for durable jobs owned by the authenticated browser user.
 * Selecting an item only fetches its snapshot; it never starts a new DSP job.
 */
export default function PreviousJobs({
    isOpen,
    onClose,
    jobs = [],
    activeJobId,
    isLoading,
    error,
    onSelect,
    onRefresh,
    onDelete,
    deletingJobId,
}) {
    const [pendingDeletion, setPendingDeletion] = React.useState(null);

    React.useEffect(() => {
        if (!isOpen) return undefined;
        const handleKeyDown = (event) => {
            if (event.key === 'Escape') onClose?.();
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isOpen, onClose]);

    React.useEffect(() => {
        if (!isOpen) setPendingDeletion(null);
    }, [isOpen]);

    if (!isOpen) return null;

    return (
        <div
            role="presentation"
            onMouseDown={(event) => {
                if (event.target === event.currentTarget) onClose?.();
            }}
            style={{
                position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
                padding: '20px', background: 'rgba(0, 0, 0, 0.68)', boxSizing: 'border-box',
            }}
        >
            <section
                role="dialog"
                aria-modal="true"
                aria-label="Previous jobs"
                style={{
                    width: 'min(680px, 100%)', maxHeight: 'min(620px, calc(100vh - 40px))', overflow: 'hidden',
                    background: '#292929', border: '1px solid #555f6d', borderRadius: '7px', padding: '16px',
                    boxShadow: '0 18px 50px rgba(0, 0, 0, 0.6)', display: 'flex', flexDirection: 'column',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                    <div style={{ color: '#eee', fontSize: '16px', fontWeight: '700' }}>Previous jobs</div>
                    <div style={{ color: '#9d9d9d', fontSize: '12px', flex: 1 }}>
                        {isLoading ? 'Loading saved tracks…' : 'Select a track to reopen its source, stems, MIDI, and BPM.'}
                    </div>
                    <button
                        type="button"
                        onClick={onRefresh}
                        disabled={isLoading}
                        aria-label="Refresh previous jobs"
                        title="Refresh previous jobs"
                        style={{
                            width: '28px', height: '28px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                            background: '#3d3d3d', color: '#ddd', border: '1px solid #5a5a5a', borderRadius: '4px',
                            padding: 0, cursor: isLoading ? 'wait' : 'pointer', opacity: isLoading ? 0.65 : 1,
                        }}
                    >
                        <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                            <path d="M17.65 6.35A7.95 7.95 0 0 0 12 4V1L7 6l5 5V7a5 5 0 1 1-4.9 6h-2.02A7 7 0 1 0 17.65 6.35Z" />
                        </svg>
                    </button>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label="Close previous jobs"
                        title="Close"
                        style={{
                            width: '28px', height: '28px', padding: 0, border: '1px solid #5a5a5a', borderRadius: '4px',
                            background: '#3d3d3d', color: '#ddd', cursor: 'pointer', fontSize: '19px', lineHeight: 1,
                        }}
                    >×</button>
                </div>

                {error && <div role="alert" style={{ color: '#f08b8b', fontSize: '12px' }}>{error}</div>}
                {!error && !isLoading && jobs.length === 0 && (
                    <div style={{ color: '#999', fontSize: '12px' }}>No saved processing jobs for this account yet.</div>
                )}
                {jobs.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '7px', overflowY: 'auto', paddingRight: '2px' }}>
                        {jobs.map((job) => {
                            const isActive = job.job_id === activeJobId;
                            const isTerminal = ['completed', 'failed'].includes(job.status);
                            const isDeleting = deletingJobId === job.job_id;
                            const isDeletePending = pendingDeletion?.job_id === job.job_id;
                            return (
                                <div
                                    key={job.job_id}
                                    style={{
                                        background: isActive ? '#264d38' : '#353535', color: '#e8e8e8',
                                        border: `1px solid ${isActive ? '#61b680' : '#555'}`, borderRadius: '4px', overflow: 'hidden',
                                    }}
                                >
                                    <div style={{ display: 'flex', alignItems: 'stretch' }}>
                                        <button
                                            type="button"
                                            onClick={() => onSelect(job)}
                                            aria-pressed={isActive}
                                            title={`Open ${job.source_filename || 'saved track'}`}
                                            style={{
                                                minWidth: 0, flex: 1, textAlign: 'left', padding: '10px 12px', background: 'transparent',
                                                color: '#e8e8e8', border: 0, cursor: 'pointer',
                                            }}
                                        >
                                            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '13px', fontWeight: '700' }}>
                                                {job.source_filename || 'Untitled audio'}
                                            </div>
                                            <div style={{ color: isActive ? '#bfe6ca' : '#aaa', fontSize: '11px', marginTop: '3px' }}>
                                                {statusLabel(job.status)} · {formatUpdatedAt(job.updated_at)}
                                            </div>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setPendingDeletion(job)}
                                            disabled={!isTerminal || isDeleting}
                                            aria-label={`Delete ${job.source_filename || 'saved track'}`}
                                            title={isTerminal ? 'Delete this job and all of its files' : 'Only completed or failed jobs can be deleted'}
                                            style={{
                                                width: '42px', flexShrink: 0, border: 0, borderLeft: '1px solid #555',
                                                background: 'transparent', color: isTerminal ? '#e69292' : '#777',
                                                cursor: isTerminal && !isDeleting ? 'pointer' : 'not-allowed', opacity: isDeleting ? 0.6 : 1,
                                            }}
                                        >
                                            {isDeleting ? '…' : (
                                                <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="currentColor">
                                                    <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-3 6h12l-1 12H7L6 9Zm4 3v6h2v-6h-2Zm4 0v6h2v-6h-2Z" />
                                                </svg>
                                            )}
                                        </button>
                                    </div>
                                    {isDeletePending && (
                                        <div
                                            role="alertdialog"
                                            aria-label={`Delete ${job.source_filename || 'saved track'} confirmation`}
                                            style={{ borderTop: '1px solid #555', padding: '10px 12px', background: 'rgba(0, 0, 0, 0.18)' }}
                                        >
                                            <div style={{ color: '#f0c4c4', fontSize: '12px', lineHeight: 1.45 }}>
                                                Permanently delete this job, its original audio, stems, MIDI, and BPM data? This cannot be undone.
                                            </div>
                                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '9px' }}>
                                                <button
                                                    type="button"
                                                    onClick={() => setPendingDeletion(null)}
                                                    disabled={isDeleting}
                                                    style={{ padding: '5px 9px', border: '1px solid #666', borderRadius: '3px', background: '#3c3c3c', color: '#ddd', cursor: isDeleting ? 'wait' : 'pointer' }}
                                                >Cancel</button>
                                                <button
                                                    type="button"
                                                    onClick={async () => {
                                                        const wasDeleted = await onDelete?.(job);
                                                        if (wasDeleted) setPendingDeletion(null);
                                                    }}
                                                    disabled={isDeleting}
                                                    style={{ padding: '5px 9px', border: '1px solid #a85f5f', borderRadius: '3px', background: '#7f3535', color: '#fff', cursor: isDeleting ? 'wait' : 'pointer' }}
                                                >{isDeleting ? 'Deleting…' : 'Delete job'}</button>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </section>
        </div>
    );
}
