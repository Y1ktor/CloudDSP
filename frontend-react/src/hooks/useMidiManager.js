import React from 'react';
import { SplendidGrandPiano, Soundfont } from 'smplr';
import { parseMidiFile, determineMasterBpm } from '../utils/MidiParser';

/**
 * Fetches and parses Basic Pitch MIDI files as their presigned URLs arrive over
 * the WebSocket. Each stem is processed once, allowing the editor to become
 * available incrementally rather than waiting for every Basic Pitch job.
 */
export function useMidiManager(midiUrls, timeSignature, setBpm, setOriginalBpm, audioCtxRef, globalSynthRef, guitarSynthRef, bassSynthRef) {
    const [parsedMidiStems, setParsedMidiStems] = React.useState({});
    const [originalMidiStems, setOriginalMidiStems] = React.useState({});
    const [isMidiLoading, setIsMidiLoading] = React.useState(false);
    const parsedMidiStemsRef = React.useRef({});
    const originalMidiStemsRef = React.useRef({});
    const loadedUrlsRef = React.useRef(new Map());
    const inFlightTracksRef = React.useRef(new Set());
    const resetVersionRef = React.useRef(0);

    React.useEffect(() => {
        // A new upload clears midiUrls before the first completion event. Reset
        // all editor data so results from an earlier job cannot be mixed in.
        if (!midiUrls) {
            resetVersionRef.current += 1;
            loadedUrlsRef.current.clear();
            inFlightTracksRef.current.clear();
            parsedMidiStemsRef.current = {};
            originalMidiStemsRef.current = {};
            setParsedMidiStems({});
            setOriginalMidiStems({});
            setIsMidiLoading(false);
            return;
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

            const parsedUpdates = {};
            const originalUpdates = {};

            await Promise.all(entriesToLoad.map(async ([track, url]) => {
                try {
                    const response = await fetch(url);
                    if (!response.ok) {
                        throw new Error(`MIDI download failed (${response.status}).`);
                    }

                    const midiBytes = await response.arrayBuffer();
                    parsedUpdates[track] = await parseMidiFile(midiBytes.slice(0), timeSignature);
                    originalUpdates[track] = await parseMidiFile(midiBytes.slice(0), timeSignature);
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

            const bestBpm = determineMasterBpm(nextParsed);
            if (bestBpm) {
                setOriginalBpm(bestBpm);
                setBpm(bestBpm);
            }
            setIsMidiLoading(inFlightTracksRef.current.size > 0);
        };

        fetchAndParse();
    }, [midiUrls, timeSignature, setBpm, setOriginalBpm, audioCtxRef, globalSynthRef, guitarSynthRef, bassSynthRef]);

    return { parsedMidiStems, setParsedMidiStems, originalMidiStems, isMidiLoading };
}
