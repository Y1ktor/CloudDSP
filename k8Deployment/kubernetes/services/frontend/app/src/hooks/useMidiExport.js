import { useCallback } from 'react';
import { Midi } from '@tonejs/midi';

/**
 * useMidiExport
 * 
 * A custom hook providing functionality to export parsed MIDI data back into standard `.mid` files.
 * It handles both full track exports and localized cycle region (loop) exports, ensuring that 
 * the exported file correctly honors any user-adjusted BPM (time stretching) by forcing 
 * `@tonejs/midi` to recalculate its internal tick map.
 * 
 * @param {Object} props - Hook arguments
 * @param {Object} props.parsedMidiStems - The global dictionary of parsed MIDI objects
 * @param {string} props.trackName - The currently selected track to export
 * @param {string} props.fileName - The original file name to use as a base for the download
 * @param {Object} props.cycleRegion - The current cycle loop boundaries { startBar, endBar }
 * @param {number} props.pixelsPerBar - The current horizontal zoom scale
 * @param {number} props.duration - The master duration of the track in seconds
 * @param {number} props.activeBpm - The current user-adjusted playback BPM
 * @param {number} props.parsedBeatsPerBar - The number of beats per bar in the time signature
 * @returns {Object} { handleExportMidi, handleExportCycleRange }
 */
export function useMidiExport({ 
    parsedMidiStems, 
    trackName, 
    fileName, 
    cycleRegion, 
    pixelsPerBar, 
    totalBars,
    duration,
    activeBpm,
    parsedBeatsPerBar
}) {
    const handleExportMidi = useCallback(() => {
        const stemData = parsedMidiStems[trackName];
        if (!stemData || !stemData.midiData) return;
        
        const exportedMidi = new Midi(stemData.midiData.toArray());
        
        // Cache original absolute times before changing tempo map
        const originalTimes = new Map();
        exportedMidi.tracks.forEach(track => {
            track.notes.forEach(note => {
                originalTimes.set(note, { time: note.time, duration: note.duration });
            });
            for (const ccNumber in track.controlChanges) {
                track.controlChanges[ccNumber].forEach(cc => {
                    originalTimes.set(cc, { time: cc.time });
                });
            }
        });

        exportedMidi.header.tempos = [{ ticks: 0, bpm: activeBpm }];
        exportedMidi.header.timeSignatures = [{ ticks: 0, timeSignature: [4, 4] }];
        exportedMidi.header.update();

        // Re-assign absolute times so @tonejs/midi recalculates the ticks for the new BPM
        exportedMidi.tracks.forEach(track => {
            track.notes.forEach(note => {
                const cached = originalTimes.get(note);
                note.time = cached.time;
                note.duration = cached.duration;
            });
            for (const ccNumber in track.controlChanges) {
                track.controlChanges[ccNumber].forEach(cc => {
                    cc.time = originalTimes.get(cc).time;
                });
            }
        });

        const midiArray = exportedMidi.toArray();
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
    }, [parsedMidiStems, trackName, fileName, activeBpm]);

    const handleExportCycleRange = useCallback(() => {
        const stemData = parsedMidiStems[trackName];
        if (!stemData || !stemData.midiData) return;
        
        const secondsPerBar = parsedBeatsPerBar * (60 / activeBpm);
        const cycleStartSeconds = cycleRegion.startBar * secondsPerBar;
        const cycleEndSeconds = cycleRegion.endBar * secondsPerBar;
        
        const originalMidi = stemData.midiData;
        
        const slicedMidi = new Midi();
        const json = slicedMidi.toJSON();
        json.header.ppq = originalMidi.header.ppq;
        slicedMidi.fromJSON(json);
        
        slicedMidi.header.tempos = [{ ticks: 0, bpm: activeBpm }];
        slicedMidi.header.timeSignatures = [{ ticks: 0, timeSignature: [4, 4] }];
        slicedMidi.header.update();

        originalMidi.tracks.forEach((originalTrack) => {
            const newTrack = slicedMidi.addTrack();
            newTrack.name = originalTrack.name;

            originalTrack.notes.forEach((note) => {
                const noteEndSeconds = note.time + note.duration;
                const isInsideRange = (note.time < cycleEndSeconds) && (noteEndSeconds > cycleStartSeconds);

                if (isInsideRange) {
                    const adjustedStartSeconds = Math.max(0, note.time - cycleStartSeconds);
                    const adjustedEndSeconds = Math.min(cycleEndSeconds - cycleStartSeconds, noteEndSeconds - cycleStartSeconds);
                    const adjustedDurationSeconds = adjustedEndSeconds - adjustedStartSeconds;

                    if (adjustedDurationSeconds > 0) {
                        newTrack.addNote({
                            midi: note.midi,
                            time: adjustedStartSeconds,
                            duration: adjustedDurationSeconds,
                            velocity: note.velocity !== undefined ? note.velocity : 0.8
                        });
                    }
                }
            });

            for (const ccNumber in originalTrack.controlChanges) {
                originalTrack.controlChanges[ccNumber].forEach(cc => {
                    if (cc.time >= cycleStartSeconds && cc.time <= cycleEndSeconds) {
                        newTrack.addCC({
                            number: cc.number,
                            value: cc.value,
                            time: cc.time - cycleStartSeconds
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
    }, [parsedMidiStems, trackName, fileName, cycleRegion, pixelsPerBar, totalBars, duration, activeBpm, parsedBeatsPerBar]);

    return { handleExportMidi, handleExportCycleRange };
}
