import React from 'react';
import { Sampler, SplendidGrandPiano, Soundfont } from 'smplr';
import { parseMidiFile } from '../utils/MidiParser';
import {
    ADTOF_DRUM_SAMPLE_BUFFERS,
    ADTOF_DRUM_SAMPLER_OPTIONS,
    ADTOF_DRUM_VOICES
} from '../utils/DrumMidi';

function midiPitchesForTrack(trackData) {
    const pitches = new Set();
    trackData?.midiData?.tracks?.forEach((midiTrack) => {
        midiTrack.notes?.forEach((note) => {
            if (Number.isFinite(note.midi)) pitches.add(note.midi);
        });
    });
    return [...pitches].sort((left, right) => left - right);
}

/**
 * Job snapshots intentionally contain fresh presigned query strings. MIDI
 * state must be keyed by the stable private object path rather than that
 * disposable signature, otherwise each poll re-downloads and re-parses the
 * same MIDI file.
 */
function remoteMidiKey(url) {
    try {
        const parsedUrl = new URL(url);
        return `remote:${parsedUrl.host}${decodeURIComponent(parsedUrl.pathname)}`;
    } catch {
        return `remote:${String(url).split('?')[0]}`;
    }
}

function createMelodicSynth(audioContext, trackName, trackData) {
    if (trackName === 'guitar') {
        // Soundfont loads an instrument bank as a unit. FluidR3 is materially
        // smaller than smplr's default MusyngKite bank while retaining a real
        // guitar timbre, which is a better browser-memory trade-off here.
        return new Soundfont(audioContext, {
            instrument: 'acoustic_guitar_nylon',
            kit: 'FluidR3_GM',
        });
    }
    if (trackName === 'bass') {
        return new Soundfont(audioContext, {
            instrument: 'acoustic_bass',
            kit: 'FluidR3_GM',
        });
    }

    // A full Splendid Grand Piano bank is large. Generated MIDI normally uses
    // a small subset of pitches, so request only the sample regions needed by
    // this track. Do not allocate an instrument at all until MIDI mode is on.
    const notes = midiPitchesForTrack(trackData);
    return new SplendidGrandPiano(audioContext, notes.length > 0 ? {
        notesToLoad: {
            notes,
            velocityRange: [0, 127],
        },
    } : undefined);
}

