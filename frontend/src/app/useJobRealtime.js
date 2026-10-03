import { useCallback, useEffect, useRef } from 'react';
import { auth } from '@platform/auth';
import { needsJobRefresh } from './jobSnapshots';
import {
    WEBSOCKET_URL, WEBSOCKET_HEARTBEAT_INTERVAL_MS, RECONNECT_MAX_DELAY_MS,
    POLL_INTERVAL_MS,
} from './config';

/** Socket notifications are hints; polling remains the durable recovery path. */
export function useJobRealtime({
    authUsername, activeJobId, activeJobIdRef, jobSnapshotsRef,
    fetchJobSnapshot, setAuthSession, setErrorMsg,
}) {
    const socketRef = useRef(null);
    const shouldReconnectRef = useRef(false);
    const reconnectTimerRef = useRef(null);
    const reconnectAttemptsRef = useRef(0);
    const heartbeatTimerRef = useRef(null);

    const subscribeToActiveJob = useCallback((socket, jobId = activeJobIdRef.current) => {
        if (!jobId) return;
        if (jobSnapshotsRef.current[jobId]?.is_demo || String(jobId).startsWith('demo:')) return;
        if (socket?.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({ action: 'subscribe', job_id: jobId }));
    }, [activeJobIdRef, jobSnapshotsRef]);

    const connectWebSocket = useCallback(async () => {
        if (!WEBSOCKET_URL || !authUsername) return;
        const existingSocket = socketRef.current;
        if (existingSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(existingSocket.readyState)) return;

        let session;
        let socket;
        try {
            session = await auth.getCurrentSession();
            if (!session) {
                setErrorMsg('Your session has expired. Sign in again to reconnect.');
                return;
            }
            setAuthSession(session);
            const socketUrl = new URL(WEBSOCKET_URL);
            socketUrl.searchParams.set('token', auth.getBearerToken(session));
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
    }, [authUsername, fetchJobSnapshot, subscribeToActiveJob, activeJobIdRef, setAuthSession, setErrorMsg]);

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
        if (jobSnapshotsRef.current[activeJobId]?.is_demo || String(activeJobId).startsWith('demo:')) {
            return undefined;
        }
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
    }, [activeJobId, authUsername, fetchJobSnapshot, jobSnapshotsRef]);

    return { socketRef, subscribeToActiveJob };
}
