import { Midi } from '@tonejs/midi';

/**
 * MidiParser.js
 * 
 * Utility functions for parsing raw MIDI files utilizing Tone.js (@tonejs/midi).
 * This module transforms raw ArrayBuffers directly from S3 into structural JSON blocks 
 * suitable for rendering onto a React Canvas. It also implements a Smart BPM Hierarchy 
 * strategy to safely calculate the true master tempo from a diverse collection of stems.
 */

/**
 * Parses a MIDI file and calculates project metadata like total bars.
 * 
 * @param {ArrayBuffer | string} midiInput - The MIDI file as an ArrayBuffer, or a URL to a local MIDI file.
 * @param {string} timeSignature - The time signature string (e.g., "4/4", "3/4").
 * @returns {Promise<Object>} An object containing bpm, totalBars, duration, and the parsed midi object.
 */
export async function parseMidiFile(midiInput, timeSignature = "4/4") {
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
        midiData: midi // Raw parsed Tone.js Midi object for rendering later
    };
}

/**
 * Determines the master BPM from a collection of parsed stems using a hierarchy strategy.
 * This avoids the pitfalls of averaging erratic vocal/ambient BPMs.
 * 
 * @param {Object} parsedStems - An object mapping track names to their parsed MIDI data (which contains .bpm)
 * @returns {number} The most reliable BPM found.
 */
export function determineMasterBpm(parsedStems) {
    if (!parsedStems || Object.keys(parsedStems).length === 0) return 120;

    const trackNames = Object.keys(parsedStems);
    
    // Helper to find a track by keyword (case-insensitive) and return its BPM
    const getBpm = (keyword) => {
        const match = trackNames.find(name => name.toLowerCase().includes(keyword));
        return match ? parsedStems[match].bpm : null;
    };

    // HIERARCHY 1: Drums / Percussion (Most reliable transients)
    const drumsBpm = getBpm('drum') || getBpm('percussion');
    if (drumsBpm) return drumsBpm;

    // HIERARCHY 2: Bass (Usually locked tightly to the grid)
    const bassBpm = getBpm('bass');
    if (bassBpm) return bassBpm;

    // HIERARCHY 3: Instrumental / Accompaniment / Other (Standard for 2-stem splits)
    const instBpm = getBpm('instrumental') || getBpm('accompaniment') || getBpm('other');
    if (instBpm) return instBpm;

    // HIERARCHY 4: Any non-vocal track
    const nonVocal = trackNames.find(name => !name.toLowerCase().includes('vocal'));
    if (nonVocal) return parsedStems[nonVocal].bpm;

    // FALLBACK: If literally only a vocal track exists, or completely unrecognized names, just take the first one
    return parsedStems[trackNames[0]].bpm;
}
