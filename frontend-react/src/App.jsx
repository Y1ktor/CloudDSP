import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BrowserRouter, Link, Route, Routes, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import AuthPanel from './components/AuthPanel';
import StemSplitter from './components/StemSplitter/StemSplitter';
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
const RECONNECT_MAX_DELAY_MS = 30_000;
const JOB_REFRESH_BACKOFF_INITIAL_MS = 5_000;
const JOB_REFRESH_BACKOFF_MAX_MS = 60_000;

function getStoredJobs(storageKey) {
    try {
        const parsed = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
        return Array.isArray(parsed) ? parsed.filter((value) => typeof value === 'string') : [];
    } catch {
        return [];
    }
}

function isJobPending(job) {
    return job && !['completed', 'failed'].includes(job.status);
}

function messageForJob(job, fallback) {
    if (!job) return fallback;
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

function preserveReadyArtifactUrls(previous, snapshot) {
    /*
     * Job API snapshots carry newly signed URLs. Replacing an already-mounted
     * media element's URL on every progress poll can cancel its load, and it
     * causes the MIDI loader to re-download the same immutable artifact.
     * Artifacts are immutable within a job, so keep the first usable URL until
     * the browser reloads or a new job starts.
     */
    if (!previous) return snapshot;
    const result = { ...snapshot };
    for (const collectionName of ['stems', 'midi']) {
        const previousArtifacts = previous[collectionName] || {};
        const nextArtifacts = snapshot[collectionName] || {};
        result[collectionName] = Object.fromEntries(
            Object.entries(nextArtifacts).map(([name, artifact]) => {
                const prior = previousArtifacts[name];
                if (artifact?.status === 'ready' && artifact.url && prior?.status === 'ready' && prior.url) {
                    return [name, { ...artifact, url: prior.url }];
                }
                return [name, artifact];
            }),
        );
    }
    return result;
}

function NavBar({ authProps }) {
    const location = useLocation();
    const navStyle = {
        background: '#222', padding: '15px 20px', display: 'flex', gap: '20px',
        borderBottom: '1px solid #444', marginBottom: '40px', alignItems: 'center',
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)', flexWrap: 'wrap',
    };
    const getLinkStyle = (path) => ({
        color: location.pathname === path ? '#fff' : '#aaa',
        textDecoration: 'none', fontWeight: 'bold', padding: '8px 16px',
        borderRadius: '4px', background: location.pathname === path ? '#4CAF50' : 'transparent',
        transition: 'all 0.2s ease-in-out',
    });

    return (
        <div style={navStyle}>
            <div style={{ color: '#fff', fontWeight: '900', fontSize: '20px', marginRight: '20px', letterSpacing: '1px' }}>CloudDSP</div>
            <Link to="/" style={getLinkStyle('/')}>Interactive EQ</Link>
            <Link to="/stems" style={getLinkStyle('/stems')}>Stem Splitter</Link>
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
    const [activeJobIds, setActiveJobIds] = useState([]);
    const [activeJobId, setActiveJobId] = useState(null);
    const [jobSnapshots, setJobSnapshots] = useState({});
    const [isUploading, setIsUploading] = useState(false);

    const socketRef = useRef(null);
    const shouldReconnectRef = useRef(false);
    const reconnectTimerRef = useRef(null);
    const reconnectAttemptsRef = useRef(0);
    const activeJobIdsRef = useRef([]);
    const jobSnapshotsRef = useRef({});
    const jobRefreshInFlightRef = useRef(new Set());
    const jobRefreshBackoffRef = useRef(new Map());

    const currentJob = activeJobId ? jobSnapshots[activeJobId] : null;
    const authUsername = authSession?.username;
    const stemUrls = useMemo(() => urlsForReadyArtifacts(currentJob?.stems), [currentJob]);
    const midiUrls = useMemo(() => urlsForReadyArtifacts(currentJob?.midi), [currentJob]);
    const midiStates = currentJob?.midi || {};
    const isSplitting = isUploading || isJobPending(currentJob);
    const storageKey = authSession ? `clouddsp.activeJobs.${authSession.username}` : null;

    useEffect(() => {
        activeJobIdsRef.current = activeJobIds;
    }, [activeJobIds]);

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

    useEffect(() => {
        if (!storageKey) {
            setActiveJobIds([]);
            setActiveJobId(null);
            setJobSnapshots({});
            return;
        }
        const restoredJobIds = getStoredJobs(storageKey);
        setActiveJobIds(restoredJobIds);
        setActiveJobId(restoredJobIds.at(-1) || null);
        setJobSnapshots({});
        jobRefreshInFlightRef.current.clear();
        jobRefreshBackoffRef.current.clear();
    }, [storageKey]);

    useEffect(() => {
        if (storageKey) sessionStorage.setItem(storageKey, JSON.stringify(activeJobIds));
    }, [activeJobIds, storageKey]);

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

    const fetchJobSnapshot = useCallback(async (jobId, { showError = false, force = false } = {}) => {
        const inFlight = jobRefreshInFlightRef.current;
        const retryState = jobRefreshBackoffRef.current.get(jobId);
        if (inFlight.has(jobId) || (!force && retryState?.nextAttemptAt > Date.now())) return null;

        inFlight.add(jobId);
        try {
            const response = await authenticatedFetch(`/jobs/${encodeURIComponent(jobId)}`);
            if (!response.ok) {
                const error = new Error(`Could not refresh job ${jobId} (${response.status}).`);
                error.status = response.status;
                throw error;
            }
            const snapshot = await response.json();
            jobRefreshBackoffRef.current.delete(jobId);
            setJobSnapshots((current) => {
                const previous = current[jobId];
                if (previous && Number(previous.revision || 0) > Number(snapshot.revision || 0)) return current;
                return { ...current, [jobId]: preserveReadyArtifactUrls(previous, snapshot) };
            });
            return snapshot;
        } catch (error) {
            const status = Number(error.status);
            if (status === 429 || status >= 500) {
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
    }, [authenticatedFetch]);

    const subscribeToJobs = useCallback((socket, jobIds = activeJobIdsRef.current) => {
        if (socket?.readyState !== WebSocket.OPEN) return;
        jobIds.forEach((jobId) => socket.send(JSON.stringify({ action: 'subscribe', job_id: jobId })));
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
            subscribeToJobs(socket);
            console.log('CloudDSP WebSocket connected and subscribed to active jobs.');
        };
        socket.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                if (message.type === 'job_updated' && message.job_id) {
                    // Notifications are hints, but they should bypass a prior
                    // polling backoff so completed artifacts hydrate promptly.
                    fetchJobSnapshot(message.job_id, { showError: true, force: true });
                } else if (message.type === 'error') {
                    setErrorMsg(message.error || 'The CloudDSP WebSocket rejected a request.');
                }
            } catch (error) {
                console.warn('Ignoring invalid CloudDSP WebSocket message:', error);
            }
        };
        socket.onerror = () => console.warn('CloudDSP WebSocket transport error.');
        socket.onclose = () => {
            socketRef.current = null;
            if (!shouldReconnectRef.current) return;
            const delay = Math.min(1_000 * (2 ** reconnectAttemptsRef.current), RECONNECT_MAX_DELAY_MS);
            reconnectAttemptsRef.current += 1;
            reconnectTimerRef.current = window.setTimeout(() => connectWebSocket(), delay);
        };
    }, [authUsername, fetchJobSnapshot, subscribeToJobs]);

    useEffect(() => {
        shouldReconnectRef.current = Boolean(authUsername && WEBSOCKET_URL);
        if (shouldReconnectRef.current) connectWebSocket();
        return () => {
            shouldReconnectRef.current = false;
            window.clearTimeout(reconnectTimerRef.current);
            socketRef.current?.close();
            socketRef.current = null;
        };
    }, [authUsername, connectWebSocket]);

    useEffect(() => {
        subscribeToJobs(socketRef.current, activeJobIds);
    }, [activeJobIds, subscribeToJobs]);

    useEffect(() => {
        if (!authUsername || activeJobIds.length === 0) return undefined;
        const refreshPendingJobs = () => {
            activeJobIds
                .filter((jobId) => !jobSnapshotsRef.current[jobId] || isJobPending(jobSnapshotsRef.current[jobId]))
                .forEach((jobId) => fetchJobSnapshot(jobId));
        };
        refreshPendingJobs();
        const timer = window.setInterval(() => {
            refreshPendingJobs();
        }, POLL_INTERVAL_MS);
        return () => window.clearInterval(timer);
    }, [activeJobIds, authUsername, fetchJobSnapshot]);

    useEffect(() => {
        if (!currentJob) return;
        setStatusMessage(messageForJob(currentJob, 'Processing…'));
        if (currentJob.status === 'failed') setErrorMsg(currentJob.error || 'CloudDSP processing failed.');
    }, [currentJob]);

    const beginNewUpload = () => {
        setActiveJobId(null);
        setErrorMsg('');
        setStatusMessage('Ready to upload audio.');
    };

    const executeStemSplit = async () => {
        if (!stemFile) {
            setErrorMsg('Please select an audio file first.');
            return;
        }
        if (!authSession) {
            setErrorMsg('Sign in before uploading audio.');
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
                    stem_mode: splitMode,
                }),
            });
            const job = await response.json();
            if (!response.ok) throw new Error(job.error || `Could not create a job (${response.status}).`);
            if (!job.job_id || !job.upload_url || !job.upload_headers) {
                throw new Error('The job API returned an incomplete upload contract.');
            }

            setActiveJobIds((current) => [...new Set([...current, job.job_id])]);
            setActiveJobId(job.job_id);
            setJobSnapshots((current) => ({
                ...current,
                [job.job_id]: { job_id: job.job_id, status: job.status, revision: job.revision, stems: {}, midi: {} },
            }));
            subscribeToJobs(socketRef.current, [job.job_id]);

            setStatusMessage('Uploading audio to the secure job location…');
            const uploadResponse = await fetch(job.upload_url, {
                method: 'PUT',
                headers: job.upload_headers,
                body: stemFile,
            });
            if (!uploadResponse.ok) {
                throw new Error(`S3 upload failed (${uploadResponse.status}). Check the upload bucket CORS policy.`);
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

    const executeLinkExtraction = () => {
        setErrorMsg('Link ingestion is not part of the durable job API yet. Download the audio locally, then upload it here.');
    };

    const handleSignIn = async (email, password) => {
        const session = await signIn(email, password);
        setAuthSession(session);
        setErrorMsg('');
        setStatusMessage('Signed in. Select an audio file to begin.');
    };

    const handleSignOut = () => {
        signOut();
        setAuthSession(null);
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
        errorMsg,
        setErrorMsg,
        executeStemSplit,
        executeLinkExtraction,
        beginNewUpload,
    };

    const authProps = {
        configured: isCognitoConfigured,
        session: authSession,
        onSignIn: handleSignIn,
        onSignUp: signUp,
        onConfirmSignUp: confirmSignUp,
        onSignOut: handleSignOut,
    };

    return (
        <BrowserRouter>
            <div style={{ minHeight: '100vh' }}>
                <NavBar authProps={authProps} />
                {!authLoading && (!JOB_API_URL || !WEBSOCKET_URL) && (
                    <div style={{ margin: '-24px 20px 20px', color: '#f5c451', fontSize: '13px' }}>
                        Set VITE_JOB_API_URL and VITE_WEBSOCKET_URL before using the processing workspace.
                    </div>
                )}
                <Routes>
                    <Route path="/" element={<EqPage />} />
                    <Route path="/stems" element={<div style={{ display: 'flex', justifyContent: 'center' }}><StemSplitter {...stemProps} /></div>} />
                </Routes>
            </div>
        </BrowserRouter>
    );
}
