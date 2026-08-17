import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes } from 'react-router-dom';
import ArchitecturePage from './components/ArchitecturePage';
import AuthPanel from './components/AuthPanel';
import StemSplitter from './components/StemSplitter/StemSplitter';
import PreviousJobs from './components/StemSplitter/PreviousJobs';
import './assets/css/styles.css';
import {
    confirmSignUp,
    getCurrentSession,
    isCognitoConfigured,
    signIn,
    signOut,
    signUp,
} from './auth/cognito';

const JOB_API_URL = import.meta.env.VITE_JOB_API_URL?.replace(/\/$/, '');
const WEBSOCKET_URL = import.meta.env.VITE_WEBSOCKET_URL;
const POLL_INTERVAL_MS = 5_000;
const WEBSOCKET_HEARTBEAT_INTERVAL_MS = 120_000;
const RECONNECT_MAX_DELAY_MS = 30_000;
const JOB_REFRESH_BACKOFF_INITIAL_MS = 5_000;
const JOB_REFRESH_BACKOFF_MAX_MS = 60_000;
const PRESIGNED_URL_REFRESH_SAFETY_MS = 60_000;
const MAX_SOURCE_UPLOAD_BYTES = 256 * 1024 * 1024;
const PENDING_SIGN_UP_STORAGE_KEY = 'clouddsp.pendingSignUp';

function readPendingSignUp() {
    try {
        const value = window.localStorage.getItem(PENDING_SIGN_UP_STORAGE_KEY);
        if (!value) return null;
        const pending = JSON.parse(value);
        return typeof pending?.email === 'string' && pending.email
            ? { email: pending.email, displayName: pending.displayName || '' }
            : null;
    } catch {
        return null;
    }
}

function isJobPending(job) {
    return job && !['completed', 'failed'].includes(job.status);
}

function hasTerminalMidiArtifacts(job) {
    /*
     * A top-level ``completed`` status is normally sufficient, but the
     * browser must keep fetching until the snapshot actually contains every
     * stem's terminal MIDI state and a usable URL for each successful result.
     * This protects the workspace from a missed WebSocket hint or a stale
     * eventually-consistent API read at the exact time Batch finishes.
     */
    const stems = job?.stems || {};
    const midi = job?.midi || {};
    const stemNames = Object.keys(stems);
    if (stemNames.length === 0) return false;
    return stemNames.every((stemName) => {
        const artifact = midi[stemName];
        if (artifact?.status === 'failed') return true;
        return artifact?.status === 'ready' && Boolean(artifact.url);
    });
}

function needsJobRefresh(job) {
    // A failed ingestion/worker job is terminal even when it never produced
    // stems or MIDI. Continuing to poll it makes the browser look as if it is
    // still waiting for results that cannot arrive.
    if (job?.status === 'failed') return false;
    return !job || isJobPending(job) || !hasTerminalMidiArtifacts(job);
}

function messageForJob(job, fallback) {
    if (!job) return fallback;
    if (job.status === 'source_ingestion') return 'Downloading audio from the linked source…';
    if (job.status === 'upload_pending') return 'Upload complete. Waiting for AWS Batch capacity…';
    if (job.status === 'stem_processing') return 'AWS Batch is separating stems…';
    if (job.status === 'midi_processing') return 'Stems are ready. MIDI extraction is still running…';
    if (job.status === 'completed') return 'Stems and MIDI extraction are complete.';
    if (job.status === 'failed') return job.error || 'Processing failed. See the job status for details.';
    return fallback;
}

function urlsForReadyArtifacts(artifacts) {
    return Object.fromEntries(
        Object.entries(artifacts || {})
            .filter(([, artifact]) => artifact?.status === 'ready' && artifact.url)
            .map(([name, artifact]) => [name, artifact.url]),
    );
}

function readyArtifactNames(artifacts) {
    return Object.entries(artifacts || {})
        .filter(([, artifact]) => artifact?.status === 'ready' && artifact.url)
        .map(([name]) => name);
}

