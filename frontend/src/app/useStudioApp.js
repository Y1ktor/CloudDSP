/**
 * One owner for tab-local workspace/account state. Behavior hooks receive stable
 * setters and refs; they do not duplicate active jobs or persist a job queue.
 * Synchronize current-job refs before realtime effects consume them.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { auth } from '@platform/auth';
import { profile } from '@platform/profile';
import { createDemoJobSnapshot } from '../utils/demoCatalog';
import { isJobPending, messageForJob, urlsForReadyArtifacts, sourceUrlForJob } from './jobSnapshots';
import { readPendingSignUp, useAppAuthentication } from './useAppAuthentication';
import { useDemoCatalog } from './useDemoCatalog';
import { useJobHistory } from './useJobHistory';
import { useJobSnapshots } from './useJobSnapshots';
import { useJobRealtime } from './useJobRealtime';
import { useJobSubmission } from './useJobSubmission';

export function useStudioApp() {
    const [stemFile, setStemFile] = useState(null);
    const [stemFileName, setStemFileName] = useState('No file loaded');
    const [splitMode, setSplitMode] = useState('6-stems');
    const [statusMessage, setStatusMessage] = useState('Sign in and upload an audio file to begin.');
    const [errorMsg, setErrorMsg] = useState('');
    const [authSession, setAuthSession] = useState(null);
    const [authLoading, setAuthLoading] = useState(auth.isConfigured);
    const [pendingSignUp, setPendingSignUp] = useState(readPendingSignUp);
    const [accountQuota, setAccountQuota] = useState(null);
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
    const [demoCatalog, setDemoCatalog] = useState({ jobs: [], defaultJobId: null });
    const [isDemoLibraryOpen, setIsDemoLibraryOpen] = useState(false);
    const [isAuthDialogOpen, setIsAuthDialogOpen] = useState(false);

    const activeJobIdRef = useRef(null);
    const jobSnapshotsRef = useRef({});
    const jobRefreshInFlightRef = useRef(new Set());
    const jobRefreshBackoffRef = useRef(new Map());
    const deletedJobIdsRef = useRef(new Set());

    const currentJob = activeJobId ? jobSnapshots[activeJobId] : null;
    const activeDemoId = currentJob?.is_demo ? currentJob.demo_id : null;
    const authUsername = authSession?.username;
    const stemUrls = useMemo(() => urlsForReadyArtifacts(currentJob?.stems), [currentJob]);
    const midiUrls = useMemo(() => urlsForReadyArtifacts(currentJob?.midi), [currentJob]);
    const midiStates = currentJob?.midi || {};
    // A saved job has already been submitted. While its snapshot and private
    // artifacts are being restored, do not describe the wait as new separation or
    // MIDI processing work.
    const isSplitting = isUploading || (!isRestoringHistoryJob && isJobPending(currentJob));
    useEffect(() => {
        activeJobIdRef.current = activeJobId;
    }, [activeJobId]);

    useEffect(() => {
        jobSnapshotsRef.current = jobSnapshots;
    }, [jobSnapshots]);

    const { authenticatedFetch, handleSignIn, handleSignUp, handleConfirmSignUp, handleSignOut } = useAppAuthentication({
        setAuthSession, setAuthLoading, setPendingSignUp, setStatusMessage, setErrorMsg,
        setAccountQuota, setIsPreviousJobsOpen, setIsRestoringHistoryJob, setIsHistoryJob,
    });
    useDemoCatalog(setDemoCatalog);

    // The account-scoped Job API is the only source of job history.
    // Do not restore a browser-local job queue after a reload: deleted jobs
    // would otherwise be polled forever and bypass the library's ownership
    // filtering. The currently open job remains React state for this tab only.
    useEffect(() => {
        if (authUsername) {
            // One-time migration cleanup for frontend versions that persisted
            // active jobs before the account-backed job library existed.
            sessionStorage.removeItem(`clouddsp.activeJobs.${authUsername}`);
        } else {
            setAccountQuota(null);
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

    const updateAccountQuota = useCallback((payload) => {
        if (payload?.quota && typeof payload.quota === 'object') {
            setAccountQuota(payload.quota);
        }
    }, []);

    const { fetchPreviousJobs, openPreviousJobs, deletePreviousJob } = useJobHistory({
        authUsername, authenticatedFetch, updateAccountQuota,
        deletedJobIdsRef, jobRefreshBackoffRef, activeJobIdRef,
        setPreviousJobs, setPreviousJobsError, setIsPreviousJobsLoading,
        setIsPreviousJobsOpen, setDeletingJobId, setJobSnapshots, setActiveJobId,
        setStemFile, setStemFileName, setIsRestoringHistoryJob, setIsHistoryJob,
        setErrorMsg, setStatusMessage,
    });
    const fetchJobSnapshot = useJobSnapshots({
        authenticatedFetch, fetchPreviousJobs, jobRefreshInFlightRef,
        jobRefreshBackoffRef, setJobSnapshots, setPreviousJobs, setActiveJobId, setErrorMsg,
    });
    const { socketRef, subscribeToActiveJob } = useJobRealtime({
        authUsername, activeJobId, activeJobIdRef, jobSnapshotsRef,
        fetchJobSnapshot, setAuthSession, setErrorMsg,
    });

    useEffect(() => {
        if (!currentJob) return;
        if (isRestoringHistoryJob) {
            setStatusMessage('Stems and MIDI will arrive shortly.');
            return;
        }
        setStatusMessage(messageForJob(currentJob, 'Processing…', profile.messages));
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

    const openDemoJob = useCallback((demo) => {
        const snapshot = createDemoJobSnapshot(demo);
        if (!snapshot) return;

        setStemFile(null);
        setStemFileName(demo.sourceFilename || demo.title);
        setActiveJobId(snapshot.job_id);
        setJobSnapshots({ [snapshot.job_id]: snapshot });
        setIsRestoringHistoryJob(false);
        setIsHistoryJob(false);
        setIsDemoLibraryOpen(false);
        setErrorMsg('');
        setStatusMessage('Public demo loaded. Playback and MIDI edits stay in this browser.');
    }, []);

    useEffect(() => {
        if (authLoading || authSession || currentJob || demoCatalog.jobs.length === 0) return;
        const defaultDemo = demoCatalog.jobs.find((job) => job.id === demoCatalog.defaultJobId)
            || demoCatalog.jobs[0];
        openDemoJob(defaultDemo);
    }, [authLoading, authSession, currentJob, demoCatalog, openDemoJob]);

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

    const { executeStemSplit, executeLinkExtraction } = useJobSubmission({
        stemFile, splitMode, authSession, authenticatedFetch, updateAccountQuota,
        fetchJobSnapshot, subscribeToActiveJob, socketRef, beginNewUpload,
        setIsUploading, setErrorMsg, setStatusMessage, setActiveJobId,
        setJobSnapshots, setPreviousJobs, setStemFile, setStemFileName,
    });

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
        sourceUrl: sourceUrlForJob(currentJob),
        isRestoringHistoryJob,
        isHistoryJob,
        isDemo: Boolean(currentJob?.is_demo),
        canProcess: Boolean(authSession),
        onOpenExamples: demoCatalog.jobs.length > 0 ? () => setIsDemoLibraryOpen(true) : null,
        errorMsg,
        setErrorMsg,
        executeStemSplit,
        executeLinkExtraction,
        beginNewUpload,
    };

    const authProps = {
        configured: auth.isConfigured,
        session: authSession,
        quota: accountQuota,
        pendingVerification: pendingSignUp,
        onSignIn: handleSignIn,
        onSignUp: handleSignUp,
        onConfirmSignUp: handleConfirmSignUp,
        onSignOut: handleSignOut,
        onOpenHistory: openPreviousJobs,
        onDialogOpenChange: setIsAuthDialogOpen,
    };

    return {
        stemProps, authProps, authenticatedFetch, authLoading, activeJobId, activeDemoId,
        demoCatalog, isDemoLibraryOpen, setIsDemoLibraryOpen, openDemoJob,
        isAuthDialogOpen, isPreviousJobsOpen, setIsPreviousJobsOpen,
        previousJobs, isPreviousJobsLoading, previousJobsError, deletingJobId,
        selectPreviousJob, fetchPreviousJobs, deletePreviousJob,
    };
}
