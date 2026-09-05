import { Midi } from '@tonejs/midi';
import { isAdtofDrumMidi } from './DrumMidi';

/**
 * MidiParser.js
 * 
 * Utility functions for parsing raw MIDI files utilizing Tone.js (@tonejs/midi).
 * This module transforms raw ArrayBuffers directly from S3 into structural JSON blocks 
 * suitable for rendering onto a React Canvas. The durable backend job owns master
 * tempo selection; MIDI header tempo is retained here only as per-file metadata.
 */

/**
 * Parses a MIDI file and calculates project metadata like total bars.
 * 
 * @param {ArrayBuffer | string} midiInput - The MIDI file as an ArrayBuffer, or a URL to a local MIDI file.
 * @param {string} timeSignature - The time signature string (e.g., "4/4", "3/4").
 * @param {Object} options - Parsing context supplied by the durable job state.
 * @param {boolean} options.isAdtofDrum - Whether the backend used ADTOF for this MIDI file.
 * @returns {Promise<Object>} An object containing bpm, totalBars, duration,
 * and the parsed MIDI object. ADTOF drum files are additionally tagged so
 * callers can render a drum grid instead of a piano roll.
 */
export async function parseMidiFile(midiInput, timeSignature = "4/4", { isAdtofDrum = false } = {}) {
    let midi;
    
    // Support either a URL string for local testing, or a raw ArrayBuffer from S3/File upload
    if (typeof midiInput === 'string') {
        const response = await fetch(midiInput);
        if (!response.ok) {
            throw new Error(`Failed to fetch MIDI from ${midiInput}: ${response.statusText}`);
        }
        const arrayBuffer = await response.arrayBuffer();
        midi = new Midi(arrayBuffer);
    } else {
        midi = new Midi(midiInput);
    }

    // 1. Extract BPM from the MIDI header's tempo track (default to 120 if none found)
    let bpm = 120;
    if (midi.header && midi.header.tempos && midi.header.tempos.length > 0) {
        // Use the first tempo event (we injected this in Python earlier!)
        bpm = midi.header.tempos[0].bpm;
    }

    // 2. Parse the Beats Per Bar from the Time Signature (e.g. "4/4" -> 4)
    const beatsPerBar = parseInt(timeSignature.split('/')[0], 10) || 4;

    // 3. Get the absolute duration of the MIDI file in seconds
    const durationInSeconds = midi.duration;

    // 4. Calculate total number of beats: Duration(s) * (BPM / 60)
    const totalBeats = durationInSeconds * (bpm / 60);

    // 5. Calculate total bars required for the UI timeline
    const totalBars = Math.ceil(totalBeats / beatsPerBar);

    return {
        bpm,
        totalBars,
        durationInSeconds,
        totalBeats,
        // Do not classify a melodic file that happens to contain one of these
        // pitches as drums. The durable extractor state is authoritative.
        isAdtofDrum: isAdtofDrum && isAdtofDrumMidi(midi),
        midiData: midi // Raw parsed Tone.js Midi object for rendering later
    };
}