function presignedUrlIsUsable(url) {
    try {
        const parsed = new URL(url);
        const legacyExpires = Number(parsed.searchParams.get('Expires'));
        if (Number.isFinite(legacyExpires) && legacyExpires > 0) {
            return legacyExpires * 1_000 > Date.now() + PRESIGNED_URL_REFRESH_SAFETY_MS;
        }

        const signatureDate = parsed.searchParams.get('X-Amz-Date');
        const signatureLifetime = Number(parsed.searchParams.get('X-Amz-Expires'));
        if (signatureDate && Number.isFinite(signatureLifetime)) {
            const match = signatureDate.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
            if (!match) return false;
            const [, year, month, day, hour, minute, second] = match;
            const expiresAt = Date.UTC(year, Number(month) - 1, day, hour, minute, second)
                + signatureLifetime * 1_000;
            return expiresAt > Date.now() + PRESIGNED_URL_REFRESH_SAFETY_MS;
        }

        // This branch is only for a future non-expiring artifact source. Job
        // API URLs today always use one of the two presigned URL formats.
        return true;
    } catch {
        return false;
    }
}

function preserveReadyArtifactUrls(previous, snapshot) {
    /*
     * Job API snapshots carry newly signed URLs. Replacing an already-mounted
     * media element's URL on every progress poll can cancel its load, and it
     * causes the MIDI loader to re-download the same immutable artifact. Keep
     * a URL only while it has at least one minute left; otherwise accept the
     * Job API's fresh URL before the browser retries an expired signature.
     */
    if (!previous) return snapshot;
    const result = { ...snapshot };
    if (snapshot.original_url && previous.original_url && presignedUrlIsUsable(previous.original_url)) {
        result.original_url = previous.original_url;
    }
    for (const collectionName of ['stems', 'midi']) {
        const previousArtifacts = previous[collectionName] || {};
        const nextArtifacts = snapshot[collectionName] || {};
        result[collectionName] = Object.fromEntries(
            Object.entries(nextArtifacts).map(([name, artifact]) => {
                const prior = previousArtifacts[name];
                if (
                    artifact?.status === 'ready'
                    && artifact.url
                    && prior?.status === 'ready'
                    && prior.url
                    && presignedUrlIsUsable(prior.url)
                ) {
                    return [name, { ...artifact, url: prior.url }];
                }
                return [name, artifact];
            }),
        );
    }
    return result;
}

function NavBar({ authProps }) {
    const navStyle = {
        background: '#161b22', padding: '15px 20px', display: 'flex', gap: '20px',
        borderBottom: '1px solid #444', marginBottom: '40px', alignItems: 'center',
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)', flexWrap: 'wrap',
    };

    return (
        <div style={navStyle}>
            <Link to="/" style={{ color: '#fff', fontWeight: '900', fontSize: '20px', marginRight: '20px', letterSpacing: '1px', textDecoration: 'none' }}>CloudDSP</Link>
            <NavLink
                to="/"
                end
                style={({ isActive }) => ({ color: isActive ? '#f3f8fd' : '#aebbc7', fontSize: '13px', fontWeight: '700', textDecoration: 'none' })}
            >Studio</NavLink>
            <NavLink
                to="/architecture"
                style={({ isActive }) => ({ color: isActive ? '#f3f8fd' : '#aebbc7', fontSize: '13px', fontWeight: '700', textDecoration: 'none' })}
            >Architecture</NavLink>
            <div style={{ marginLeft: 'auto' }}><AuthPanel {...authProps} /></div>
        </div>
    );
}

