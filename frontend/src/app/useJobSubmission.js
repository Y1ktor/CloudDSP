/** Submit durable jobs from audio files or media links and perform constrained POST uploads. */
import { profile } from '@platform/profile';
import { quotaErrorMessage } from './quotaMessages';
import { MAX_SOURCE_UPLOAD_BYTES } from './config';

export function useJobSubmission({
    stemFile, splitMode, authSession, authenticatedFetch, updateAccountQuota,
    fetchJobSnapshot, subscribeToActiveJob, socketRef, beginNewUpload,
    setIsUploading, setErrorMsg, setStatusMessage, setActiveJobId,
    setJobSnapshots, setPreviousJobs, setStemFile, setStemFileName,
}) {
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
            const job = await response.json().catch(() => ({}));
            updateAccountQuota(job);
            if (!response.ok) {
                job.statusCode = response.status;
                throw new Error(quotaErrorMessage(job, 'direct_uploads', `Could not create a job (${response.status}).`));
            }
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

            setStatusMessage(profile.messages.uploading);
            const uploadForm = new FormData();
            Object.entries(job.upload_fields).forEach(([name, value]) => uploadForm.append(name, value));
            uploadForm.append('file', stemFile);
            const uploadResponse = await fetch(job.upload_url, {
                method: 'POST',
                body: uploadForm,
            });
            if (!uploadResponse.ok) {
                throw new Error(profile.messages.uploadFailed(uploadResponse.status));
            }
            setStatusMessage(profile.messages.uploadPending);
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
            const job = await response.json().catch(() => ({}));
            updateAccountQuota(job);
            if (!response.ok) {
                job.statusCode = response.status;
                throw new Error(quotaErrorMessage(job, 'ytdlp', `Could not create a linked-source job (${response.status}).`));
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

    return { executeStemSplit, executeLinkExtraction };
}
