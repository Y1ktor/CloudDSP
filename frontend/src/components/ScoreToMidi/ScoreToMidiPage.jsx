/** Score upload and result-review surface; transcription service is a later integration. */
import React, { useEffect, useMemo, useState } from 'react';
import ScoreMidiWorkspace from './ScoreMidiWorkspace';
import './ScoreToMidiPage.css';

const SCORE_EXTENSIONS = new Set(['pdf', 'png', 'jpg', 'jpeg']);
const MIDI_EXTENSIONS = new Set(['mid', 'midi']);

function extensionOf(file) {
    return file?.name?.split('.').pop()?.toLowerCase() || '';
}

function readableSize(bytes) {
    return bytes < 1024 * 1024
        ? `${Math.max(1, Math.round(bytes / 1024))} KB`
        : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ScoreToMidiPage() {
    const [scoreFile, setScoreFile] = useState(null);
    const [midiFile, setMidiFile] = useState(null);
    const [resultVersion, setResultVersion] = useState(0);
    const [scoreError, setScoreError] = useState('');
    const [midiError, setMidiError] = useState('');
    const [isDragging, setIsDragging] = useState(false);

    const scoreUrl = useMemo(() => scoreFile ? URL.createObjectURL(scoreFile) : null, [scoreFile]);
    const midiUrl = useMemo(() => midiFile ? URL.createObjectURL(midiFile) : null, [midiFile]);
    useEffect(() => () => { if (scoreUrl) URL.revokeObjectURL(scoreUrl); }, [scoreUrl]);
    useEffect(() => () => { if (midiUrl) URL.revokeObjectURL(midiUrl); }, [midiUrl]);

    const chooseScore = (file) => {
        if (!file) return;
        if (!SCORE_EXTENSIONS.has(extensionOf(file))) {
            setScoreError('Choose a PDF, PNG, or JPEG score.');
            return;
        }
        setScoreFile(file);
        setMidiFile(null);
        setResultVersion((version) => version + 1);
        setScoreError('');
        setMidiError('');
    };

    const chooseMidi = (file) => {
        if (!file) return;
        if (!MIDI_EXTENSIONS.has(extensionOf(file))) {
            setMidiError('Choose a .mid or .midi file.');
            return;
        }
        setMidiFile(file);
        setResultVersion((version) => version + 1);
        setMidiError('');
    };

    const isImage = scoreFile && extensionOf(scoreFile) !== 'pdf';

    return (
        <main className="score-midi-page">
            <div className="score-midi-page-heading">
                <div>
                    <span className="score-midi-kicker">SCORE WORKSPACE</span>
                    <h1>Score to MIDI</h1>
                    <p>Bring in printed music, then review the notes in an editable MIDI timeline.</p>
                </div>
                <span className="score-midi-phase-badge">Sheet → MIDI</span>
            </div>

            <section className="score-midi-input-card" aria-labelledby="score-midi-upload-title">
                <div className="score-midi-section-heading">
                    <div>
                        <span className="score-midi-step">01 / SOURCE</span>
                        <h2 id="score-midi-upload-title">Upload sheet music</h2>
                    </div>
                    <span className="score-midi-format-note">PDF · PNG · JPEG</span>
                </div>

                <div className="score-midi-upload-bar">
                    <label className="score-midi-primary-button" htmlFor="score-midi-upload">Browse score</label>
                    <input
                        id="score-midi-upload"
                        className="score-midi-file-input"
                        type="file"
                        accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                        onChange={(event) => {
                            chooseScore(event.target.files?.[0]);
                            event.target.value = '';
                        }}
                    />
                    <div className="score-midi-file-name" title={scoreFile?.name || 'No score selected'}>
                        {scoreFile?.name || 'No score selected'}
                    </div>
                    <button className="score-midi-transcribe-button" type="button" disabled title="Score transcription is not connected yet">
                        Transcribe score
                    </button>
                </div>
                {scoreError && <p className="score-midi-error" role="alert">{scoreError}</p>}
                <p className="score-midi-availability">Score preview is ready now. Automatic transcription will be enabled when the processing service is connected.</p>

                <div className="score-midi-input-grid">
                    <div
                        className={`score-midi-preview ${isDragging ? 'score-midi-preview-dragging' : ''}`}
                        onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
                        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setIsDragging(false); }}
                        onDrop={(event) => {
                            event.preventDefault();
                            setIsDragging(false);
                            chooseScore(event.dataTransfer.files?.[0]);
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
                            <div><span className={scoreFile ? 'done' : ''}>1</span><div><strong>Choose a score</strong><small>PDF, PNG, or JPEG</small></div></div>
                            <div><span>2</span><div><strong>Recognize notation</strong><small>Transcription service pending</small></div></div>
                            <div><span className={midiFile ? 'done' : ''}>3</span><div><strong>Review MIDI</strong><small>Play, edit, and download notes</small></div></div>
                        </div>
                    </div>
                </div>
            </section>

            <section className="score-midi-output-card" aria-labelledby="score-midi-output-title">
                <div className="score-midi-section-heading">
                    <div>
                        <span className="score-midi-step">02 / RESULT</span>
                        <h2 id="score-midi-output-title">Review the MIDI</h2>
                    </div>
                    <label className="score-midi-secondary-button" htmlFor="score-midi-result-upload">Open MIDI result</label>
                    <input
                        id="score-midi-result-upload"
                        className="score-midi-file-input"
                        type="file"
                        accept=".mid,.midi,audio/midi,audio/x-midi"
                        onChange={(event) => {
                            chooseMidi(event.target.files?.[0]);
                            event.target.value = '';
                        }}
                    />
                </div>
                {midiError && <p className="score-midi-error" role="alert">{midiError}</p>}
                {midiFile ? (
                    <ScoreMidiWorkspace
                        key={resultVersion}
                        midiFile={midiFile}
                        downloadUrl={midiUrl}
                        sessionKey={`score-result-${resultVersion}`}
                    />
                ) : (
                    <div className="score-midi-output-empty">
                        <div className="score-midi-output-icon" aria-hidden="true">▤</div>
                        <div>
                            <h3>The MIDI timeline will appear here</h3>
                            <p>Have a MIDI transcription already? Open it above to try playback and the full editor.</p>
                        </div>
                    </div>
                )}
            </section>
        </main>
    );
}
