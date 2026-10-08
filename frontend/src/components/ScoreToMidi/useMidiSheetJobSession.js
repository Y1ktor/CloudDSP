/** Keep one score workspace in memory across tabs; PostgreSQL owns history. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { uploadSheetMidi as uploadScore, validateSheetMidiFile as validateScoreFile } from './sheetUpload';

export function useMidiSheetJobSession(authenticatedFetch) {
    const [scoreFile, setScoreFile] = useState(null);
    const [scoreError, setScoreError] = useState('');
    const [isUploading, setIsUploading] = useState(false);
    const [scoreUploadState, setScoreUploadState] = useState(null);
    const [scoreJob, setScoreJob] = useState(null);
    const [editorKey, setEditorKey] = useState(0);
    const editorKeyRef = useRef(0);
    const selectionSequence = useRef(0);
    const mountedRef = useRef(true);
    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; };
    }, []);

    useEffect(() => {
        if (!scoreUploadState?.jobId || !authenticatedFetch) return undefined;
        const controller = new AbortController();
        const { signal } = controller;
        const isCurrent = () => !signal.aborted && scoreUploadState.selectionId === selectionSequence.current;
        let timer;
        let sourceRequested = Boolean(scoreUploadState.localFile);
        const filename = scoreUploadState.sourceFilename || 'score';

        const restoreSource = async (job) => {
            if (sourceRequested || !job.source_url) return;
            sourceRequested = true;
            try {
                const response = await fetch(job.source_url, { signal });
                if (!response.ok) throw new Error('Could not reopen the submitted MIDI.');
                const blob = await response.blob();
                if (isCurrent()) setScoreFile(new File([blob], job.source_filename || filename, {
                    type: job.source_content_type || blob.type,
                }));
            } catch (failure) {
                if (isCurrent()) {
                    sourceRequested = false;
                    setScoreError(failure.message || 'Could not reopen the submitted MIDI.');
                }
            }
        };

        const poll = async () => {
            try {
                const response = await authenticatedFetch(`/sheet-jobs/${encodeURIComponent(scoreUploadState.jobId)}`, { signal });
                if (!isCurrent()) return;
                if ([401, 403, 404, 410].includes(response.status)) {
                    setScoreError(response.status === 404 || response.status === 410
                        ? 'This MIDI-to-sheet job is no longer available.' : 'Sign in again to reopen this sheet job.');
                    return;
                }
                if (!response.ok) throw new Error(`Could not read sheet status (${response.status}).`);
                const job = await response.json();
                if (!isCurrent()) return;
                setScoreError('');
                setScoreJob(job);
                // A slow PDF preview must not block completed sheet playback.
                void restoreSource(job);
                if (job.status === 'completed') {
                    return;
                }
                if (job.status === 'failed') return;
            } catch (failure) {
                if (isCurrent()) setScoreError(failure.message || 'Could not check sheet status.');
            }
            if (isCurrent()) timer = window.setTimeout(poll, 3000);
        };
        void poll();
        return () => { controller.abort(); window.clearTimeout(timer); };
    }, [scoreUploadState, authenticatedFetch]);

    const clearResult = useCallback(() => {
        setScoreError('');
        setScoreUploadState(null);
        setScoreJob(null);

    }, []);

    const chooseScore = useCallback((file) => {
        if (!file || isUploading) return;
        const error = validateScoreFile(file);
        if (error) { setScoreError(error); return; }
        selectionSequence.current += 1;
        clearResult();
        setScoreFile(file);
        setEditorKey(++editorKeyRef.current);
    }, [clearResult, isUploading]);

    const openSavedJob = useCallback((job) => {
        if (!job?.job_id || isUploading) return;
        clearResult();
        setScoreFile(null);
        setEditorKey(++editorKeyRef.current);
        // Opening history only GETs the durable snapshot. A new selection token
        // reloads fresh signatures even when reopening the same job twice.
        setScoreUploadState({ jobId: job.job_id, sourceFilename: job.source_filename,
            selectionId: ++selectionSequence.current, fromHistory: true });
    }, [clearResult, isUploading]);

    const submitScore = useCallback(async (exportMidi) => {
        if (!scoreFile || !authenticatedFetch || isUploading) return;
        const selectionId = ++selectionSequence.current;
        clearResult();
        setIsUploading(true);
        try {
            // Export is invoked only here, after all current editor changes.
            const editedFile = exportMidi();
            const uploaded = await uploadScore({ file: editedFile, authenticatedFetch });
            if (mountedRef.current && selectionId === selectionSequence.current) {
                // The submitted version also becomes the current source, so
                // switching tabs cannot pair an older MIDI with this result.
                setScoreFile(editedFile);
                setEditorKey(++editorKeyRef.current);
                setScoreUploadState({ ...uploaded, sourceFilename: editedFile.name, localFile: editedFile, selectionId });
            }
        } catch (failure) {
            if (mountedRef.current) setScoreError(failure.message || 'Could not upload the edited MIDI. Please try again.');
        } finally {
            if (mountedRef.current) setIsUploading(false);
        }
    }, [authenticatedFetch, clearResult, isUploading, scoreFile]);

    const retainMidi = useCallback((file, key) => {
        if (mountedRef.current && key === editorKeyRef.current) setScoreFile(file);
    }, []);

    return { scoreFile, scoreError, isUploading, scoreUploadState, scoreJob,
        editorKey, retainMidi, chooseScore, submitScore, openSavedJob };
}
