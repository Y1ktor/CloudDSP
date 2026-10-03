import { useCallback, useEffect } from 'react';

/** Account-backed library reads/deletion; no browser-local job history. */
export function useJobHistory({
    authUsername, authenticatedFetch, updateAccountQuota,
    deletedJobIdsRef, jobRefreshBackoffRef, activeJobIdRef,
    setPreviousJobs, setPreviousJobsError, setIsPreviousJobsLoading,
    setIsPreviousJobsOpen, setDeletingJobId, setJobSnapshots, setActiveJobId,
    setStemFile, setStemFileName, setIsRestoringHistoryJob, setIsHistoryJob,
    setErrorMsg, setStatusMessage,
}) {
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
            updateAccountQuota(payload);
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
    }, [authUsername, authenticatedFetch, updateAccountQuota, deletedJobIdsRef, setPreviousJobs, setPreviousJobsError, setIsPreviousJobsLoading]);

    useEffect(() => {
        fetchPreviousJobs();
    }, [fetchPreviousJobs]);

    const openPreviousJobs = useCallback(() => {
        setIsPreviousJobsOpen(true);
        void fetchPreviousJobs();
    }, [fetchPreviousJobs, setIsPreviousJobsOpen]);

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
    }, [authenticatedFetch, deletedJobIdsRef, jobRefreshBackoffRef, activeJobIdRef,
        setPreviousJobs, setPreviousJobsError, setDeletingJobId, setJobSnapshots,
        setActiveJobId, setStemFile, setStemFileName, setIsRestoringHistoryJob,
        setIsHistoryJob, setErrorMsg, setStatusMessage]);

    return { fetchPreviousJobs, openPreviousJobs, deletePreviousJob };
}