function ensureMelodicSynth(midiSynthRefs, audioContext, trackName, trackData) {
    const existingRef = midiSynthRefs.current.get(trackName);
    if (existingRef?.current) return existingRef;

    const synthRef = { current: createMelodicSynth(audioContext, trackName, trackData) };
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
 * Keep the original MIDI in its compact wire representation for revert/undo.
 * A parsed Tone.js Midi object contains a large JavaScript graph for every
 * note. Keeping a second parsed copy for every stem doubled that cost even
 * before a user opened the editor. The binary snapshot is immutable and is
 * reconstructed only when the user actually asks to undo or revert.
 */
function createOriginalMidiSnapshot(parsedMidi, midiBytes) {
    const { midiData: _parsedMidiData, ...metadata } = parsedMidi;
    return {
        ...metadata,
        binarySnapshot: midiBytes.slice(0),
    };
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
    drumVoiceSynthRefs,
    releaseInstrument,
    resetInstruments
) {
    const [parsedMidiStems, setParsedMidiStemsState] = React.useState({});
    const [originalMidiStems, setOriginalMidiStems] = React.useState({});
    const [isMidiLoading, setIsMidiLoading] = React.useState(false);
    const parsedMidiStemsRef = React.useRef({});
    const originalMidiStemsRef = React.useRef({});
    // `loadedUrlsRef` stores stable S3 object keys, never expiring presigned
    // URLs. The in-flight map also protects a new job from an older queued
    // task deleting its state after the user switches history entries.
    const loadedUrlsRef = React.useRef(new Map());
    const inFlightTracksRef = React.useRef(new Map());
    const midiLoadControllersRef = React.useRef(new Map());
    const midiLoadQueueRef = React.useRef(Promise.resolve());
    const midiLoadSequenceRef = React.useRef(0);
    const loadingInstrumentsRef = React.useRef(new Map());
    const instrumentLoadQueueRef = React.useRef(Promise.resolve());
    const instrumentLoadSequenceRef = React.useRef(0);
    const resetVersionRef = React.useRef(0);
    const previousJobIdRef = React.useRef(jobId);
    const [playbackInstrumentStatus, setPlaybackInstrumentStatus] = React.useState({});

    // The editor supplies an object replacement after a mutation. Keep the
    // non-rendering ref in lockstep so a later MIDI toggle builds a sampled
    // instrument from the edited notes, not a stale pre-edit snapshot.
    const setParsedMidiStems = React.useCallback((nextValue) => {
        setParsedMidiStemsState((current) => {
            const resolved = typeof nextValue === 'function' ? nextValue(current) : nextValue;
            const safeValue = resolved && typeof resolved === 'object' ? resolved : {};
            parsedMidiStemsRef.current = safeValue;
            return safeValue;
        });
    }, []);

    const ensurePlaybackInstrument = React.useCallback((trackName) => {
        const trackData = parsedMidiStemsRef.current[trackName];
        if (!trackData || !trackName) return false;

        if (!audioCtxRef.current) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioCtxRef.current = new AudioContext();
        }

        if (loadingInstrumentsRef.current.has(trackName)) return true;

        const loadToken = `${resetVersionRef.current}:${trackName}:${instrumentLoadSequenceRef.current += 1}`;
        const resetVersion = resetVersionRef.current;
        loadingInstrumentsRef.current.set(trackName, loadToken);
        setPlaybackInstrumentStatus((current) => ({ ...current, [trackName]: 'loading' }));

        // smplr starts network fetch/decode work as soon as an instrument is
        // constructed. Queue construction itself, not just `ready`, so one
        // central MIDI toggle cannot decode several complete sample banks in
        // parallel and recreate Safari's native-memory spike.
        instrumentLoadQueueRef.current = instrumentLoadQueueRef.current
            .catch(() => undefined)
            .then(async () => {
                const isCurrentLoad = () => (
                    resetVersion === resetVersionRef.current
                    && loadingInstrumentsRef.current.get(trackName) === loadToken
                );
                if (!isCurrentLoad()) return;

                const currentTrackData = parsedMidiStemsRef.current[trackName];
                const audioContext = audioCtxRef.current;
                if (!currentTrackData || !audioContext) {
                    if (loadingInstrumentsRef.current.get(trackName) === loadToken) {
                        loadingInstrumentsRef.current.delete(trackName);
                    }
                    return;
                }
                const isAdtofDrum = currentTrackData.isAdtofDrum === true || trackName === 'drums';
                const instrumentRefs = isAdtofDrum
                    ? [...ensureDrumVoiceSynths(drumVoiceSynthRefs, audioContext, trackName).values()]
                    : [ensureMelodicSynth(midiSynthRefs, audioContext, trackName, currentTrackData)];

                try {
                    await Promise.all(instrumentRefs.map((instrumentRef) => instrumentRef.current?.ready));
                    if (!isCurrentLoad()) return;
                    setPlaybackInstrumentStatus((current) => ({ ...current, [trackName]: 'ready' }));
                } catch (error) {
                    if (!isCurrentLoad()) return;
                    console.error(`[CloudDSP] MIDI instrument load failed for '${trackName}':`, error);
                    setPlaybackInstrumentStatus((current) => ({ ...current, [trackName]: 'failed' }));
                } finally {
                    if (loadingInstrumentsRef.current.get(trackName) === loadToken) {
                        loadingInstrumentsRef.current.delete(trackName);
                    }
                }
            });

        return true;
    }, [audioCtxRef, midiSynthRefs, drumVoiceSynthRefs]);

    const releasePlaybackInstrument = React.useCallback((trackName) => {
        if (!trackName) return;
        loadingInstrumentsRef.current.delete(trackName);
        releaseInstrument?.(trackName);
        setPlaybackInstrumentStatus((current) => {
            if (!Object.hasOwn(current, trackName)) return current;
            const { [trackName]: _released, ...remaining } = current;
            return remaining;
        });
    }, [releaseInstrument]);

    React.useEffect(() => {
        // A new upload normally clears midiUrls, but reset explicitly by job
        // ID as well so an old completed job can never suppress the first MIDI
        // fetch for the next one.
        const jobChanged = previousJobIdRef.current !== jobId;
        if (jobChanged || !midiUrls) {
            previousJobIdRef.current = jobId;
            resetVersionRef.current += 1;
            midiLoadControllersRef.current.forEach((controller) => controller.abort());
            midiLoadControllersRef.current.clear();
            loadedUrlsRef.current.clear();
            inFlightTracksRef.current.clear();
            loadingInstrumentsRef.current.clear();
            resetInstruments?.();
            parsedMidiStemsRef.current = {};
            originalMidiStemsRef.current = {};
            setParsedMidiStems({});
            setOriginalMidiStems({});
            setIsMidiLoading(false);
            setPlaybackInstrumentStatus({});
            if (!midiUrls) return;
        }

        const entriesToLoad = Object.entries(midiUrls).flatMap(([track, url]) => {
            if (!url) return [];
            const artifactKey = remoteMidiKey(url);
            const inFlight = inFlightTracksRef.current.get(track);
            if (
                loadedUrlsRef.current.get(track) === artifactKey
                || inFlight?.artifactKey === artifactKey
            ) {
                return [];
            }
            return [{ track, url, artifactKey }];
        });
        if (entriesToLoad.length === 0) return;

        const resetVersion = resetVersionRef.current;
        const scheduledEntries = entriesToLoad.map((entry) => {
            const token = `${resetVersion}:${entry.track}:${midiLoadSequenceRef.current += 1}`;
            const scheduledEntry = { ...entry, token };
            inFlightTracksRef.current.set(entry.track, scheduledEntry);
            return scheduledEntry;
        });
        setIsMidiLoading(true);

        const loadMidi = async ({ track, url, artifactKey, token }) => {
            const controller = new AbortController();
            midiLoadControllersRef.current.set(token, controller);
            const isCurrentLoad = () => (
                resetVersion === resetVersionRef.current
                && !controller.signal.aborted
                && inFlightTracksRef.current.get(track)?.token === token
            );

            try {
                // Queue entries can belong to a snapshot that was superseded
                // before it reached the loader. Do not begin a stale fetch.
                if (!isCurrentLoad()) return;
                console.info(`[CloudDSP] Received signed S3 MIDI URL for '${track}'.`);
                console.info(`[CloudDSP] Starting S3 MIDI download for '${track}'.`);
                // The compact original snapshot and editable MIDI graph are
                // the intentional cache; avoid a second raw-response cache
                // entry for every generated artifact.
                const response = await fetch(url, {
                    signal: controller.signal,
                    cache: 'no-store',
                });
                if (!response.ok) {
                    console.error(`[CloudDSP] S3 MIDI request failed for '${track}' (HTTP ${response.status}).`);
                    throw new Error(`MIDI download failed (${response.status}).`);
                }

                const midiBytes = await response.arrayBuffer();
                console.info(`[CloudDSP] S3 MIDI download complete for '${track}' (${midiBytes.byteLength} bytes).`);
                if (!isCurrentLoad()) return;

                const isAdtofDrum = midiStates?.[track]?.extractor === 'adtof'
                    || track === 'drums';
                const parsedMidi = await parseMidiFile(midiBytes, timeSignature, { isAdtofDrum });
                if (!isCurrentLoad()) return;

                const originalSnapshot = createOriginalMidiSnapshot(parsedMidi, midiBytes);
                const nextParsed = { ...parsedMidiStemsRef.current, [track]: parsedMidi };
                const nextOriginal = { ...originalMidiStemsRef.current, [track]: originalSnapshot };
                parsedMidiStemsRef.current = nextParsed;
                originalMidiStemsRef.current = nextOriginal;
                setParsedMidiStems(nextParsed);
                setOriginalMidiStems(nextOriginal);
                loadedUrlsRef.current.set(track, artifactKey);

                const noteCount = parsedMidi?.midiData?.tracks?.reduce(
                    (count, midiTrack) => count + (midiTrack.notes?.length || 0),
                    0,
                ) || 0;
                console.info(`[CloudDSP] MIDI parse complete for '${track}' (${noteCount} notes).`);
            } catch (error) {
                if (controller.signal.aborted || error?.name === 'AbortError') return;
                console.error(`[CloudDSP] Failed to load MIDI for '${track}':`, error);
            } finally {
                midiLoadControllersRef.current.delete(token);
                if (inFlightTracksRef.current.get(track)?.token === token) {
                    inFlightTracksRef.current.delete(track);
                    setIsMidiLoading(inFlightTracksRef.current.size > 0);
                }
            }
        };

        // Parsing a dense generated MIDI creates a large object graph. Like
        // audio decode, one persistent queue prevents a history snapshot with
        // many fresh artifacts from multiplying that peak by the stem count.
        scheduledEntries.forEach((entry) => {
            midiLoadQueueRef.current = midiLoadQueueRef.current
                .catch(() => undefined)
                .then(() => loadMidi(entry));
        });
    }, [midiUrls, midiStates, jobId, timeSignature, resetInstruments, setParsedMidiStems]);

    React.useEffect(() => () => {
        resetVersionRef.current += 1;
        midiLoadControllersRef.current.forEach((controller) => controller.abort());
        midiLoadControllersRef.current.clear();
        inFlightTracksRef.current.clear();
    }, []);

    return {
        parsedMidiStems,
        setParsedMidiStems,
        originalMidiStems,
        isMidiLoading,
        playbackInstrumentStatus,
        ensurePlaybackInstrument,
        releasePlaybackInstrument,
    };
}
