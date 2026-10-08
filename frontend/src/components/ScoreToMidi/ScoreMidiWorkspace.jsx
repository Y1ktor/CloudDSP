/** Reuse the Studio timeline and popup editor for a MIDI-only score result. */
import React, { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { exportMidiForSheet } from './midiSheetExport';
import MidiEditorPopup from '../StemSplitter/MidiEditorPopup';
import MidiScheduler from '../StemSplitter/Workspace/MidiScheduler';
import WorkspaceTimeline from '../StemSplitter/Workspace/WorkspaceTimeline';
import WorkspaceTempoControls from '../StemSplitter/Workspace/WorkspaceTempoControls';
import { useScoreMidiWorkspace } from './useScoreMidiWorkspace';

const EMPTY = {};
const noop = () => {};

const ScoreMidiWorkspace = forwardRef(function ScoreMidiWorkspace({ midiFile, downloadUrl, sessionKey, sourceEditor = false, onRetainSnapshot, onQueueSheet, queueDisabled = false, isQueuingSheet = false }, ref) {
    const workspace = useScoreMidiWorkspace(midiFile, sessionKey);
    const {
        trackName, audioEngine, timeline, parsedMidiStems, setParsedMidiStems,
        isMidiLoading, midiLoadError, playbackInstrumentStatus, scoreData,
        selectedTrack, setSelectedTrack, editorOpenTrack,
        handleOpenEditor, handleCloseEditor, activeMidiTracks, toggleMidiMode,
        timelineRows, midiStatusByTrack, midiSynthRefs,
        undoStacks, pushUndoState, handleUndoMidi, handleRevertMidi,
    } = workspace;
    // Retain only MIDI bytes/metadata across direction or route changes. The
    // audio hooks still dispose their instruments and transport on unmount.
    const retainRef = useRef(null);
    retainRef.current = scoreData && onRetainSnapshot ? () => {
        try { onRetainSnapshot(exportMidiForSheet(scoreData.midiData, timeline.activeBpm,
            audioEngine.timeSignature, midiFile.name)); } catch { /* No valid editable snapshot yet. */ }
    } : null;
    useEffect(() => () => retainRef.current?.(), []);
    useImperativeHandle(ref, () => ({
        exportMidi: () => exportMidiForSheet(scoreData?.midiData, timeline.activeBpm, audioEngine.timeSignature, midiFile.name),
    }), [scoreData, timeline.activeBpm, audioEngine.timeSignature, midiFile.name]);
    const noteCount = scoreData?.midiData?.tracks?.reduce(
        (count, track) => count + (track.notes?.length || 0), 0,
    ) || 0;
    const readyToPlay = Boolean(scoreData) && playbackInstrumentStatus === 'ready';

    return (
        <section className="score-midi-result" aria-labelledby="score-midi-result-title">
            <div className="score-midi-result-heading">
                <div>
                    <span className="score-midi-kicker">{sourceEditor ? 'MIDI SOURCE' : 'RESULT WORKSPACE'}</span>
                    <h2 id="score-midi-result-title">MIDI timeline</h2>
                    <p>{sourceEditor ? 'Edit notes, BPM, and meter, then queue this version for sheet rendering.' : 'Play, inspect, and edit the notes in the same piano roll as Studio.'}</p>
                </div>
                <div className="score-midi-result-meta">
                    <span>{scoreData ? `${noteCount.toLocaleString()} notes` : 'Preparing MIDI'}</span>
                    <span>{scoreData ? `${timeline.totalBars} bars` : '—'}</span>
                    {downloadUrl && <a className="score-midi-secondary-button" href={downloadUrl} download={midiFile.name}>Download MIDI</a>}
                </div>
            </div>

            {midiLoadError && <p className="score-midi-error" role="alert">This MIDI file could not be opened: {midiLoadError}</p>}
            {!midiLoadError && !scoreData && (
                <div className="score-midi-result-pending" role="status">
                    {isMidiLoading ? 'Reading notes and preparing the timeline…' : 'Preparing the MIDI result…'}
                </div>
            )}
            {scoreData && (
                <>
                    <div className="score-midi-transport">
                        <button type="button" onClick={audioEngine.handleGoToBeginning} aria-label="Go to beginning" title="Go to beginning">|◀</button>
                        <button
                            type="button"
                            onPointerDown={audioEngine.unlockAudio}
                            onClick={audioEngine.togglePlay}
                            disabled={!readyToPlay}
                            aria-label={audioEngine.isPlaying ? 'Pause MIDI' : 'Play MIDI'}
                            title={readyToPlay ? 'Play or pause MIDI' : 'Loading piano sounds'}
                        >{audioEngine.isPlaying ? '❚❚' : '▶'}</button>
                        <button
                            type="button"
                            onClick={() => audioEngine.setIsCycling(!audioEngine.isCycling)}
                            aria-pressed={audioEngine.isCycling}
                            title="Loop selected bars"
                        >↻</button>
                        <span className="score-midi-clock">
                            {audioEngine.formatTime(timeline.dynamicProgress)} / {audioEngine.formatTime(timeline.dynamicDuration)}
                        </span>
                        <span className="score-midi-transport-separator" />
                        <WorkspaceTempoControls audioEngine={audioEngine} />
                        <span className="score-midi-transport-spacer" />
                        {sourceEditor ? <button className="score-midi-transcribe-button score-midi-queue-sheet" type="button"
                            disabled={queueDisabled || isQueuingSheet || !noteCount}
                            onClick={onQueueSheet}>
                            {isQueuingSheet ? 'Uploading…' : 'Queue sheet'}
                        </button> : <span className="score-midi-playback-status" role="status">
                            {readyToPlay ? 'Ready to play' : playbackInstrumentStatus === 'failed' ? 'Piano sounds unavailable' : 'Loading piano sounds…'}
                        </span>}
                    </div>

                    <WorkspaceTimeline
                        audioEngine={audioEngine}
                        timeline={timeline}
                        timelineRows={timelineRows}
                        drumVoiceGainsDb={EMPTY}
                        setDrumVoiceGainDb={noop}
                        selectedTrack={selectedTrack}
                        setSelectedTrack={setSelectedTrack}
                        handleOpenEditor={handleOpenEditor}
                        activeMidiTracks={activeMidiTracks}
                        toggleMidiMode={toggleMidiMode}
                        toggleDrumSubtracks={noop}
                        drumMutedVoices={EMPTY}
                        drumSoloedVoices={EMPTY}
                        toggleDrumMute={noop}
                        toggleDrumSolo={noop}
                        parsedMidiStems={parsedMidiStems}
                        midiStatusByTrack={midiStatusByTrack}
                        editorOpenTrack={editorOpenTrack}
                    />
                    <p className="score-midi-editor-hint">Double-click the Score track to open the piano-roll editor.</p>

                    <MidiScheduler
                        trackName={trackName}
                        activeBpm={timeline.activeBpm}
                        originalBpm={audioEngine.originalBpm}
                        isPlaying={audioEngine.isPlaying}
                        parsedMidiStems={parsedMidiStems}
                        audioCtxRef={audioEngine.audioCtxRef}
                        transportRef={audioEngine.transportRef}
                        synthRef={midiSynthRefs.current.get(trackName)}
                        isMidiMode={Boolean(activeMidiTracks[trackName])}
                        mutedTracks={audioEngine.mutedTracks}
                        soloedTracks={audioEngine.soloedTracks}
                        drumMutedVoices={EMPTY}
                        drumSoloedVoices={EMPTY}
                        trackGainDb={audioEngine.trackGainsDb[trackName] ?? 0}
                        drumVoiceGainsDb={EMPTY}
                    />

                    {editorOpenTrack && <MidiEditorPopup
                        trackName={editorOpenTrack}
                        tempoControls={<WorkspaceTempoControls audioEngine={audioEngine} />}
                        onClose={handleCloseEditor}
                        duration={audioEngine.duration}
                        pixelsPerBar={timeline.pixelsPerBar}
                        totalBars={timeline.totalBars}
                        playheadX={timeline.playheadX}
                        cycleDragRef={timeline.cycleDragRef}
                        cycleRegion={audioEngine.cycleRegion}
                        isCycling={audioEngine.isCycling}
                        timeSignature={audioEngine.timeSignature}
                        playheadDragRef={timeline.playheadDragRef}
                        setIsPlayheadHovered={timeline.setIsPlayheadHovered}
                        isPlayheadHovered={timeline.isPlayheadHovered}
                        handleGoToBeginning={audioEngine.handleGoToBeginning}
                        isPlaying={audioEngine.isPlaying}
                        unlockAudio={audioEngine.unlockAudio}
                        togglePlay={audioEngine.togglePlay}
                        toggleCycling={() => audioEngine.setIsCycling(!audioEngine.isCycling)}
                        mutedTracks={audioEngine.mutedTracks}
                        soloedTracks={audioEngine.soloedTracks}
                        toggleMute={audioEngine.toggleMute}
                        toggleSolo={audioEngine.toggleSolo}
                        drumMutedVoices={EMPTY}
                        drumSoloedVoices={EMPTY}
                        toggleDrumMute={noop}
                        toggleDrumSolo={noop}
                        drumVoiceGainsDb={EMPTY}
                        setDrumVoiceGainDb={noop}
                        activeBpm={timeline.activeBpm}
                        parsedBeatsPerBar={timeline.parsedBeatsPerBar}
                        handleSeek={audioEngine.handleSeek}
                        parsedMidiStems={parsedMidiStems}
                        midiStatus={midiStatusByTrack[editorOpenTrack]}
                        setParsedMidiStems={setParsedMidiStems}
                        audioCtxRef={audioEngine.audioCtxRef}
                        transportRef={audioEngine.transportRef}
                        fileName={midiFile.name}
                        synthRef={midiSynthRefs.current.get(editorOpenTrack)}
                        isMidiMode={Boolean(activeMidiTracks[editorOpenTrack])}
                        setIsMidiMode={toggleMidiMode}
                        handleRevertMidi={() => handleRevertMidi(editorOpenTrack)}
                        handleUndoMidi={() => handleUndoMidi(editorOpenTrack)}
                        pushUndoState={() => pushUndoState(editorOpenTrack)}
                        undoStackLength={undoStacks[editorOpenTrack]?.length || 0}
                    />}
                </>
            )}
        </section>
    );
});

export default ScoreMidiWorkspace;
