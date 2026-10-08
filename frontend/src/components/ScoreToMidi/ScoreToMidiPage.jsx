/** Score and MIDI source workspace; score uploads now reach the local queue. */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import ScoreMidiWorkspace from './ScoreMidiWorkspace';
import './ScoreToMidiPage.css';

function extensionOf(file) {
    return file?.name?.split('.').pop()?.toLowerCase() || '';
}

function readableSize(bytes) {
    return bytes < 1024 * 1024
        ? `${Math.max(1, Math.round(bytes / 1024))} KB`
        : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ScoreToMidiPage({ authenticated = false, authLoading = false, authProvider = 'your account', authenticatedFetch = null, scoreSession, sheetSession, direction, setDirection }) {
    const midiEditorRef = useRef(null);
    const { scoreFile: sheetSourceMidiFile, scoreError: sheetSourceMidiError,
        isUploading: sheetUploading, scoreUploadState: sheetUpload, scoreJob: sheetJob,
        editorKey, retainMidi, chooseScore: chooseSheetSourceMidi, submitScore: submitMidi } = sheetSession;
    const [isDragging, setIsDragging] = useState(false);
    const { scoreFile, scoreError, isUploading, scoreUploadState, scoreJob,
        resultMidiFile, chooseScore, submitScore } = scoreSession;
    const canStageSource = authenticated && !authLoading && !isUploading && !sheetUploading;
    const canUploadScore = canStageSource && Boolean(authenticatedFetch) && Boolean(scoreFile);
    const sourceFilename = scoreFile?.name || scoreJob?.source_filename || scoreUploadState?.sourceFilename;

    const scoreUrl = useMemo(() => scoreFile ? URL.createObjectURL(scoreFile) : null, [scoreFile]);
    useEffect(() => () => { if (scoreUrl) URL.revokeObjectURL(scoreUrl); }, [scoreUrl]);
    useEffect(() => {
        if (scoreUploadState?.fromHistory) setDirection('sheet-to-midi');
    }, [scoreUploadState, setDirection]);

    const isImage = scoreFile && extensionOf(scoreFile) !== 'pdf';
    const isMidiToSheet = direction === 'midi-to-sheet';
    const sourceUnavailable = scoreError || (scoreJob && !scoreJob.source_uploaded);
    const scoreStatus = scoreJob?.status === 'completed' ? 'Transcription complete.'
        : scoreJob?.status === 'failed' ? `Transcription failed: ${scoreJob.error || 'The score could not be recognized.'}`
            : scoreJob?.status === 'processing' ? 'Transcribing your score…'
                : scoreUploadState?.fromHistory && !scoreJob ? 'Opening saved score…'
                    : scoreUploadState?.fromHistory && !scoreJob?.source_uploaded ? 'Waiting for the original score upload.'
                        : 'Score queued for transcription.';

    return (
        <main className="score-midi-page">
            <section className="score-midi-input-card" aria-label={isMidiToSheet ? 'MIDI source' : 'Sheet music source'}>
                <div className="score-midi-title-row">
                    <h1 className="score-midi-input-title">{isMidiToSheet ? 'MIDI to Sheet' : 'Score to MIDI'}</h1>
                    <button
                        className="score-midi-direction-button"
                        type="button"
                        disabled={isUploading || sheetUploading}
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
                            <button className="score-midi-transcribe-button" type="button"
                                disabled={!canStageSource || !authenticatedFetch || !sheetSourceMidiFile}
                                onClick={() => submitMidi(() => midiEditorRef.current?.exportMidi())}>
                                {sheetUploading ? 'Uploading…' : 'Queue sheet'}
                            </button>
                        </div>
                        {sheetSourceMidiError && <p className="score-midi-error" role="alert">{sheetSourceMidiError}</p>}
                        <p className="score-midi-availability">MIDI: MID or MIDI · 10 MiB maximum. Files stay in your browser until you queue the sheet.</p>
                        {sheetUpload && <p className="score-midi-upload-status" role="status">
                            {sheetJob?.status === 'completed' ? 'Sheet ready.' : sheetJob?.status === 'failed'
                                ? `Rendering failed: ${sheetJob.error || 'Could not render this MIDI.'}`
                                : sheetJob?.status === 'processing' ? 'Rendering your sheet…' : 'Sheet queued for rendering.'}
                            {' '}Job {sheetUpload.jobId}.
                        </p>}

                        {sheetSourceMidiFile ? <ScoreMidiWorkspace ref={midiEditorRef}
                            key={editorKey} midiFile={sheetSourceMidiFile} sessionKey={`sheet-${editorKey}`} sourceEditor
                            onRetainSnapshot={(file) => retainMidi(file, editorKey)} />
                            : <div className={`score-midi-preview ${isDragging ? 'score-midi-preview-dragging' : ''}`}
                                onDragOver={(event) => { event.preventDefault(); if (canStageSource) setIsDragging(true); }}
                                onDragLeave={() => setIsDragging(false)}
                                onDrop={(event) => { event.preventDefault(); setIsDragging(false);
                                    if (canStageSource) chooseSheetSourceMidi(event.dataTransfer.files?.[0]); }}>
                                <div className="score-midi-preview-empty"><span className="score-midi-score-glyph" aria-hidden="true">♫</span>
                                    <strong>Your MIDI editor appears here</strong><span>Choose a MIDI file above or drop one in this area.</span></div>
                            </div>}
                        {sheetJob?.status === 'completed' && <div className="score-midi-result-heading">
                            <h2>Your sheet is ready</h2>
                            <div className="score-midi-result-meta">
                                <a className="score-midi-secondary-button" href={sheetJob.pdf_url} target="_blank" rel="noopener noreferrer">Open PDF ↗</a>
                                <a className="score-midi-secondary-button" href={sheetJob.musicxml_url} download>Download MusicXML</a>
                            </div>
                        </div>}
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
                            <div className="score-midi-file-name" title={sourceFilename || 'No score selected'}>
                                {sourceFilename || 'No score selected'}
                            </div>
                            <button className="score-midi-transcribe-button" type="button" disabled={!canUploadScore} onClick={submitScore} title={authenticatedFetch ? 'Upload the score to the transcription queue' : 'Score uploads are available in the local workspace'}>
                                {isUploading ? 'Uploading…' : 'Queue score'}
                            </button>
                        </div>
                        {scoreError && <p className="score-midi-error" role="alert">{scoreError}</p>}
                        {scoreUploadState && <p className="score-midi-upload-status" role="status">{scoreStatus} Job {scoreUploadState.jobId}.</p>}
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
                                        <strong>{sourceFilename ? (sourceUnavailable ? 'Score preview unavailable' : 'Restoring your score preview…') : 'Your sheet preview appears here'}</strong>
                                        <span>{sourceFilename ? sourceFilename : 'Choose a file above or drop one in this area.'}</span>
                                    </div>
                                )}
                            </div>
                            <div className="score-midi-source-side">
                                <div className="score-midi-source-caption">SOURCE DETAILS</div>
                                <h3>{sourceFilename || 'Waiting for a score'}</h3>
                                <p>{scoreFile
                                    ? `${extensionOf(scoreFile).toUpperCase()} · ${readableSize(scoreFile.size)}`
                                    : sourceFilename ? 'Saved score. Its source preview appears when available.' : 'Upload a printed score to stage it for transcription.'}</p>
                                <div className="score-midi-flow-list">
                                    <div><span className={sourceFilename ? 'done' : ''}>1</span><div><strong>Choose a score</strong><small>PDF, PNG, JPG, or JPEG</small></div></div>
                                    <div><span className={scoreUploadState ? 'done' : ''}>2</span><div><strong>Queue transcription</strong><small>{scoreUploadState ? (scoreUploadState.fromHistory ? 'Saved score job' : 'Source uploaded to the queue') : 'Upload to start a score job'}</small></div></div>
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