export default function App() {
    const [stemFile, setStemFile] = useState(null);
    const [stemFileName, setStemFileName] = useState('No file loaded');
    const [splitMode, setSplitMode] = useState('6-stems');
    const [statusMessage, setStatusMessage] = useState('Sign in and upload an audio file to begin.');
    const [errorMsg, setErrorMsg] = useState('');
    const [authSession, setAuthSession] = useState(null);
    const [authLoading, setAuthLoading] = useState(isCognitoConfigured);
    const [pendingSignUp, setPendingSignUp] = useState(readPendingSignUp);
    const [activeJobId, setActiveJobId] = useState(null);
    const [jobSnapshots, setJobSnapshots] = useState({});
    const [isUploading, setIsUploading] = useState(false);
    const [previousJobs, setPreviousJobs] = useState([]);
    const [isPreviousJobsLoading, setIsPreviousJobsLoading] = useState(false);
    const [previousJobsError, setPreviousJobsError] = useState('');
    const [isPreviousJobsOpen, setIsPreviousJobsOpen] = useState(false);
    const [deletingJobId, setDeletingJobId] = useState(null);
    const [isRestoringHistoryJob, setIsRestoringHistoryJob] = useState(false);
    const [isHistoryJob, setIsHistoryJob] = useState(false);

    const socketRef = useRef(null);
    const shouldReconnectRef = useRef(false);
    const reconnectTimerRef = useRef(null);
    const reconnectAttemptsRef = useRef(0);
    const heartbeatTimerRef = useRef(null);
    const activeJobIdRef = useRef(null);
    const jobSnapshotsRef = useRef({});
    const jobRefreshInFlightRef = useRef(new Set());
    const jobRefreshBackoffRef = useRef(new Map());
    const deletedJobIdsRef = useRef(new Set());

    const currentJob = activeJobId ? jobSnapshots[activeJobId] : null;
    const authUsername = authSession?.username;
    const stemUrls = useMemo(() => urlsForReadyArtifacts(currentJob?.stems), [currentJob]);
    const midiUrls = useMemo(() => urlsForReadyArtifacts(currentJob?.midi), [currentJob]);
    const midiStates = currentJob?.midi || {};
    // A saved job has already been submitted. While its snapshot and private
    // artifacts are being restored, do not describe the wait as new Batch or
    // MIDI processing work.
    const isSplitting = isUploading || (!isRestoringHistoryJob && isJobPending(currentJob));
    useEffect(() => {
        activeJobIdRef.current = activeJobId;
    }, [activeJobId]);

    useEffect(() => {
        jobSnapshotsRef.current = jobSnapshots;
    }, [jobSnapshots]);

    const restoreSession = useCallback(async () => {
        try {
            const session = await getCurrentSession();
            setAuthSession(session);
            return session;
        } catch (error) {
            console.warn('Could not restore Cognito session:', error);
            setAuthSession(null);
            return null;
        } finally {
            setAuthLoading(false);
        }
    }, []);

    useEffect(() => {
        restoreSession();
    }, [restoreSession]);

    // The durable account-scoped Job API is the only source of job history.
    // Do not restore a browser-local job queue after a reload: deleted jobs
    // would otherwise be polled forever and bypass the library's ownership
    // filtering. The currently open job remains React state for this tab only.
    useEffect(() => {
        if (authUsername) {
            // One-time migration cleanup for frontend versions that persisted
            // active jobs before the account-backed job library existed.
            sessionStorage.removeItem(`clouddsp.activeJobs.${authUsername}`);
        } else {
            setActiveJobId(null);
            setJobSnapshots({});
            setIsPreviousJobsOpen(false);
            setIsRestoringHistoryJob(false);
            setIsHistoryJob(false);
            jobRefreshInFlightRef.current.clear();
            jobRefreshBackoffRef.current.clear();
            deletedJobIdsRef.current.clear();
        }
    }, [authUsername]);

    const authenticatedFetch = useCallback(async (path, options = {}) => {
        if (!JOB_API_URL) throw new Error('VITE_JOB_API_URL is not configured.');
        const session = await getCurrentSession();
        if (!session) throw new Error('Your session has expired. Sign in again to continue.');
        const response = await fetch(`${JOB_API_URL}${path}`, {
            ...options,
            headers: {
                Authorization: `Bearer ${session.idToken}`,
                ...(options.headers || {}),
            },
        });
        return response;
    }, []);

    const fetchPreviousJobs = useCallback(async () => {
        if (!authUsername) {
            setPreviousJobs([]);
            setPreviousJobsError('');
            return [];
        }

        setIsPreviousJobsLoading(true);
        setPreviousJobsError('');
        try {
            const response = await authenticatedFetch('/jobs');
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || `Could not load previous jobs (${response.status}).`);
            }
            const jobs = (Array.isArray(payload.jobs) ? payload.jobs : []).filter(
                (job) => !deletedJobIdsRef.current.has(job.job_id),
            );
            setPreviousJobs(jobs);
            return jobs;
        } catch (error) {
            console.warn('Could not load previous CloudDSP jobs:', error);
            setPreviousJobsError(error.message || 'Could not load previous jobs.');
            return [];
        } finally {
            setIsPreviousJobsLoading(false);
        }
    }, [authUsername, authenticatedFetch]);

    useEffect(() => {
        fetchPreviousJobs();
    }, [fetchPreviousJobs]);

    const openPreviousJobs = useCallback(() => {
        setIsPreviousJobsOpen(true);
        void fetchPreviousJobs();
    }, [fetchPreviousJobs]);

    const fetchJobSnapshot = useCallback(async (jobId, {
        showError = false,
        force = false,
        replaceArtifactUrls = false,
    } = {}) => {
        const inFlight = jobRefreshInFlightRef.current;
        const retryState = jobRefreshBackoffRef.current.get(jobId);
        if (inFlight.has(jobId) || (!force && retryState?.nextAttemptAt > Date.now())) return null;

        inFlight.add(jobId);
        try {
            console.info(`[CloudDSP] Requesting stems and MIDI snapshot for job ${jobId}.`);
            const response = await authenticatedFetch(`/jobs/${encodeURIComponent(jobId)}`);
            if (!response.ok) {
                console.error(`[CloudDSP] Job snapshot request failed for ${jobId} (HTTP ${response.status}).`);
                const error = new Error(`Could not refresh job ${jobId} (${response.status}).`);
                error.status = response.status;
                throw error;
            }
            const snapshot = await response.json();
            const stemNames = readyArtifactNames(snapshot.stems);
            const midiNames = readyArtifactNames(snapshot.midi);
            console.info(
                `[CloudDSP] Received job snapshot for ${jobId}. `
                + `Signed S3 stem URLs: ${stemNames.length ? stemNames.join(', ') : 'none'}. `
                + `Signed S3 MIDI URLs: ${midiNames.length ? midiNames.join(', ') : 'none'}.`,
            );
            jobRefreshBackoffRef.current.delete(jobId);
            setJobSnapshots((current) => {
                const previous = replaceArtifactUrls ? null : current[jobId];
                if (previous && Number(previous.revision || 0) > Number(snapshot.revision || 0)) return current;
                return { ...current, [jobId]: preserveReadyArtifactUrls(previous, snapshot) };
            });
            setPreviousJobs((current) => current.map((job) => (
                job.job_id === jobId
                    ? {
                        ...job,
                        source_filename: snapshot.source_filename || job.source_filename,
                        status: snapshot.status || job.status,
                        stem_mode: snapshot.stem_mode || job.stem_mode,
                        tempo: snapshot.tempo || job.tempo,
                        updated_at: snapshot.updated_at || job.updated_at,
                        expires_at: snapshot.expires_at ?? job.expires_at,
                    }
                    : job
            )));
            return snapshot;
        } catch (error) {
            const status = Number(error.status);
            if (status === 404) {
                // A job may have expired or been removed in another browser.
                // It cannot become valid on a later poll, so drop it from the
                // active workspace instead of retrying indefinitely.
                setJobSnapshots((current) => {
                    const remaining = { ...current };
                    delete remaining[jobId];
                    return remaining;
                });
                setActiveJobId((current) => current === jobId ? null : current);
                void fetchPreviousJobs();
                jobRefreshBackoffRef.current.delete(jobId);
                console.info(`Removed unavailable CloudDSP job ${jobId} from the active workspace.`);
            } else if (status === 429 || status >= 500) {
                const attempts = (retryState?.attempts || 0) + 1;
                const delay = Math.min(
                    JOB_REFRESH_BACKOFF_INITIAL_MS * (2 ** (attempts - 1)),
                    JOB_REFRESH_BACKOFF_MAX_MS,
                );
                jobRefreshBackoffRef.current.set(jobId, {
                    attempts,
                    nextAttemptAt: Date.now() + delay,
                });
                console.warn(`Job snapshot refresh failed for ${jobId} (${status}); retrying in ${delay / 1000}s.`, error);
            } else {
                jobRefreshBackoffRef.current.delete(jobId);
                console.warn(`Job snapshot refresh failed for ${jobId}:`, error);
            }
            if (showError) setErrorMsg(error.message || 'Could not refresh the processing job.');
            return null;
        } finally {
            inFlight.delete(jobId);
        }
    }, [authenticatedFetch, fetchPreviousJobs]);

    const subscribeToActiveJob = useCallback((socket, jobId = activeJobIdRef.current) => {
        if (!jobId) return;
        if (socket?.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({ action: 'subscribe', job_id: jobId }));
    }, []);

    const connectWebSocket = useCallback(async () => {
        if (!WEBSOCKET_URL || !authUsername) return;
        const existingSocket = socketRef.current;
        if (existingSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(existingSocket.readyState)) return;

        let session;
        let socket;
        try {
            session = await getCurrentSession();
            if (!session) {
                setErrorMsg('Your session has expired. Sign in again to reconnect.');
                return;
            }
            setAuthSession(session);
            const socketUrl = new URL(WEBSOCKET_URL);
            socketUrl.searchParams.set('token', session.idToken);
            socket = new WebSocket(socketUrl.toString());
        } catch (error) {
            console.warn('Could not open CloudDSP WebSocket:', error);
            setErrorMsg('Could not establish an authenticated WebSocket connection.');
            return;
        }
        socketRef.current = socket;

        socket.onopen = () => {
            reconnectAttemptsRef.current = 0;
            subscribeToActiveJob(socket);
            window.clearInterval(heartbeatTimerRef.current);
            heartbeatTimerRef.current = window.setInterval(() => {
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ action: 'heartbeat' }));
                }
            }, WEBSOCKET_HEARTBEAT_INTERVAL_MS);
            console.info('[CloudDSP] WebSocket connected and subscribed to the active job.');
        };
        socket.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                if (message.type === 'job_updated' && message.job_id === activeJobIdRef.current) {
                    // Notifications are hints, but they should bypass a prior
                    // polling backoff so completed artifacts hydrate promptly.
                    console.info(`[CloudDSP] WebSocket reported an update for job ${message.job_id}; requesting fresh artifacts.`);
                    fetchJobSnapshot(message.job_id, { showError: true, force: true });
                } else if (message.type === 'error') {
                    console.error('[CloudDSP] WebSocket reported an application error:', message.error);
                    setErrorMsg(message.error || 'The CloudDSP WebSocket rejected a request.');
                }
            } catch (error) {
                console.warn('[CloudDSP] Ignoring invalid WebSocket message:', error);
            }
        };
        socket.onerror = () => console.warn('[CloudDSP] WebSocket transport error.');
        socket.onclose = () => {
            window.clearInterval(heartbeatTimerRef.current);
            heartbeatTimerRef.current = null;
            socketRef.current = null;
            if (!shouldReconnectRef.current) return;
            const delay = Math.min(1_000 * (2 ** reconnectAttemptsRef.current), RECONNECT_MAX_DELAY_MS);
            reconnectAttemptsRef.current += 1;
            reconnectTimerRef.current = window.setTimeout(() => connectWebSocket(), delay);
        };
    }, [authUsername, fetchJobSnapshot, subscribeToActiveJob]);

    useEffect(() => {
        shouldReconnectRef.current = Boolean(authUsername && WEBSOCKET_URL);
        if (shouldReconnectRef.current) connectWebSocket();
        return () => {
            shouldReconnectRef.current = false;
            window.clearTimeout(reconnectTimerRef.current);
            window.clearInterval(heartbeatTimerRef.current);
            heartbeatTimerRef.current = null;
            socketRef.current?.close();
            socketRef.current = null;
        };
    }, [authUsername, connectWebSocket]);

    useEffect(() => {
        subscribeToActiveJob(socketRef.current, activeJobId);
    }, [activeJobId, subscribeToActiveJob]);

    useEffect(() => {
        if (!authUsername || !activeJobId) return undefined;
        const refreshPendingJobs = () => {
            if (needsJobRefresh(jobSnapshotsRef.current[activeJobId])) {
                fetchJobSnapshot(activeJobId);
            }
        };
        refreshPendingJobs();
        const timer = window.setInterval(() => {
            refreshPendingJobs();
        }, POLL_INTERVAL_MS);
        return () => window.clearInterval(timer);
    }, [activeJobId, authUsername, fetchJobSnapshot]);

    useEffect(() => {
        if (!currentJob) return;
        if (isRestoringHistoryJob) {
            setStatusMessage('Stems and MIDI will arrive shortly.');
            return;
        }
        setStatusMessage(messageForJob(currentJob, 'Processing…'));
        if (currentJob.status === 'failed') setErrorMsg(currentJob.error || 'CloudDSP processing failed.');
    }, [currentJob, isRestoringHistoryJob]);

    const beginNewUpload = () => {
        setActiveJobId(null);
        setJobSnapshots({});
        setIsRestoringHistoryJob(false);
        setIsHistoryJob(false);
        setErrorMsg('');
        setStatusMessage('Ready to upload audio.');
    };

    const openPreviousJob = async (selectedJob) => {
        const jobId = selectedJob?.job_id;
        if (!jobId) return;

        setErrorMsg('');
        setStemFile(null);
        setStemFileName(selectedJob.source_filename || 'Saved CloudDSP job');
        setActiveJobId(jobId);
        setIsRestoringHistoryJob(true);
        setIsHistoryJob(true);
        setStatusMessage('Stems and MIDI will arrive shortly.');
        subscribeToActiveJob(socketRef.current, jobId);

        // Opening a saved job is an explicit reload. Accept fresh signed URLs
        // even if this tab had displayed the same job earlier in the day.
        try {
            const snapshot = await fetchJobSnapshot(jobId, {
                showError: true,
                force: true,
                replaceArtifactUrls: true,
            });
            if (snapshot) {
                setStemFileName(snapshot.source_filename || selectedJob.source_filename || 'Saved CloudDSP job');
            }
        } finally {
            setIsRestoringHistoryJob(false);
        }
    };

    const selectPreviousJob = async (selectedJob) => {
        setIsPreviousJobsOpen(false);
        await openPreviousJob(selectedJob);
    };

    const deletePreviousJob = useCallback(async (selectedJob) => {
        const jobId = selectedJob?.job_id;
        if (!jobId) return false;

        setDeletingJobId(jobId);
        setPreviousJobsError('');
        try {
            console.info(`[CloudDSP] Requesting permanent deletion for job ${jobId}.`);
            const response = await authenticatedFetch(`/jobs/${encodeURIComponent(jobId)}`, {
                method: 'DELETE',
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload.error || `Could not delete job ${jobId} (${response.status}).`);
            }

            deletedJobIdsRef.current.add(jobId);
            setPreviousJobs((current) => current.filter((job) => job.job_id !== jobId));
            setJobSnapshots((current) => {
                const remaining = { ...current };
                delete remaining[jobId];
                return remaining;
            });
            jobRefreshBackoffRef.current.delete(jobId);
            if (activeJobIdRef.current === jobId) {
                setActiveJobId(null);
                setStemFile(null);
                setStemFileName('No file loaded');
                setIsRestoringHistoryJob(false);
                setIsHistoryJob(false);
                setErrorMsg('');
                setStatusMessage('Job deleted. Select an audio file to begin.');
            }
            console.info(`[CloudDSP] Deleted job ${jobId} and ${payload.deleted_objects ?? 0} stored file version(s).`);
            return true;
        } catch (error) {
            console.error(`[CloudDSP] Could not delete job ${jobId}:`, error);
            setPreviousJobsError(error.message || 'Could not delete the job.');
            return false;
        } finally {
            setDeletingJobId(null);
        }
    }, [authenticatedFetch]);

    const executeStemSplit = async () => {
        if (!stemFile) {
            setErrorMsg('Please select an audio file first.');
            return;
        }
        if (!authSession) {
            setErrorMsg('Sign in before uploading audio.');
            return;
        }
        if (!Number.isFinite(stemFile.size) || stemFile.size < 1 || stemFile.size > MAX_SOURCE_UPLOAD_BYTES) {
            setErrorMsg('Audio files must be between 1 byte and 256 MiB.');
            return;
        }

        setIsUploading(true);
        setErrorMsg('');
        setStatusMessage('Creating a durable processing job…');
        try {
            const contentType = stemFile.type || 'application/octet-stream';
            const response = await authenticatedFetch('/jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: stemFile.name,
                    content_type: contentType,
                    size_bytes: stemFile.size,
                    stem_mode: splitMode,
                }),
            });
            const job = await response.json();
            if (!response.ok) throw new Error(job.error || `Could not create a job (${response.status}).`);
            if (!job.job_id || !job.upload_url || !job.upload_fields) {
                throw new Error('The job API returned an incomplete secure upload contract. Please refresh and try again.');
            }

            setActiveJobId(job.job_id);
            setJobSnapshots((current) => ({
                ...current,
                [job.job_id]: { job_id: job.job_id, status: job.status, revision: job.revision, stems: {}, midi: {} },
            }));
            setPreviousJobs((current) => [
                {
                    job_id: job.job_id,
                    source_filename: stemFile.name,
                    status: job.status,
                    stem_mode: splitMode,
                    created_at: new Date().toISOString(),
                    updated_at: new Date().toISOString(),
                    expires_at: job.expires_at,
                },
                ...current.filter((existingJob) => existingJob.job_id !== job.job_id),
            ]);
            subscribeToActiveJob(socketRef.current, job.job_id);

            setStatusMessage('Uploading audio to the secure job location…');
            const uploadForm = new FormData();
            Object.entries(job.upload_fields).forEach(([name, value]) => uploadForm.append(name, value));
            uploadForm.append('file', stemFile);
            const uploadResponse = await fetch(job.upload_url, {
                method: 'POST',
                body: uploadForm,
            });
            if (!uploadResponse.ok) {
                throw new Error(`S3 upload failed (${uploadResponse.status}). The file may exceed the 256 MiB limit or the upload policy may have expired.`);
            }
            setStatusMessage('Upload complete. Waiting for AWS Batch capacity…');
            await fetchJobSnapshot(job.job_id, { showError: true });
        } catch (error) {
            console.error('CloudDSP upload failed:', error);
            setErrorMsg(error.message || 'Failed to upload audio to CloudDSP.');
        } finally {
            setIsUploading(false);
        }
    };

    const executeLinkExtraction = async (sourceUrl) => {
        const trimmedSourceUrl = typeof sourceUrl === 'string' ? sourceUrl.trim() : '';
        try {
            const parsedUrl = new URL(trimmedSourceUrl);
            if (!['http:', 'https:'].includes(parsedUrl.protocol) || !parsedUrl.hostname) {
                throw new Error('Paste a complete HTTP or HTTPS media URL.');
            }
        } catch (error) {
            setErrorMsg(error.message || 'Paste a complete HTTP or HTTPS media URL.');
            return false;
        }
        if (!authSession) {
            setErrorMsg('Sign in before extracting audio from a link.');
            return false;
        }

        beginNewUpload();
        setStemFile(null);
        setStemFileName(trimmedSourceUrl);
        setIsUploading(true);
        setErrorMsg('');
        setStatusMessage('Creating a linked-source processing job…');
        try {
            const response = await authenticatedFetch('/jobs/link', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    source_url: trimmedSourceUrl,
                    stem_mode: splitMode,
                }),
            });
            const job = await response.json();
            if (!response.ok) {
                throw new Error(job.error || `Could not create a linked-source job (${response.status}).`);
            }
            if (!job.job_id) {
                throw new Error('The job API returned an incomplete linked-source job.');
            }

            setActiveJobId(job.job_id);
            setJobSnapshots((current) => ({
                ...current,
                [job.job_id]: {
                    job_id: job.job_id,
                    status: job.status || 'source_ingestion',
                    revision: job.revision || 1,
                    stems: {},
                    midi: {},
                },
            }));
            setPreviousJobs((current) => [
                {
                    job_id: job.job_id,
                    source_filename: trimmedSourceUrl,
                    status: job.status || 'source_ingestion',
                    stem_mode: splitMode,
                    created_at: new Date().toISOString(),
                    updated_at: new Date().toISOString(),
                    expires_at: job.expires_at,
                },
                ...current.filter((existingJob) => existingJob.job_id !== job.job_id),
            ]);
            subscribeToActiveJob(socketRef.current, job.job_id);
            setStatusMessage('Downloading audio from the linked source…');
            await fetchJobSnapshot(job.job_id, { showError: true, force: true });
            return true;
        } catch (error) {
            console.error('CloudDSP linked-source ingestion request failed:', error);
            setErrorMsg(error.message || 'Failed to start linked-source extraction.');
            return false;
        } finally {
            setIsUploading(false);
        }
    };

    const handleSignIn = async (email, password) => {
        const session = await signIn(email, password);
        setAuthSession(session);
        setErrorMsg('');
        setStatusMessage('Signed in. Select an audio file to begin.');
    };

    const savePendingSignUp = useCallback((pending) => {
        setPendingSignUp(pending);
        try {
            if (pending) {
                window.localStorage.setItem(PENDING_SIGN_UP_STORAGE_KEY, JSON.stringify(pending));
            } else {
                window.localStorage.removeItem(PENDING_SIGN_UP_STORAGE_KEY);
            }
        } catch (error) {
            // The dialog remains usable if private browsing prevents persistent
            // storage; it will simply not survive a page reload.
            console.warn('Could not persist pending CloudDSP email verification:', error);
        }
    }, []);

    const handleSignUp = async (email, password, displayName) => {
        const result = await signUp(email, password, displayName);
        if (!result.confirmed) {
            savePendingSignUp({ email: email.trim(), displayName: displayName.trim() });
        }
        return result;
    };

    const handleConfirmSignUp = async (email, code) => {
        const result = await confirmSignUp(email, code);
        savePendingSignUp(null);
        return result;
    };

    const handleSignOut = () => {
        signOut();
        setAuthSession(null);
        setIsPreviousJobsOpen(false);
        setIsRestoringHistoryJob(false);
        setIsHistoryJob(false);
        setErrorMsg('');
        setStatusMessage('Signed out.');
    };

    const stemProps = {
        file: stemFile,
        setFile: setStemFile,
        fileName: stemFileName,
        setFileName: setStemFileName,
        splitMode,
        setSplitMode,
        isSplitting,
        statusMessage,
        stemUrls: Object.keys(stemUrls).length ? stemUrls : null,
        midiUrls: Object.keys(midiUrls).length ? midiUrls : null,
        midiStates,
        jobTempo: currentJob?.tempo,
        jobId: currentJob?.job_id || activeJobId,
        // A linked source has no object until ingestion uploads its durable
        // input key. The Job API omits original_url after a pre-upload failure;
        // retain the status check as a client-side guard against a stale API
        // snapshot so the audio loader never requests a known-missing key.
        sourceUrl: currentJob?.status === 'source_ingestion'
            || (currentJob?.source_type === 'yt-dlp'
                && currentJob?.status === 'failed'
                && !currentJob?.source_uploaded)
            ? null
            : currentJob?.original_url,
        isRestoringHistoryJob,
        isHistoryJob,
        errorMsg,
        setErrorMsg,
        executeStemSplit,
        executeLinkExtraction,
        beginNewUpload,
    };

    const authProps = {
        configured: isCognitoConfigured,
        session: authSession,
        pendingVerification: pendingSignUp,
        onSignIn: handleSignIn,
        onSignUp: handleSignUp,
        onConfirmSignUp: handleConfirmSignUp,
        onSignOut: handleSignOut,
        onOpenHistory: openPreviousJobs,
    };

    return (
        <BrowserRouter>
            <div style={{ minHeight: '100vh' }}>
                <NavBar authProps={authProps} />
                <PreviousJobs
                    isOpen={isPreviousJobsOpen}
                    onClose={() => setIsPreviousJobsOpen(false)}
                    jobs={previousJobs}
                    activeJobId={activeJobId}
                    isLoading={isPreviousJobsLoading}
                    error={previousJobsError}
                    onSelect={selectPreviousJob}
                    onRefresh={fetchPreviousJobs}
                    onDelete={deletePreviousJob}
                    deletingJobId={deletingJobId}
                />
                {!authLoading && (!JOB_API_URL || !WEBSOCKET_URL) && (
                    <div style={{ margin: '-24px 20px 20px', color: '#f5c451', fontSize: '13px' }}>
                        Set VITE_JOB_API_URL and VITE_WEBSOCKET_URL before using the processing workspace.
                    </div>
                )}
                <Routes>
                    <Route path="/" element={<div style={{ display: 'flex', justifyContent: 'center' }}><StemSplitter {...stemProps} /></div>} />
                    <Route path="/architecture" element={<ArchitecturePage />} />
                    <Route path="/stems" element={<Navigate to="/" replace />} />
                </Routes>
            </div>
        </BrowserRouter>
    );
}
