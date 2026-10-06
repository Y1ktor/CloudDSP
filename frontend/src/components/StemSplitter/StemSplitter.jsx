/**
 * Composes the shared stem-splitting workspace, connecting source uploads,
 * audio and MIDI playback, timeline editing, and downloads.
 */
import React from 'react';
import ControlBar from './ControlBar';
import MidiEditorPopup from './MidiEditorPopup';
import DownloadPopup from './DownloadPopup';
import MidiScheduler from './Workspace/MidiScheduler';
import WorkspaceTimeline from './Workspace/WorkspaceTimeline';
import WorkspaceTransportControls from './Workspace/WorkspaceTransportControls';
import { WorkspaceDemoNotice, WorkspaceActivityNotice, WorkspaceReadinessNotices } from './Workspace/WorkspaceNotices';
import { useStemSplitterSession } from './Workspace/useStemSplitterSession';
import { useProjectDownloads } from './Workspace/useProjectDownloads';
import { useWorkspaceTimeline } from './Workspace/useWorkspaceTimeline';
import { validateSourceUpload } from './Workspace/sourceUpload';

/** Compose the shared cloud/local workspace from session, file, and timeline responsibilities. */
export default function StemSplitter({
    file, setFile,
    fileName, setFileName,
    splitMode, setSplitMode,
    isSplitting, isRestoringHistoryJob, isHistoryJob, isDemo, canProcess, statusMessage, stemUrls, midiUrls, midiStates, jobTempo, jobId, errorMsg, setErrorMsg,
    sourceUrl,
    executeStemSplit, executeLinkExtraction, beginNewUpload, onOpenExamples
}) {
    const {
        showSigMenu,
        setShowSigMenu,
        selectedTrack,
        setSelectedTrack,
        editorOpenTrack,
        activeMidiTracks,
        drumMutedVoices,
        drumSoloedVoices,
        drumVoiceGainsDb,
        toggleDrumSubtracks,
        toggleDrumMute,
        toggleDrumSolo,
        setDrumVoiceGainDb,
        midiSynthRefs,
        drumVoiceSynthRefs,
        audioEngine,
        parsedMidiStems,
        setParsedMidiStems,
        isMidiLoading,
        handleOpenEditor,
        handleCloseEditor,
        toggleMidiMode,
        midiCapableTrackNames,
        isGlobalMidiEnabled,
        toggleGlobalMidiMode,
        hasDeterminedTempo,
        midiStatusByTrack,
        backendMidiProcessingCount,
        midiDownloadCount,
        undoStacks,
        pushUndoState,
        handleUndoMidi,
        handleRevertMidi,
        tracksToRender,
        timelineRows,
    } = useStemSplitterSession({ file, stemUrls, sourceUrl, midiUrls, midiStates, jobId, jobTempo });
    const {
        isDownloadOpen,
        setIsDownloadOpen,
        selectedDownloadArtifactIds,
        setSelectedDownloadArtifactIds,
        downloadRootFolderName,
        downloadArtifacts,
        openDownloadPopup,
    } = useProjectDownloads({ file, fileName, sourceUrl, stemUrls, midiUrls });
    const timeline = useWorkspaceTimeline({ audioEngine, jobId, editorOpenTrack });
    const {
        pixelsPerBar,
        parsedBeatsPerBar,
        activeBpm,
        totalBars,
        dynamicDuration,
        dynamicProgress,
        playheadX,
        isPlayheadHovered,
        setIsPlayheadHovered,
        playheadDragRef,
        cycleDragRef,
    } = timeline;
    const showActivityNotice = isSplitting || isRestoringHistoryJob;
    const activityMessage = isRestoringHistoryJob ? 'Stems and MIDI will arrive shortly.' : statusMessage;

    const handleFileUpload = (event) => {
        const uploadedFile = event.target.files[0];
        if (!uploadedFile) return;
        const validationError = validateSourceUpload(uploadedFile);
        if (validationError) {
            setErrorMsg(validationError);
            event.target.value = '';
            return;
        }
        setFile(uploadedFile);
        setFileName(uploadedFile.name);
        beginNewUpload();
        setErrorMsg("");
    };

    return (
        <div style={{
            background: 'var(--studio-surface)',
            color: 'var(--studio-text)',
            padding: '20px',
            borderRadius: '5px',
            width: '95vw',
            maxWidth: '1400px',
            margin: '0 auto 40px auto',
            boxSizing: 'border-box',
            boxShadow: '0 10px 28px rgba(44, 62, 80, 0.12)',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px'
        }}>
            <h2 style={{ margin: 0, fontSize: '18px', borderBottom: '1px solid var(--studio-border)', paddingBottom: '10px' }}>
                Stem Splitting & Audio-to-MIDI
            </h2>

            <WorkspaceDemoNotice
                isDemo={isDemo}
                onOpenExamples={onOpenExamples}
            />

            <ControlBar
                isSplitting={isSplitting}
                processingEnabled={canProcess}
                handleFileUpload={handleFileUpload}
                fileName={fileName}
                splitMode={splitMode}
                setSplitMode={setSplitMode}
                executeStemSplit={executeStemSplit}
                executeLinkExtraction={executeLinkExtraction}
                file={file}
                errorMsg={errorMsg}
            />

            {/* Dynamic Results Area */}
            <div style={{
                background: 'var(--studio-surface-muted)',
                borderRadius: '4px',
                padding: '20px',
                minHeight: '200px',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: Object.keys(tracksToRender).length ? 'flex-start' : 'center',
                alignItems: Object.keys(tracksToRender).length ? 'stretch' : 'center',
                color: 'var(--studio-text-muted)',
                border: '1px dashed var(--studio-border-strong)',
                gap: '15px'
            }}>
                {Object.keys(tracksToRender).length ? (
                    <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '3px' }}>
                        <WorkspaceActivityNotice
                            showActivityNotice={showActivityNotice}
                            activityMessage={activityMessage}
                            isSplitting={isSplitting}
                            hasDeterminedTempo={hasDeterminedTempo}
                        />
                        <WorkspaceReadinessNotices
                            isAudioReady={audioEngine.isAudioReady}
                            backendMidiProcessingCount={backendMidiProcessingCount}
                            isSplitting={isSplitting}
                            midiDownloadCount={midiDownloadCount}
                            isHistoryJob={isHistoryJob}
                        />
                        <WorkspaceTransportControls
                            audioEngine={audioEngine}
                            isMidiLoading={isMidiLoading}
                            dynamicProgress={dynamicProgress}
                            dynamicDuration={dynamicDuration}
                            toggleGlobalMidiMode={toggleGlobalMidiMode}
                            midiCapableTrackNames={midiCapableTrackNames}
                            isGlobalMidiEnabled={isGlobalMidiEnabled}
                            hasDeterminedTempo={hasDeterminedTempo}
                            showSigMenu={showSigMenu}
                            setShowSigMenu={setShowSigMenu}
                            openDownloadPopup={openDownloadPopup}
                            downloadArtifacts={downloadArtifacts}
                        />

                        <WorkspaceTimeline
                            audioEngine={audioEngine}
                            timeline={timeline}
                            timelineRows={timelineRows}
                            drumVoiceGainsDb={drumVoiceGainsDb}
                            setDrumVoiceGainDb={setDrumVoiceGainDb}
                            selectedTrack={selectedTrack}
                            setSelectedTrack={setSelectedTrack}
                            handleOpenEditor={handleOpenEditor}
                            activeMidiTracks={activeMidiTracks}
                            toggleMidiMode={toggleMidiMode}
                            toggleDrumSubtracks={toggleDrumSubtracks}
                            drumMutedVoices={drumMutedVoices}
                            drumSoloedVoices={drumSoloedVoices}
                            toggleDrumMute={toggleDrumMute}
                            toggleDrumSolo={toggleDrumSolo}
                            parsedMidiStems={parsedMidiStems}
                            midiStatusByTrack={midiStatusByTrack}
                            editorOpenTrack={editorOpenTrack}
                        />
                    </div>
                ) : (
                    showActivityNotice ? (
                        <WorkspaceActivityNotice
                            showActivityNotice={showActivityNotice}
                            activityMessage={activityMessage}
                            isSplitting={isSplitting}
                            hasDeterminedTempo={hasDeterminedTempo}
                        />
                    ) : <div>Stem extraction and MIDI results will appear here as downloadable multitracks</div>
                )}
                <style>{`
                    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                `}</style>
            </div>

            {isDownloadOpen && (
                <DownloadPopup
                    rootFolderName={downloadRootFolderName}
                    artifacts={downloadArtifacts}
                    selectedArtifactIds={selectedDownloadArtifactIds}
                    setSelectedArtifactIds={setSelectedDownloadArtifactIds}
                    onClose={() => setIsDownloadOpen(false)}
                />
            )}

            {/* Background MIDI Schedulers */}
            {Object.keys(parsedMidiStems).map(trackName => {
                const synthRefToUse = parsedMidiStems[trackName]?.isAdtofDrum
                    ? drumVoiceSynthRefs.current.get(trackName)
                    : midiSynthRefs.current.get(trackName);

                return (
                    <MidiScheduler
                        key={`midi-synth-${trackName}`}
                        trackName={trackName}
                        activeBpm={activeBpm}
                        originalBpm={audioEngine.originalBpm}
                        isPlaying={audioEngine.isPlaying}
                        parsedMidiStems={parsedMidiStems}
                        audioCtxRef={audioEngine.audioCtxRef}
                        transportRef={audioEngine.transportRef}
                        synthRef={synthRefToUse}
                        isMidiMode={!!activeMidiTracks[trackName]}
                        mutedTracks={audioEngine.mutedTracks}
                        soloedTracks={audioEngine.soloedTracks}
                        drumMutedVoices={drumMutedVoices}
                        drumSoloedVoices={drumSoloedVoices}
                        trackGainDb={audioEngine.trackGainsDb[trackName] ?? 0}
                        drumVoiceGainsDb={drumVoiceGainsDb}
                    />
                );
            })}

            {/* Mount the expensive editing surface only while it is visible.
                A closed popup has no reason to retain keyboard listeners,
                selection state, or a MIDI-note virtual index. */}
            {editorOpenTrack && <MidiEditorPopup
                trackName={editorOpenTrack}
                onClose={handleCloseEditor}
                duration={audioEngine.duration}
                pixelsPerBar={pixelsPerBar}
                totalBars={totalBars}
                playheadX={playheadX}
                cycleDragRef={cycleDragRef}
                cycleRegion={audioEngine.cycleRegion}
                isCycling={audioEngine.isCycling}
                timeSignature={audioEngine.timeSignature}
                playheadDragRef={playheadDragRef}
                setIsPlayheadHovered={setIsPlayheadHovered}
                isPlayheadHovered={isPlayheadHovered}
                handleGoToBeginning={audioEngine.handleGoToBeginning}
                isPlaying={audioEngine.isPlaying}
                unlockAudio={audioEngine.unlockAudio}
                togglePlay={audioEngine.togglePlay}
                toggleCycling={() => audioEngine.setIsCycling(!audioEngine.isCycling)}
                mutedTracks={audioEngine.mutedTracks}
                soloedTracks={audioEngine.soloedTracks}
                toggleMute={audioEngine.toggleMute}
                toggleSolo={audioEngine.toggleSolo}
                drumMutedVoices={drumMutedVoices}
                drumSoloedVoices={drumSoloedVoices}
                toggleDrumMute={toggleDrumMute}
                toggleDrumSolo={toggleDrumSolo}
                drumVoiceGainsDb={drumVoiceGainsDb}
                setDrumVoiceGainDb={setDrumVoiceGainDb}
                activeBpm={activeBpm}
                parsedBeatsPerBar={parsedBeatsPerBar}
                handleSeek={audioEngine.handleSeek}
                parsedMidiStems={parsedMidiStems}
                midiStatus={midiStatusByTrack[editorOpenTrack]}
                setParsedMidiStems={setParsedMidiStems}
                audioCtxRef={audioEngine.audioCtxRef}
                transportRef={audioEngine.transportRef}
                fileName={fileName}
                synthRef={
                    parsedMidiStems[editorOpenTrack]?.isAdtofDrum
                        ? drumVoiceSynthRefs.current.get(editorOpenTrack)
                        : midiSynthRefs.current.get(editorOpenTrack)
                }
                isMidiMode={!!activeMidiTracks[editorOpenTrack]}
                setIsMidiMode={() => toggleMidiMode(editorOpenTrack)}
                handleRevertMidi={() => handleRevertMidi(editorOpenTrack)}
                handleUndoMidi={() => handleUndoMidi(editorOpenTrack)}
                pushUndoState={() => pushUndoState(editorOpenTrack)}
                undoStackLength={undoStacks[editorOpenTrack] ? undoStacks[editorOpenTrack].length : 0}
            />}
        </div>
    );
}
