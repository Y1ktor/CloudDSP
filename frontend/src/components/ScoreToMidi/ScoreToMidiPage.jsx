/** Score and MIDI source workspace; score uploads now reach the local queue. */
import React, { useEffect, useMemo, useState } from 'react';
import ScoreMidiWorkspace from './ScoreMidiWorkspace';
import { uploadScore, validateScoreFile } from './scoreUpload';
import './ScoreToMidiPage.css';

const MIDI_EXTENSIONS = new Set(['mid', 'midi']);

function extensionOf(file) {
    return file?.name?.split('.').pop()?.toLowerCase() || '';
}

function readableSize(bytes) {
    return bytes < 1024 * 1024
        ? `${Math.max(1, Math.round(bytes / 1024))} KB`
        : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ScoreToMidiPage({ authenticated = false, authLoading = false, authProvider = 'your account', authenticatedFetch = null }) {
    const [direction, setDirection] = useState('sheet-to-midi');
    const [scoreFile, setScoreFile] = useState(null);
    const [sheetSourceMidiFile, setSheetSourceMidiFile] = useState(null);
    const [scoreError, setScoreError] = useState('');
    const [sheetSourceMidiError, setSheetSourceMidiError] = useState('');
    const [isDragging, setIsDragging] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [scoreUploadState, setScoreUploadState] = useState(null);
    const [scoreJob, setScoreJob] = useState(null);
    const [resultMidiFile, setResultMidiFile] = useState(null);
    const canStageSource = authenticated && !authLoading;
    const canUploadScore = canStageSource && Boolean(authenticatedFetch) && Boolean(scoreFile) && !isUploading;

    const scoreUrl = useMemo(() => scoreFile ? URL.createObjectURL(scoreFile) : null, [scoreFile]);
    useEffect(() => () => { if (scoreUrl) URL.revokeObjectURL(scoreUrl); }, [scoreUrl]);

    useEffect(() => {
        if (!scoreUploadState?.jobId || !authenticatedFetch) return undefined;
        let active = true;
        let timer;
        const poll = async () => {
            try {
                const response = await authenticatedFetch(`/score-jobs/${scoreUploadState.jobId}`);
                if (!response.ok) throw new Error(`Could not read score status (${response.status}).`);
                const job = await response.json();
                if (!active) return;
                setScoreJob(job);
                if (job.status === 'completed') {
                    const midiResponse = await fetch(job.midi_url);
                    if (!midiResponse.ok) throw new Error('Could not download the completed MIDI.');
                    const blob = await midiResponse.blob();
                    if (active) setResultMidiFile(new File([blob], `${scoreFile?.name || 'score'}.mid`, { type: 'audio/midi' }));
                    return;
                }
                if (job.status === 'failed') return;
            } catch (error) {
                if (active) setScoreError(error.message || 'Could not check score status.');
            }
            if (active) timer = window.setTimeout(poll, 3000);
        };
        poll();
        return () => { active = false; window.clearTimeout(timer); };
    }, [scoreUploadState?.jobId, authenticatedFetch, scoreFile?.name]);

    const chooseScore = (file) => {
        if (!canStageSource || !file) return;
        const validationError = validateScoreFile(file);
        if (validationError) {
            setScoreError(validationError);
            return;
        }
        setScoreFile(file);
        setScoreError('');
        setScoreUploadState(null);
        setScoreJob(null);
        setResultMidiFile(null);
    };

    const submitScore = async () => {
        if (!canUploadScore) return;
        setIsUploading(true);
        setScoreError('');
        setScoreUploadState(null);
        setScoreJob(null);
        setResultMidiFile(null);
        try {
            const uploaded = await uploadScore({ file: scoreFile, authenticatedFetch });
            setScoreUploadState(uploaded);
        } catch (error) {
            setScoreError(error.message || 'Could not upload the score. Please try again.');
        } finally {
            setIsUploading(false);
        }
    };

    const chooseSheetSourceMidi = (file) => {
        if (!canStageSource || !file) return;
        if (!MIDI_EXTENSIONS.has(extensionOf(file))) {
            setSheetSourceMidiError('Choose a .mid or .midi file.');
            return;
        }
        setSheetSourceMidiFile(file);
        setSheetSourceMidiError('');
    };

    const isImage = scoreFile && extensionOf(scoreFile) !== 'pdf';
    const isMidiToSheet = direction === 'midi-to-sheet';

    return (
        <main className="score-midi-page">
            <section className="score-midi-input-card" aria-label={isMidiToSheet ? 'MIDI source' : 'Sheet music source'}>
                <div className="score-midi-title-row">
                    <h1 className="score-midi-input-title">{isMidiToSheet ? 'MIDI to Sheet' : 'Score to MIDI'}</h1>
                    <button
                        className="score-midi-direction-button"
                        type="button"
                        onClick={() => { setDirection(isMidiToSheet ? 'sheet-to-midi' : 'midi-to-sheet'); setIsDragging(false); }}
                        aria-label={isMidiToSheet ? 'Switch to Score to MIDI' : 'Switch to MIDI to Sheet'}
                        title={isMidiToSheet ? 'Switch to Score to MIDI' : 'Switch to MIDI to Sheet'}
                    >
                        <span>{isMidiToSheet ? 'MIDI → Sheet' : 'Sheet → MIDI'}</span>
                        <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <path d="M3 6h13m0 0-3-3m3 3-3 3M17 14H4m0 0 3-3m-3 3 3 3" />
                        </svg>
                    </button>
                </div>
                {isMidiToSheet ? (
                    <>
                        <div className="score-midi-upload-bar">
                            <label
                                className={`score-midi-primary-button ${canStageSource ? '' : 'score-midi-primary-button-disabled'}`}
                                htmlFor={canStageSource ? 'sheet-midi-upload' : undefined}
                                aria-disabled={!canStageSource}
                                title={canStageSource ? 'Choose a MIDI file' : `Sign in with ${authProvider} to choose a MIDI file`}
                            >Browse MIDI</label>
                            <input
                                id="sheet-midi-upload"
                                className="score-midi-file-input"
                                type="file"
                                accept=".mid,.midi,audio/midi,audio/x-midi"
                                disabled={!canStageSource}
                                onChange={(event) => {
                                    chooseSheetSourceMidi(event.target.files?.[0]);
                                    event.target.value = '';
                                }}
                            />
                            <div className="score-midi-file-name" title={sheetSourceMidiFile?.name || 'No MIDI selected'}>
                                {sheetSourceMidiFile?.name || 'No MIDI selected'}
                            </div>
                            <button className="score-midi-transcribe-button" type="button" disabled title="Sheet rendering is not connected yet">
                                Render sheet
                            </button>
                        </div>
                        {sheetSourceMidiError && <p className="score-midi-error" role="alert">{sheetSourceMidiError}</p>}
                        <p className="score-midi-availability">MIDI: MID or MIDI.</p>

                        <div className="score-midi-input-grid">
                            <div
                                className={`score-midi-preview ${isDragging ? 'score-midi-preview-dragging' : ''}`}
                                onDragOver={(event) => { event.preventDefault(); if (canStageSource) setIsDragging(true); }}
                                onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setIsDragging(false); }}
                                onDrop={(event) => {
                                    event.preventDefault();
                                    setIsDragging(false);
                                    if (canStageSource) chooseSheetSourceMidi(event.dataTransfer.files?.[0]);
                                }}
                            >
                                <div className="score-midi-preview-empty">
                                    <span className="score-midi-score-glyph" aria-hidden="true">♫</span>
                                    <strong>{sheetSourceMidiFile ? sheetSourceMidiFile.name : 'Your MIDI source appears here'}</strong>
                                    <span>{sheetSourceMidiFile
                                        ? `${readableSize(sheetSourceMidiFile.size)} · Ready for sheet rendering`
                                        : 'Choose a MIDI file above or drop one in this area.'}</span>
                                </div>
                            </div>
                            <div className="score-midi-source-side">
                                <div className="score-midi-source-caption">SOURCE DETAILS</div>
                                <h3>{sheetSourceMidiFile ? sheetSourceMidiFile.name : 'Waiting for MIDI'}</h3>
                                <p>{sheetSourceMidiFile
                                    ? `${extensionOf(sheetSourceMidiFile).toUpperCase()} · ${readableSize(sheetSourceMidiFile.size)}`
                                    : 'Upload a MIDI file to stage it for notation rendering.'}</p>
                                <div className="score-midi-flow-list">
                                    <div><span className={sheetSourceMidiFile ? 'done' : ''}>1</span><div><strong>Choose MIDI</strong><small>MID or MIDI</small></div></div>
                                    <div><span>2</span><div><strong>Render notation</strong><small>Sheet rendering service pending</small></div></div>
                                    <div><span>3</span><div><strong>Review sheet</strong><small>Preview and download PDF</small></div></div>
                                </div>
                            </div>
                        </div>
                    </>
                ) : (
                    <>
                        <div className="score-midi-upload-bar">
                            <label
                                className={`score-midi-primary-button ${canStageSource ? '' : 'score-midi-primary-button-disabled'}`}
                                htmlFor={canStageSource ? 'score-midi-upload' : undefined}
                                aria-disabled={!canStageSource}
                                title={canStageSource ? 'Choose a score' : `Sign in with ${authProvider} to choose a score`}
                            >Browse score</label>
                            <input
                                id="score-midi-upload"
                                className="score-midi-file-input"
                                type="file"
                                accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                                disabled={!canStageSource}
                                onChange={(event) => {
                                    chooseScore(event.target.files?.[0]);
                                    event.target.value = '';
                                }}
                            />
                            <div className="score-midi-file-name" title={scoreFile?.name || 'No score selected'}>
                                {scoreFile?.name || 'No score selected'}
                            </div>
                            <button className="score-midi-transcribe-button" type="button" disabled={!canUploadScore} onClick={submitScore} title={authenticatedFetch ? 'Upload the score to the transcription queue' : 'Score uploads are available in the local workspace'}>
                                {isUploading ? 'Uploading…' : 'Queue score'}
                            </button>
                        </div>
                        {scoreError && <p className="score-midi-error" role="alert">{scoreError}</p>}
                        {scoreUploadState && <p className="score-midi-upload-status" role="status">{scoreJob?.status === 'completed' ? 'Transcription complete.' : scoreJob?.status === 'failed' ? `Transcription failed: ${scoreJob.error || 'The score could not be recognized.'}` : scoreJob?.status === 'processing' ? 'Transcribing your score…' : 'Score queued for transcription.'} Job {scoreUploadState.jobId}.</p>}
                        <p className="score-midi-availability">Sheet music: PDF, PNG, JPG, or JPEG · 25 MiB maximum.</p>

                        <div className="score-midi-input-grid">
                            <div
                                className={`score-midi-preview ${isDragging ? 'score-midi-preview-dragging' : ''}`}
                                onDragOver={(event) => { event.preventDefault(); if (canStageSource) setIsDragging(true); }}
                                onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setIsDragging(false); }}
                                onDrop={(event) => {
                                    event.preventDefault();
                                    setIsDragging(false);
                                    if (canStageSource) chooseScore(event.dataTransfer.files?.[0]);
                                }}
                            >
                                {isImage ? (
                                    <img src={scoreUrl} alt={`Preview of ${scoreFile.name}`} />
                                ) : scoreFile ? (
                                    <div className="score-midi-pdf-preview">
                                        <span className="score-midi-document-icon" aria-hidden="true">PDF</span>
                                        <strong>{scoreFile.name}</strong>
                                        <span>{readableSize(scoreFile.size)} · PDF score</span>
                                        <a href={scoreUrl} target="_blank" rel="noopener noreferrer">Open PDF preview ↗</a>
                                    </div>
                                ) : (
                                    <div className="score-midi-preview-empty">
                                        <span className="score-midi-score-glyph" aria-hidden="true">♪</span>
                                        <strong>Your sheet preview appears here</strong>
                                        <span>Choose a file above or drop one in this area.</span>
                                    </div>
                                )}
                            </div>
                            <div className="score-midi-source-side">
                                <div className="score-midi-source-caption">SOURCE DETAILS</div>
                                <h3>{scoreFile ? scoreFile.name : 'Waiting for a score'}</h3>
                                <p>{scoreFile
                                    ? `${extensionOf(scoreFile).toUpperCase()} · ${readableSize(scoreFile.size)}`
                                    : 'Upload a printed score to stage it for transcription.'}</p>
                                <div className="score-midi-flow-list">
                                    <div><span className={scoreFile ? 'done' : ''}>1</span><div><strong>Choose a score</strong><small>PDF, PNG, JPG, or JPEG</small></div></div>
                                    <div><span className={scoreUploadState ? 'done' : ''}>2</span><div><strong>Queue transcription</strong><small>{scoreUploadState ? 'Source uploaded to the queue' : 'Upload to start a score job'}</small></div></div>
                                    <div><span>3</span><div><strong>Review MIDI</strong><small>Play, edit, and download notes</small></div></div>
                                </div>
                            </div>
                        </div>
                    </>
                )}
            </section>
            {!isMidiToSheet && resultMidiFile && scoreJob?.midi_url && (
                <ScoreMidiWorkspace midiFile={resultMidiFile} downloadUrl={scoreJob.midi_url} sessionKey={scoreUploadState.jobId} />
            )}
        </main>
    );
}
