/**
 * Displays the authenticated user's saved-job library with refresh, reopen,
 * retention information, and deletion confirmation.
 */
import React from 'react';
import { jobExpiryMilliseconds } from '../../app/jobHistory';

function formatUpdatedAt(value) {
    if (!value) return 'Saved job';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Saved job';
    return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function statusLabel(status) {
    return String(status || 'unknown').replaceAll('_', ' ');
}

function formatExpiry(expiresAt) {
    const expiry = jobExpiryMilliseconds(expiresAt);
    if (!Number.isFinite(expiry) || expiry <= 0) {
        return 'Expiration unavailable';
    }

    const millisecondsRemaining = expiry - Date.now();
    if (millisecondsRemaining <= 0) return 'Expired';

    const daysRemaining = Math.ceil(millisecondsRemaining / (24 * 60 * 60 * 1000));
    if (daysRemaining === 1) return 'expires in 1 day';
    return `expires in ${daysRemaining} days`;
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
    jobKind = 'stem',
    selectionDisabled = false,
}) {
    const [pendingDeletion, setPendingDeletion] = React.useState(null);
    const isSheet = jobKind === 'sheet';
    const isScore = jobKind === 'score' || isSheet;
    const title = isScore ? (isSheet ? 'MIDI to Sheet history' : 'Score to MIDI history') : 'Previous jobs';
    const fallbackName = isScore ? 'Untitled score' : 'Untitled audio';

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
                padding: '20px', background: 'rgba(37, 52, 70, 0.28)', boxSizing: 'border-box',
            }}
        >
            <section
                role="dialog"
                aria-modal="true"
                aria-label={title}
                style={{
                    width: 'min(680px, 100%)', minHeight: '220px', maxHeight: 'min(620px, calc(100vh - 40px))', overflow: 'hidden',
                    background: 'var(--studio-surface)', border: '1px solid var(--studio-border)', borderRadius: '7px', padding: '16px',
                    boxShadow: '0 18px 50px rgba(44, 62, 80, 0.22)', display: 'flex', flexDirection: 'column',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                    <div style={{ color: 'var(--studio-text)', fontSize: '16px', fontWeight: '700' }}>{title}</div>
                    <div style={{ color: 'var(--studio-text-muted)', fontSize: '12px', flex: 1 }}>
                        {isScore
                            ? (isLoading ? 'Loading saved scores…' : (isSheet ? 'Select a job to reopen its submitted MIDI and sheet.' : 'Select a score to reopen its sheet and MIDI.'))
                            : (isLoading ? 'Loading saved tracks…' : 'Select a track to reopen its source, stems, MIDI, and BPM.')}
                    </div>
                    <button
                        type="button"
                        onClick={onRefresh}
                        disabled={isLoading}
                        aria-label="Refresh previous jobs"
                        title="Refresh previous jobs"
                        style={{
                            width: '28px', height: '28px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                            background: 'var(--studio-surface-raised)', color: 'var(--studio-text-secondary)', border: '1px solid var(--studio-border-strong)', borderRadius: '4px',
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
                            width: '28px', height: '28px', padding: 0, border: '1px solid var(--studio-border-strong)', borderRadius: '4px',
                            background: 'var(--studio-surface-raised)', color: 'var(--studio-text-secondary)', cursor: 'pointer', fontSize: '19px', lineHeight: 1,
                        }}
                    >×</button>
                </div>

                {error && <div role="alert" style={{ color: '#a93845', fontSize: '12px' }}>{error}</div>}
                {!error && !isLoading && jobs.length === 0 && (
                    <div style={{ color: 'var(--studio-text-muted)', fontSize: '12px' }}>{isScore ? 'No saved score jobs for this account yet.' : 'No saved processing jobs for this account yet.'}</div>
                )}
                {jobs.length > 0 && (
                    <div style={{ display: 'flex', flex: '1 1 auto', flexDirection: 'column', gap: '7px', minHeight: 0, overflowY: 'auto', paddingRight: '2px' }}>
                        {jobs.map((job) => {
                            const isActive = job.job_id === activeJobId;
                            const isTerminal = ['completed', 'failed'].includes(job.status);
                            const isDeleting = deletingJobId === job.job_id;
                            const isDeletePending = pendingDeletion?.job_id === job.job_id;
                            return (
                                <div
                                    key={job.job_id}
                                    style={{
                                        width: '100%', minHeight: '84px', flex: '0 0 auto', boxSizing: 'border-box',
                                        background: isActive ? '#e5f4eb' : 'var(--studio-surface-muted)', color: 'var(--studio-text)',
                                        border: `1px solid ${isActive ? '#9dcfb1' : 'var(--studio-border)'}`, borderRadius: '4px', overflow: 'hidden',
                                    }}
                                >
                                    <div style={{ display: 'flex', width: '100%', minHeight: '82px', alignItems: 'stretch' }}>
                                        <button
                                            type="button"
                                            onClick={() => onSelect(job)}
                                            disabled={selectionDisabled}
                                            aria-pressed={isActive}
                                            title={selectionDisabled ? 'Wait for the current upload to finish' : `Open ${job.source_filename || fallbackName}`}
                                            style={{
                                                display: 'block', minWidth: 0, minHeight: '82px', flex: '1 1 auto', boxSizing: 'border-box',
                                                textAlign: 'left', padding: '11px 13px', background: 'transparent', color: 'var(--studio-text)',
                                                border: 0, cursor: 'pointer',
                                            }}
                                        >
                                            <div style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '14px', fontWeight: '700', lineHeight: 1.25 }}>
                                                {job.source_filename || fallbackName}
                                            </div>
                                            <div style={{ display: 'block', color: isActive ? '#2d7750' : 'var(--studio-text-muted)', fontSize: '12px', marginTop: '5px', lineHeight: 1.25 }}>
                                                {statusLabel(job.status)} · {formatUpdatedAt(job.updated_at)}
                                            </div>
                                            <div
                                                style={{ display: 'block', color: isActive ? '#4a8565' : '#6b9277', fontSize: '12px', marginTop: '3px', lineHeight: 1.25 }}
                                                title={jobExpiryMilliseconds(job.expires_at) > 0 ? new Date(jobExpiryMilliseconds(job.expires_at)).toLocaleString() : undefined}
                                            >
                                                {formatExpiry(job.expires_at)}
                                            </div>
                                        </button>
                                        {onDelete && <button
                                            type="button"
                                            onClick={() => setPendingDeletion(job)}
                                            disabled={!isTerminal || isDeleting}
                                            aria-label={`Delete ${job.source_filename || 'saved track'}`}
                                            title={isTerminal ? 'Delete this job and all of its files' : 'Only completed or failed jobs can be deleted'}
                                            style={{
                                                width: '46px', minHeight: '82px', flex: '0 0 46px', border: 0, borderLeft: '1px solid var(--studio-border)',
                                                background: 'transparent', color: isTerminal ? '#c1434f' : 'var(--studio-text-muted)',
                                                cursor: isTerminal && !isDeleting ? 'pointer' : 'not-allowed', opacity: isDeleting ? 0.6 : 1,
                                            }}
                                        >
                                            {isDeleting ? '…' : (
                                                <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="currentColor">
                                                    <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-3 6h12l-1 12H7L6 9Zm4 3v6h2v-6h-2Zm4 0v6h2v-6h-2Z" />
                                                </svg>
                                            )}
                                        </button>}
                                    </div>
                                    {isDeletePending && (
                                        <div
                                            role="alertdialog"
                                            aria-label={`Delete ${job.source_filename || 'saved track'} confirmation`}
                                            style={{ borderTop: '1px solid var(--studio-border)', padding: '10px 12px', background: 'var(--studio-danger-soft)' }}
                                        >
                                            <div style={{ color: '#8d2733', fontSize: '12px', lineHeight: 1.45 }}>
                                                Permanently delete this job, its original audio, stems, MIDI, and BPM data? This cannot be undone.
                                            </div>
                                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '9px' }}>
                                                <button
                                                    type="button"
                                                    onClick={() => setPendingDeletion(null)}
                                                    disabled={isDeleting}
                                                    style={{ padding: '5px 9px', border: '1px solid var(--studio-border-strong)', borderRadius: '3px', background: 'var(--studio-surface)', color: 'var(--studio-text)', cursor: isDeleting ? 'wait' : 'pointer' }}
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
