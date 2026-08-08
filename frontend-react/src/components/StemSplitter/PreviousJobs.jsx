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
 * Compact library of durable jobs owned by the authenticated browser user.
 * Selecting an item only fetches its snapshot; it never starts a new DSP job.
 */
export default function PreviousJobs({ jobs = [], activeJobId, isLoading, error, onSelect, onRefresh }) {
    return (
        <section aria-label="Previous jobs" style={{
            background: '#292929', border: '1px solid #484848', borderRadius: '4px', padding: '12px',
        }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: jobs.length || error ? '10px' : 0 }}>
                <div style={{ color: '#eee', fontSize: '13px', fontWeight: '700' }}>Previous jobs</div>
                <div style={{ color: '#9d9d9d', fontSize: '12px', flex: 1 }}>
                    {isLoading ? 'Loading saved tracks…' : 'Select a track to reopen its source, stems, MIDI, and BPM.'}
                </div>
                <button
                    type="button"
                    onClick={onRefresh}
                    disabled={isLoading}
                    style={{
                        background: '#3d3d3d', color: '#ddd', border: '1px solid #5a5a5a', borderRadius: '3px',
                        padding: '4px 8px', fontSize: '11px', cursor: isLoading ? 'wait' : 'pointer',
                    }}
                >
                    Refresh
                </button>
            </div>

            {error && <div role="alert" style={{ color: '#f08b8b', fontSize: '12px' }}>{error}</div>}
            {!error && !isLoading && jobs.length === 0 && (
                <div style={{ color: '#999', fontSize: '12px' }}>No saved processing jobs for this account yet.</div>
            )}
            {jobs.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '7px', maxHeight: '142px', overflowY: 'auto', paddingRight: '2px' }}>
                    {jobs.map((job) => {
                        const isActive = job.job_id === activeJobId;
                        return (
                            <button
                                key={job.job_id}
                                type="button"
                                onClick={() => onSelect(job)}
                                aria-pressed={isActive}
                                title={`Open ${job.source_filename || 'saved track'}`}
                                style={{
                                    minWidth: '170px', maxWidth: '250px', textAlign: 'left', padding: '8px 10px',
                                    background: isActive ? '#264d38' : '#353535',
                                    color: '#e8e8e8', border: `1px solid ${isActive ? '#61b680' : '#555'}`,
                                    borderRadius: '4px', cursor: 'pointer',
                                }}
                            >
                                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '12px', fontWeight: '700' }}>
                                    {job.source_filename || 'Untitled audio'}
                                </div>
                                <div style={{ color: isActive ? '#bfe6ca' : '#aaa', fontSize: '11px', marginTop: '3px' }}>
                                    {statusLabel(job.status)} · {formatUpdatedAt(job.updated_at)}
                                </div>
                            </button>
                        );
                    })}
                </div>
            )}
        </section>
    );
}
