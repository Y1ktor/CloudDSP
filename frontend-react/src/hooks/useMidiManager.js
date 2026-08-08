import React from 'react';
import { Sampler, SplendidGrandPiano, Soundfont } from 'smplr';
import { parseMidiFile } from '../utils/MidiParser';
import { ADTOF_DRUM_SAMPLER_OPTIONS } from '../utils/DrumMidi';

/**
 * Fetches and parses generated MIDI files as their presigned URLs arrive.
 * Tempo is intentionally not inferred from MIDI headers: the durable backend
 * job owns tempo selection and passes it to the editor separately.
 */
export function useMidiManager(midiUrls, midiStates, jobId, timeSignature, audioCtxRef, globalSynthRef, guitarSynthRef, bassSynthRef, drumSynthRef) {
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
            if (!globalSynthRef.current) {
                globalSynthRef.current = new SplendidGrandPiano(audioCtxRef.current);
            }
            if (!guitarSynthRef.current) {
                guitarSynthRef.current = new Soundfont(audioCtxRef.current, { instrument: 'acoustic_guitar_nylon' });
            }
            if (!bassSynthRef.current) {
                bassSynthRef.current = new Soundfont(audioCtxRef.current, { instrument: 'acoustic_bass' });
            }
            if (entriesToLoad.some(([track]) => track === 'drums') && !drumSynthRef.current) {
                // Begin fetching the five ADTOF drum samples while the MIDI
                // file is downloading, so first playback does not fall back
                // to a piano or require the full TR-808 sample collection.
                const drumSampler = Sampler(audioCtxRef.current, ADTOF_DRUM_SAMPLER_OPTIONS);
                drumSampler.ready
                    .then(() => console.info('CloudDSP drum samples are ready.'))
                    .catch((error) => console.error('CloudDSP drum sample load failed:', error));
                drumSynthRef.current = drumSampler;
            }

            const parsedUpdates = {};
            const originalUpdates = {};

            await Promise.all(entriesToLoad.map(async ([track, url]) => {
                try {
                    console.info(`Downloading generated MIDI for ${track}.`);
                    const response = await fetch(url);
                    if (!response.ok) {
                        throw new Error(`MIDI download failed (${response.status}).`);
                    }

                    const midiBytes = await response.arrayBuffer();
                    const isAdtofDrum = midiStates?.[track]?.extractor === 'adtof'
                        || track === 'drums';
                    parsedUpdates[track] = await parseMidiFile(midiBytes.slice(0), timeSignature, { isAdtofDrum });
                    originalUpdates[track] = await parseMidiFile(midiBytes.slice(0), timeSignature, { isAdtofDrum });
                    loadedUrlsRef.current.set(track, url);
                    console.log(`Parsed backend MIDI for ${track}.`);
                } catch (error) {
                    console.error(`Failed to load MIDI for ${track}:`, error);
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
    }, [midiUrls, midiStates, jobId, timeSignature, audioCtxRef, globalSynthRef, guitarSynthRef, bassSynthRef, drumSynthRef]);

    return { parsedMidiStems, setParsedMidiStems, originalMidiStems, isMidiLoading };
}
