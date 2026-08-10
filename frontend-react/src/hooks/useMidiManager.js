import React from 'react';
import { Sampler, SplendidGrandPiano, Soundfont } from 'smplr';
import { parseMidiFile } from '../utils/MidiParser';
import {
    ADTOF_DRUM_SAMPLE_BUFFERS,
    ADTOF_DRUM_SAMPLER_OPTIONS,
    ADTOF_DRUM_VOICES
} from '../utils/DrumMidi';

function createMelodicSynth(audioContext, trackName) {
    if (trackName === 'guitar') {
        return new Soundfont(audioContext, { instrument: 'acoustic_guitar_nylon' });
    }
    if (trackName === 'bass') {
        return new Soundfont(audioContext, { instrument: 'acoustic_bass' });
    }
    return new SplendidGrandPiano(audioContext);
}

function ensureMelodicSynth(midiSynthRefs, audioContext, trackName) {
    const existingRef = midiSynthRefs.current.get(trackName);
    if (existingRef?.current) return existingRef;

    const synthRef = { current: createMelodicSynth(audioContext, trackName) };
    midiSynthRefs.current.set(trackName, synthRef);
    return synthRef;
}

function ensureDrumVoiceSynths(drumVoiceSynthRefs, audioContext, trackName) {
    const existingRefs = drumVoiceSynthRefs.current.get(trackName);
    if (existingRefs) return existingRefs;

    const voiceSynthRefs = new Map();
    ADTOF_DRUM_VOICES.forEach((voice) => {
        const sampler = Sampler(audioContext, {
            ...ADTOF_DRUM_SAMPLER_OPTIONS,
            // A separate sampler output per voice lets Kick, Snare, and the
            // other ADTOF classes have independent, click-free dB gain.
            buffers: { [voice.sample]: ADTOF_DRUM_SAMPLE_BUFFERS[voice.sample] },
        });
        sampler.ready.catch((error) => {
            console.error(`[CloudDSP] Drum sample load failed for '${voice.label}':`, error);
        });
        voiceSynthRefs.set(voice.id, { current: sampler });
    });
    drumVoiceSynthRefs.current.set(trackName, voiceSynthRefs);
    return voiceSynthRefs;
}

/**
 * Fetches and parses generated MIDI files as their presigned URLs arrive, and
 * initializes one isolated MIDI output per track (or per ADTOF drum class).
 * Tempo is intentionally not inferred from MIDI headers: the durable backend
 * job owns tempo selection and passes it to the editor separately.
 */
export function useMidiManager(
    midiUrls,
    midiStates,
    jobId,
    timeSignature,
    audioCtxRef,
    midiSynthRefs,
    drumVoiceSynthRefs
) {
    const [parsedMidiStems, setParsedMidiStems] = React.useState({});
    const [originalMidiStems, setOriginalMidiStems] = React.useState({});
    const [isMidiLoading, setIsMidiLoading] = React.useState(false);
    const parsedMidiStemsRef = React.useRef({});
    const originalMidiStemsRef = React.useRef({});
    const loadedUrlsRef = React.useRef(new Map());
    const inFlightTracksRef = React.useRef(new Set());
    const resetVersionRef = React.useRef(0);
    const previousJobIdRef = React.useRef(jobId);

    React.useEffect(() => {
        // A new upload normally clears midiUrls, but reset explicitly by job
        // ID as well so an old completed job can never suppress the first MIDI
        // fetch for the next one.
        const jobChanged = previousJobIdRef.current !== jobId;
        if (jobChanged || !midiUrls) {
            previousJobIdRef.current = jobId;
            resetVersionRef.current += 1;
            loadedUrlsRef.current.clear();
            inFlightTracksRef.current.clear();
            parsedMidiStemsRef.current = {};
            originalMidiStemsRef.current = {};
            setParsedMidiStems({});
            setOriginalMidiStems({});
            setIsMidiLoading(false);
            if (!midiUrls) return;
        }

        const entriesToLoad = Object.entries(midiUrls).filter(([track, url]) => (
            url
            && loadedUrlsRef.current.get(track) !== url
            && !inFlightTracksRef.current.has(track)
        ));
        if (entriesToLoad.length === 0) return;

        const resetVersion = resetVersionRef.current;
        entriesToLoad.forEach(([track]) => inFlightTracksRef.current.add(track));

        const fetchAndParse = async () => {
            setIsMidiLoading(true);

            if (!audioCtxRef.current) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                audioCtxRef.current = new AudioContext();
            }
            entriesToLoad.forEach(([track]) => {
                if (track === 'drums') {
                    // Begin loading the five small ADTOF samples while MIDI is
                    // downloading. They are split by voice so each lane has an
                    // independent output fader instead of sharing one kit bus.
                    ensureDrumVoiceSynths(drumVoiceSynthRefs, audioCtxRef.current, track);
                } else {
                    ensureMelodicSynth(midiSynthRefs, audioCtxRef.current, track);
                }
            });

            const parsedUpdates = {};
            const originalUpdates = {};

            await Promise.all(entriesToLoad.map(async ([track, url]) => {
                try {
                    console.info(`[CloudDSP] Received signed S3 MIDI URL for '${track}'.`);
                    console.info(`[CloudDSP] Starting S3 MIDI download for '${track}'.`);
                    const response = await fetch(url);
                    if (!response.ok) {
                        console.error(`[CloudDSP] S3 MIDI request failed for '${track}' (HTTP ${response.status}).`);
                        throw new Error(`MIDI download failed (${response.status}).`);
                    }

                    const midiBytes = await response.arrayBuffer();
                    console.info(`[CloudDSP] S3 MIDI download complete for '${track}' (${midiBytes.byteLength} bytes).`);
                    const isAdtofDrum = midiStates?.[track]?.extractor === 'adtof'
                        || track === 'drums';
                    const parsedMidi = await parseMidiFile(midiBytes.slice(0), timeSignature, { isAdtofDrum });
                    parsedUpdates[track] = parsedMidi;
                    originalUpdates[track] = await parseMidiFile(midiBytes.slice(0), timeSignature, { isAdtofDrum });
                    loadedUrlsRef.current.set(track, url);
                    const noteCount = parsedMidi?.midiData?.tracks?.reduce(
                        (count, midiTrack) => count + (midiTrack.notes?.length || 0),
                        0,
                    ) || 0;
                    console.info(`[CloudDSP] MIDI parse complete for '${track}' (${noteCount} notes).`);
                } catch (error) {
                    console.error(`[CloudDSP] Failed to load MIDI for '${track}':`, error);
                } finally {
                    inFlightTracksRef.current.delete(track);
                }
            }));

            if (resetVersion !== resetVersionRef.current || Object.keys(parsedUpdates).length === 0) {
                setIsMidiLoading(inFlightTracksRef.current.size > 0);
                return;
            }

            const nextParsed = { ...parsedMidiStemsRef.current, ...parsedUpdates };
            const nextOriginal = { ...originalMidiStemsRef.current, ...originalUpdates };
            parsedMidiStemsRef.current = nextParsed;
            originalMidiStemsRef.current = nextOriginal;
            setParsedMidiStems(nextParsed);
            setOriginalMidiStems(nextOriginal);

            setIsMidiLoading(inFlightTracksRef.current.size > 0);
        };

        fetchAndParse();
    }, [midiUrls, midiStates, jobId, timeSignature, audioCtxRef, midiSynthRefs, drumVoiceSynthRefs]);

    return { parsedMidiStems, setParsedMidiStems, originalMidiStems, isMidiLoading };
}
