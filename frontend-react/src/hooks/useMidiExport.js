import { useCallback } from 'react';
import { Midi } from '@tonejs/midi';

export function useMidiExport({ 
    parsedMidiStems, 
    trackName, 
    fileName, 
    cycleRegion, 
    pixelsPerBar, 
    totalBars, 
    duration 
}) {
    const handleExportMidi = useCallback(() => {
        const stemData = parsedMidiStems[trackName];
        if (!stemData || !stemData.midiData) return;
        
        const midiArray = stemData.midiData.toArray();
        const blob = new Blob([midiArray], { type: 'audio/midi' });
        const url = URL.createObjectURL(blob);
        
        const baseName = fileName ? fileName.replace(/\.[^/.]+$/, "") : "myupload";
        const exportName = `${baseName}-${trackName}.mid`;
        
        const a = document.createElement('a');
        a.href = url;
        a.download = exportName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }, [parsedMidiStems, trackName, fileName]);

    const handleExportCycleRange = useCallback(() => {
        const stemData = parsedMidiStems[trackName];
        if (!stemData || !stemData.midiData) return;
        
        const cycleStartSeconds = (cycleRegion.startBar / totalBars) * duration;
        const cycleEndSeconds = (cycleRegion.endBar / totalBars) * duration;
        
        const originalMidi = stemData.midiData;
        const startTick = originalMidi.header.secondsToTicks(cycleStartSeconds);
        const endTick = originalMidi.header.secondsToTicks(cycleEndSeconds);
        
        const slicedMidi = new Midi();
        slicedMidi.header.tempos = [];
        slicedMidi.header.timeSignatures = [];
        
        // Find the active tempo and time signature
        const temposCopy = [...originalMidi.header.tempos];
        const timeSigsCopy = [...originalMidi.header.timeSignatures];
        const activeTempo = temposCopy.reverse().find(t => t.ticks <= startTick) || { bpm: 120 };
        const activeTimeSig = timeSigsCopy.reverse().find(t => t.ticks <= startTick) || { timeSignature: [4, 4] };
        
        slicedMidi.header.tempos.push({ ticks: 0, bpm: activeTempo.bpm });
        slicedMidi.header.timeSignatures.push({ ticks: 0, timeSignature: activeTimeSig.timeSignature });

        originalMidi.header.tempos.forEach(t => {
            if (t.ticks > startTick && t.ticks < endTick) {
                slicedMidi.header.tempos.push({ ticks: t.ticks - startTick, bpm: t.bpm });
            }
        });

        // CRITICAL: Must update header after mutating tempos/timeSignatures to prevent infinite loops
        slicedMidi.header.update();

        originalMidi.tracks.forEach((originalTrack) => {
            const newTrack = slicedMidi.addTrack();
            newTrack.name = originalTrack.name;

            originalTrack.notes.forEach((note) => {
                const noteEndTick = note.ticks + note.durationTicks;
                const isInsideRange = (note.ticks < endTick) && (noteEndTick > startTick);

                if (isInsideRange) {
                    const adjustedStartTick = Math.max(0, note.ticks - startTick);
                    const adjustedEndTick = Math.min(endTick - startTick, noteEndTick - startTick);
                    const adjustedDurationTicks = adjustedEndTick - adjustedStartTick;

                    if (adjustedDurationTicks > 0) {
                        newTrack.addNote({
                            midi: note.midi,
                            ticks: adjustedStartTick,
                            durationTicks: adjustedDurationTicks,
                            velocity: note.velocity !== undefined ? note.velocity : 0.8
                        });
                    }
                }
            });

            for (const ccNumber in originalTrack.controlChanges) {
                originalTrack.controlChanges[ccNumber].forEach(cc => {
                    if (cc.ticks >= startTick && cc.ticks <= endTick) {
                        newTrack.addCC({
                            number: cc.number,
                            value: cc.value,
                            ticks: cc.ticks - startTick
                        });
                    }
                });
            }

            newTrack.notes.sort((a, b) => a.ticks - b.ticks);
        });

        const midiArray = slicedMidi.toArray();
        const blob = new Blob([midiArray], { type: 'audio/midi' });
        const url = URL.createObjectURL(blob);
        
        const baseName = fileName ? fileName.replace(/\.[^/.]+$/, "") : "myupload";
        const exportName = `${baseName}-${trackName}-cycle.mid`;
        
        const a = document.createElement('a');
        a.href = url;
        a.download = exportName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }, [parsedMidiStems, trackName, fileName, cycleRegion, pixelsPerBar, totalBars, duration]);

    return { handleExportMidi, handleExportCycleRange };
}
