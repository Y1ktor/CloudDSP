/**
 * Combines fixed track controls with the scrollable workspace ruler, playhead,
 * and MIDI grid.
 */
import React from 'react';
import TrackList from '../TrackList';
import TrackGrid from '../TrackGrid';
import TimelineRuler from '../TimelineRuler';
import TransportPlayheadLine from '../TransportPlayheadLine';

/** Fixed track consoles and a shared scroll viewport; the modal releases the occluded note grid. */
export default function WorkspaceTimeline({
    audioEngine,
    timeline,
    timelineRows,
    drumVoiceGainsDb,
    setDrumVoiceGainDb,
    selectedTrack,
    setSelectedTrack,
    handleOpenEditor,
    activeMidiTracks,
    toggleMidiMode,
    toggleDrumSubtracks,
    drumMutedVoices,
    drumSoloedVoices,
    toggleDrumMute,
    toggleDrumSolo,
    parsedMidiStems,
    midiStatusByTrack,
    editorOpenTrack,
}) {
    const {
        pixelsPerBar,
        setPixelsPerBar,
        totalBars,
        timelineRef,
        timelinePlayheadRef,
        playheadX,
        cycleDragRef,
        playheadDragRef,
        setIsPlayheadHovered,
        isPlayheadHovered,
        rulerPlayheadRef,
        visibleTimelineRange,
        activeBpm,
        parsedBeatsPerBar,
        handleTimelineSeek,
        setTimelineScrollContainer,
    } = timeline;

    return (
        <div style={{ width: '100%', display: 'flex', gap: '3px', paddingBottom: '10px' }}>

            {/* LEFT COLUMN: Track Consoles (Fixed) */}
            <TrackList
                pixelsPerBar={pixelsPerBar}
                setPixelsPerBar={setPixelsPerBar}
                timelineRows={timelineRows}
                toggleMute={audioEngine.toggleMute}
                mutedTracks={audioEngine.mutedTracks}
                toggleSolo={audioEngine.toggleSolo}
                soloedTracks={audioEngine.soloedTracks}
                trackGainsDb={audioEngine.trackGainsDb}
                setTrackGainDb={audioEngine.setTrackGainDb}
                drumVoiceGainsDb={drumVoiceGainsDb}
                setDrumVoiceGainDb={setDrumVoiceGainDb}
                selectedTrack={selectedTrack}
                setSelectedTrack={setSelectedTrack}
                onDoubleClickTrack={handleOpenEditor}
                activeMidiTracks={activeMidiTracks}
                toggleMidiMode={toggleMidiMode}
                toggleDrumSubtracks={toggleDrumSubtracks}
                drumMutedVoices={drumMutedVoices}
                drumSoloedVoices={drumSoloedVoices}
                toggleDrumMute={toggleDrumMute}
                toggleDrumSolo={toggleDrumSolo}
            />

            {/* RIGHT COLUMN: Timeline Canvas (Scrollable) */}
            <div ref={setTimelineScrollContainer} style={{ flexGrow: 1, overflowX: 'auto', paddingBottom: '10px', scrollBehavior: 'auto', backgroundColor: 'var(--studio-canvas)' }}>
                <div ref={timelineRef} style={{ minWidth: `${pixelsPerBar * totalBars}px`, display: 'flex', flexDirection: 'column', gap: '3px', position: 'relative', backgroundColor: 'var(--studio-canvas)' }}>

                    {/* Time Indicator (Playhead) */}
                    {audioEngine.duration > 0 && (
                        <TransportPlayheadLine
                            playheadRef={timelinePlayheadRef}
                            fallbackX={playheadX}
                            isPlaying={audioEngine.isPlaying}
                        />
                    )}

                    {/* Timeline Header Right (Time Bar) */}
                    <TimelineRuler
                        duration={audioEngine.duration}
                        pixelsPerBar={pixelsPerBar}
                        cycleDragRef={cycleDragRef}
                        cycleRegion={audioEngine.cycleRegion}
                        isCycling={audioEngine.isCycling}
                        totalBars={totalBars}
                        timeSignature={audioEngine.timeSignature}
                        timelineRef={timelineRef}
                        playheadDragRef={playheadDragRef}
                        setIsPlayheadHovered={setIsPlayheadHovered}
                        isPlayheadHovered={isPlayheadHovered}
                        playheadX={playheadX}
                        playheadElementRef={rulerPlayheadRef}
                        isPlayheadExternallyDriven={audioEngine.isPlaying}
                        visibleRange={visibleTimelineRange}
                        activeBpm={activeBpm}
                        parsedBeatsPerBar={parsedBeatsPerBar}
                        handleSeek={handleTimelineSeek}
                    />

                    {/* The popup is modal. Its own virtual piano roll is
                        the only useful note surface while open, so release
                        the occluded workspace grid instead of updating two
                        dense note renderers during an edit. */}
                    {!editorOpenTrack && <TrackGrid
                        timelineRows={timelineRows}
                        parsedMidiStems={parsedMidiStems}
                        midiStatusByTrack={midiStatusByTrack}
                        pixelsPerBar={pixelsPerBar}
                        activeBpm={activeBpm}
                        parsedBeatsPerBar={parsedBeatsPerBar}
                        selectedTrack={selectedTrack}
                        setSelectedTrack={setSelectedTrack}
                        onDoubleClickTrack={handleOpenEditor}
                        visibleRange={visibleTimelineRange}
                    />}
                </div>
            </div>
        </div>
    );
}
