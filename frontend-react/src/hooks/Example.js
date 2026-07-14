import { Midi } from '@tonejs/midi';

function exportCycleRange(originalMidiBuffer, startTick, endTick) {
  // 1. Parse the original MIDI file
  const originalMidi = new Midi(originalMidiBuffer);
  
  // 2. Create a blank, fresh MIDI object for the export
  const slicedMidi = new Midi();
  slicedMidi.header.setTempos([]); 
  slicedMidi.header.setTimeSignatures([]);
  
  // 3. Preserve the Tempo and Time Signature
  // Find the last tempo that occurred BEFORE or AT our cycle start
  const activeTempo = originalMidi.header.tempos.reverse().find(t => t.ticks <= startTick) || { bpm: 120 };
  const activeTimeSig = originalMidi.header.timeSignatures.reverse().find(t => t.ticks <= startTick) || { timeSignature: [4, 4] };
  
  // Inject them at the very beginning of the new file (Tick 0)
  slicedMidi.header.tempos.push({ ticks: 0, bpm: activeTempo.bpm });
  slicedMidi.header.timeSignatures.push({ ticks: 0, timeSignature: activeTimeSig.timeSignature });

  // Add any tempo changes that happen INSIDE the cycle range
  originalMidi.header.tempos.forEach(t => {
      if (t.ticks > startTick && t.ticks < endTick) {
          slicedMidi.header.tempos.push({ ticks: t.ticks - startTick, bpm: t.bpm });
      }
  });

  // 4. Process the Tracks
  originalMidi.tracks.forEach((originalTrack) => {
    const newTrack = slicedMidi.addTrack();
    newTrack.name = originalTrack.name;
    newTrack.instrument = originalTrack.instrument;

    // Filter and adjust NOTES
    originalTrack.notes.forEach((note) => {
      // Check if the note falls inside our cycle range AT ALL
      const noteEndTick = note.ticks + note.durationTicks;
      const isInsideRange = (note.ticks < endTick) && (noteEndTick > startTick);

      if (isInsideRange) {
        // Calculate new start tick (clamp to 0 if it started before the cycle)
        const adjustedStartTick = Math.max(0, note.ticks - startTick);
        
        // Calculate new end tick (clamp to end of cycle if it holds past it)
        const adjustedEndTick = Math.min(endTick - startTick, noteEndTick - startTick);
        
        // Calculate new duration
        const adjustedDurationTicks = adjustedEndTick - adjustedStartTick;

        // Only add if it actually has a duration
        if (adjustedDurationTicks > 0) {
          newTrack.addNote({
            midi: note.midi,
            ticks: adjustedStartTick,
            durationTicks: adjustedDurationTicks,
            velocity: note.velocity
          });
        }
      }
    });

    // Filter and adjust CONTROL CHANGES (e.g., Sustain pedal)
    // *Important so the DAW doesn't get stuck with a held sustain pedal!*
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

    // 5. CRITICAL: Sort everything chronologically by ticks
    newTrack.notes.sort((a, b) => a.ticks - b.ticks);
    // (addCC implicitly handles sorting inside the library, but Notes must be sorted!)
  });

  // 6. Export as a valid Binary MIDI ArrayBuffer
  return slicedMidi.toArray();
}