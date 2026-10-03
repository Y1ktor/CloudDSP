import { useCallback } from 'react';
import { profile } from '@platform/profile';
import { readyArtifactNames, preserveReadyArtifactUrls } from './jobSnapshots';
import { JOB_REFRESH_BACKOFF_INITIAL_MS, JOB_REFRESH_BACKOFF_MAX_MS } from './config';

/** De-duplicate snapshot requests and retain revision, URL, and retry guards. */
export function useJobSnapshots({
    authenticatedFetch, fetchPreviousJobs, jobRefreshInFlightRef,
    jobRefreshBackoffRef, setJobSnapshots, setPreviousJobs, setActiveJobId, setErrorMsg,
}) {
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
                + `${profile.messages.artifactStemLabel}: ${stemNames.length ? stemNames.join(', ') : 'none'}. `
                + `${profile.messages.artifactMidiLabel}: ${midiNames.length ? midiNames.join(', ') : 'none'}.`,
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
    }, [authenticatedFetch, fetchPreviousJobs, jobRefreshInFlightRef,
        jobRefreshBackoffRef, setJobSnapshots, setPreviousJobs, setActiveJobId, setErrorMsg]);

    return fetchJobSnapshot;
}
