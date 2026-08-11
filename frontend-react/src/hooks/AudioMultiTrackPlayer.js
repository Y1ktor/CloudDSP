/**
 * Synchronized browser-side stem transport.
 *
 * HTMLMediaElement instances have independent buffering and start clocks. That
 * is unsuitable for a DAW: a browser may resolve each play() call at a
 * different time, especially after pause/resume. This transport decodes each
 * ready stem into an AudioBuffer and schedules every source against one shared
 * AudioContext time instead.
 */
import React from 'react';
import { clampTrackGainDb, dbToLinearGain } from '../utils/MidiPlayback';

const START_LEAD_SECONDS = 0.08;
const MIX_RAMP_SECONDS = 0.005;
// `decodeAudioData` temporarily needs both the encoded bytes and its decoded
// PCM output. The hook owns one queue across *all* source-descriptor updates,
// so stems that arrive in separate snapshots cannot start independent decode
// queues and create a Safari memory spike.
// The only React consumer is a whole-second text readout. Keep that state at
// one Hz; every-frame positioning comes from transportRef and never needs a
// workspace render.
const PROGRESS_COMMIT_INTERVAL_MS = 1000;
const MEBIBYTE = 1024 * 1024;
const AUDIO_MEMORY_WARNING_BYTES = 512 * MEBIBYTE;

function localFileKey(file) {
    return `file:${file.name}:${file.size}:${file.lastModified}`;
}

/**
 * Presigned S3 URLs change on every job snapshot even when they identify the
 * same object. Audio buffers must be keyed by the object path, not by the
 * temporary signature, otherwise polling causes needless re-downloads and
 * interrupts a load already in progress.
 */
function remoteAudioKey(url) {
    try {
        const parsedUrl = new URL(url);
        return `remote:${parsedUrl.host}${decodeURIComponent(parsedUrl.pathname)}`;
    } catch {
        // A normal URL is expected, but retain a deterministic fallback for a
        // development URL that cannot be parsed by URL().
        return `remote:${url.split('?')[0]}`;
    }
}

function audioBufferByteLength(buffer) {
    if (!buffer) return 0;
    return buffer.length * buffer.numberOfChannels * Float32Array.BYTES_PER_ELEMENT;
}

function formatMebibytes(bytes) {
    return `${(bytes / MEBIBYTE).toFixed(1)} MiB`;
}

/**
 * @param {Object} stemUrls ready stem URLs keyed by stem name
 * @param {File|null} file locally selected original file
 * @param {Object} activeMidiTracks MIDI-synthesis mode state by track
 * @param {string|null} sourceUrl signed URL for a restored job's original file
 * @param {string|null} jobId durable job identity
 */
export function useAudioMultiTrackPlayer(stemUrls, file, activeMidiTracks = {}, sourceUrl = null, jobId = null) {
    const audioCtxRef = React.useRef(null);
    const buffersRef = React.useRef(new Map());
    const bufferKeysRef = React.useRef(new Map());
    const gainNodesRef = React.useRef(new Map());
    const sourcesRef = React.useRef(new Map());
    const loadControllersRef = React.useRef(new Map());
    const inFlightKeysRef = React.useRef(new Map());
    const inFlightAudioBytesRef = React.useRef(new Map());
    const currentDescriptorsRef = React.useRef({});
    const expectedTracksRef = React.useRef([]);
    const loadGenerationRef = React.useRef(0);
    const loadSequenceRef = React.useRef(0);
    const loadQueueRef = React.useRef(Promise.resolve());
    const isMountedRef = React.useRef(true);
    const durationRef = React.useRef(0);
    const transportStartTimeRef = React.useRef(null);
    const transportOffsetRef = React.useRef(0);
    const transportRateRef = React.useRef(1);
    const isPlayingRef = React.useRef(false);
    const transportPositionRef = React.useRef(0);
    // Direct visual and MIDI scheduler consumers must read this ref instead of
    // subscribing to a React update for every animation frame.
    const transportRef = React.useRef({
        position: 0,
        offset: 0,
        startTime: null,
        rate: 1,
        isPlaying: false,
        revision: 0,
    });
    const lastProgressCommitRef = React.useRef(0);
    const stopPlaybackRef = React.useRef(null);
    const schedulePlaybackRef = React.useRef(null);
    const peakDecodedAudioBytesRef = React.useRef(0);
    const memoryWarningGenerationRef = React.useRef(null);
    const mutedTracksRef = React.useRef({});
    const soloedTracksRef = React.useRef({});
    const activeMidiTracksRef = React.useRef(activeMidiTracks);
    const trackGainsDbRef = React.useRef({});

    const [isPlaying, setIsPlaying] = React.useState(false);
    const [progress, setProgress] = React.useState(0);
    const [duration, setDurationState] = React.useState(0);
    const [mutedTracks, setMutedTracks] = React.useState({});
    const [soloedTracks, setSoloedTracks] = React.useState({});
    const [trackGainsDb, setTrackGainsDb] = React.useState({});
    const [isCycling, setIsCycling] = React.useState(false);
    const [cycleRegion, setCycleRegion] = React.useState({ startBar: 0, endBar: 2 });
    const [bpm, setBpm] = React.useState(120.0);
    const [originalBpm, setOriginalBpm] = React.useState(null);
    const [timeSignature, setTimeSignature] = React.useState('4/4');
    const [audioLoadState, setAudioLoadState] = React.useState({});
    const [transportStartTime, setTransportStartTime] = React.useState(null);
    const [audioMemoryMetrics, setAudioMemoryMetrics] = React.useState({
        decodedBytes: 0,
        inFlightBytes: 0,
        peakDecodedBytes: 0,
        decodedTrackCount: 0,
    });
    const dragStateRef = React.useRef({ isDragging: false, mode: null, startY: 0, startBpm: 0 });

    // Register this before the loading effects. React Strict Mode tears down
    // and recreates effects in development; the next load pass must see the
    // player as mounted rather than treating every request as stale.
    React.useEffect(() => {
        isMountedRef.current = true;
        return () => {
            isMountedRef.current = false;
        };
    }, []);

    const sourceDescriptors = React.useMemo(() => {
        const descriptors = {};
        if (file) {
            descriptors.Original = { kind: 'file', source: file, key: localFileKey(file) };
        } else if (sourceUrl) {
            descriptors.Original = { kind: 'url', source: sourceUrl, key: remoteAudioKey(sourceUrl) };
        }
        Object.entries(stemUrls || {}).forEach(([trackName, url]) => {
            if (url) descriptors[trackName] = { kind: 'url', source: url, key: remoteAudioKey(url) };
        });
        return descriptors;
    }, [file, sourceUrl, stemUrls]);
    const sourceTrackSignature = Object.keys(sourceDescriptors).sort().join('|');
    currentDescriptorsRef.current = sourceDescriptors;

    const setDuration = React.useCallback((nextDuration) => {
        setDurationState((currentDuration) => {
            const resolvedDuration = typeof nextDuration === 'function'
                ? nextDuration(currentDuration)
                : nextDuration;
            const safeDuration = Number.isFinite(resolvedDuration) && resolvedDuration > 0
                ? resolvedDuration
                : 0;
            durationRef.current = safeDuration;
            return safeDuration;
        });
    }, []);

    const updateTransportSnapshot = (nextValues, incrementRevision = false) => {
        Object.assign(transportRef.current, nextValues);
        if (incrementRevision) transportRef.current.revision += 1;
        transportPositionRef.current = transportRef.current.position;
    };

    const updateTransportPosition = (position) => {
        const safePosition = Number.isFinite(position) ? Math.max(0, position) : 0;
        transportRef.current.position = safePosition;
        transportPositionRef.current = safePosition;
        return safePosition;
    };

    const commitProgress = (position, force = false) => {
        const safePosition = updateTransportPosition(position);
        const now = performance.now();
        if (force || now - lastProgressCommitRef.current >= PROGRESS_COMMIT_INTERVAL_MS) {
            lastProgressCommitRef.current = now;
            if (isMountedRef.current) setProgress(safePosition);
        }
        return safePosition;
    };

    const publishAudioMemoryMetrics = (reason) => {
        let decodedBytes = 0;
        buffersRef.current.forEach((buffer) => {
            decodedBytes += audioBufferByteLength(buffer);
        });
        const inFlightBytes = Array.from(inFlightAudioBytesRef.current.values())
            .reduce((total, value) => total + value, 0);
        peakDecodedAudioBytesRef.current = Math.max(peakDecodedAudioBytesRef.current, decodedBytes);
        const nextMetrics = {
            decodedBytes,
            inFlightBytes,
            peakDecodedBytes: peakDecodedAudioBytesRef.current,
            decodedTrackCount: buffersRef.current.size,
        };
        if (isMountedRef.current) setAudioMemoryMetrics(nextMetrics);
        console.info(
            `[CloudDSP] Audio memory (${reason}): ${formatMebibytes(decodedBytes)} decoded across `
            + `${buffersRef.current.size} track(s), ${formatMebibytes(inFlightBytes)} temporary.`,
        );
        if (
            decodedBytes >= AUDIO_MEMORY_WARNING_BYTES
            && memoryWarningGenerationRef.current !== loadGenerationRef.current
        ) {
            memoryWarningGenerationRef.current = loadGenerationRef.current;
            console.warn(
                `[CloudDSP] Decoded audio buffers use ${formatMebibytes(decodedBytes)}. `
                + 'This project retains full PCM buffers to keep stem playback sample-synchronized.',
            );
        }
    };

    const ensureAudioContext = () => {
        if (!audioCtxRef.current) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioCtxRef.current = new AudioContext();
        }
        return audioCtxRef.current;
    };

    const currentTransportPosition = React.useCallback(() => {
        const context = audioCtxRef.current;
        const startTime = transportStartTimeRef.current;
        if (!isPlayingRef.current || !context || startTime === null) return transportOffsetRef.current;
        const elapsed = Math.max(0, context.currentTime - startTime);
        return Math.min(durationRef.current || Infinity, transportOffsetRef.current + elapsed * transportRateRef.current);
    }, []);

    const getTransportPosition = React.useCallback(() => {
        const position = currentTransportPosition();
        updateTransportPosition(position);
        return position;
    }, [currentTransportPosition]);

    const applyMixState = (
        nextMuted = mutedTracksRef.current,
        nextSoloed = soloedTracksRef.current,
        nextMidi = activeMidiTracksRef.current,
        nextGainsDb = trackGainsDbRef.current
    ) => {
        const context = audioCtxRef.current;
        if (!context) return;
        const hasSolo = Object.values(nextSoloed).some(Boolean);
        const now = context.currentTime;
        gainNodesRef.current.forEach((gainNode, trackName) => {
            const shouldMute = nextMidi[trackName]
                || (hasSolo ? !nextSoloed[trackName] : Boolean(nextMuted[trackName]));
            const targetGain = shouldMute ? 0 : dbToLinearGain(nextGainsDb[trackName]);
            gainNode.gain.cancelScheduledValues(now);
            gainNode.gain.setValueAtTime(gainNode.gain.value, now);
            gainNode.gain.linearRampToValueAtTime(targetGain, now + MIX_RAMP_SECONDS);
        });
    };

    const ensureTrackGain = (trackName, context) => {
        const existing = gainNodesRef.current.get(trackName);
        if (existing) return existing;
        const gainNode = context.createGain();
        gainNode.gain.value = 0;
        gainNode.connect(context.destination);
        gainNodesRef.current.set(trackName, gainNode);
        applyMixState();
        return gainNode;
    };

    const stopActiveSources = () => {
        sourcesRef.current.forEach((source) => {
            source.onended = null;
            try { source.stop(); } catch { /* Source may already be stopped. */ }
            try { source.disconnect(); } catch { /* Source may already be disconnected. */ }
            // Source nodes can otherwise keep their large AudioBuffer alive
            // until the engine's next native graph cleanup pass.
            try { source.buffer = null; } catch { /* A stopped source may reject this in older engines. */ }
        });
        sourcesRef.current.clear();
    };

    const stopPlayback = (resetToStart = false) => {
        if (isPlayingRef.current) {
            transportOffsetRef.current = resetToStart ? 0 : currentTransportPosition();
        } else if (resetToStart) {
            transportOffsetRef.current = 0;
        }
        stopActiveSources();
        transportStartTimeRef.current = null;
        setTransportStartTime(null);
        isPlayingRef.current = false;
        setIsPlaying(false);
        updateTransportSnapshot({
            position: transportOffsetRef.current,
            offset: transportOffsetRef.current,
            startTime: null,
            rate: transportRateRef.current,
            isPlaying: false,
        }, true);
        commitProgress(transportOffsetRef.current, true);
    };
    stopPlaybackRef.current = stopPlayback;

    const schedulePlayback = (offset) => {
        const context = ensureAudioContext();
        const trackNames = expectedTracksRef.current;
        const startTime = context.currentTime + START_LEAD_SECONDS;
        const masterTrack = buffersRef.current.has('Original') ? 'Original' : trackNames[0];

        stopActiveSources();
        transportOffsetRef.current = Math.max(0, Math.min(offset, durationRef.current));
        transportStartTimeRef.current = startTime;
        setTransportStartTime(startTime);
        updateTransportSnapshot({
            position: transportOffsetRef.current,
            offset: transportOffsetRef.current,
            startTime,
            rate: transportRateRef.current,
            isPlaying: true,
        }, true);

        trackNames.forEach((trackName) => {
            const buffer = buffersRef.current.get(trackName);
            if (!buffer || transportOffsetRef.current >= buffer.duration) return;
            const source = context.createBufferSource();
            source.buffer = buffer;
            source.playbackRate.setValueAtTime(transportRateRef.current, startTime);
            source.connect(ensureTrackGain(trackName, context));
            if (trackName === masterTrack) {
                source.onended = () => {
                    if (sourcesRef.current.get(trackName) !== source) return;
                    stopActiveSources();
                    transportOffsetRef.current = 0;
                    transportStartTimeRef.current = null;
                    setTransportStartTime(null);
                    isPlayingRef.current = false;
                    setIsPlaying(false);
                    updateTransportSnapshot({
                        position: 0,
                        offset: 0,
                        startTime: null,
                        rate: transportRateRef.current,
                        isPlaying: false,
                    }, true);
                    commitProgress(0, true);
                };
            }
            source.start(startTime, transportOffsetRef.current);
            sourcesRef.current.set(trackName, source);
        });

        applyMixState();
        isPlayingRef.current = true;
        setIsPlaying(true);
        commitProgress(transportOffsetRef.current, true);
    };
    schedulePlaybackRef.current = schedulePlayback;

    // A new upload or saved job must never reuse a prior job's buffers or
    // transport state. Source additions within the same job retain already
    // decoded tracks and simply expand the ready set.
    React.useEffect(() => {
        loadGenerationRef.current += 1;
        stopPlayback(true);
        buffersRef.current.clear();
        bufferKeysRef.current.clear();
        loadControllersRef.current.forEach(({ controller }) => controller.abort());
        loadControllersRef.current.clear();
        inFlightKeysRef.current.clear();
        inFlightAudioBytesRef.current.clear();
        peakDecodedAudioBytesRef.current = 0;
        memoryWarningGenerationRef.current = null;
        gainNodesRef.current.forEach((gainNode) => gainNode.disconnect());
        gainNodesRef.current.clear();
        const nextMuted = sourceTrackSignature.includes('Original') && sourceTrackSignature !== 'Original'
            ? { Original: true }
            : {};
        mutedTracksRef.current = nextMuted;
        soloedTracksRef.current = {};
        trackGainsDbRef.current = {};
        setMutedTracks(nextMuted);
        setSoloedTracks({});
        setTrackGainsDb({});
        setDuration(0);
        setAudioLoadState({});
        setAudioMemoryMetrics({
            decodedBytes: 0,
            inFlightBytes: 0,
            peakDecodedBytes: 0,
            decodedTrackCount: 0,
        });
    // Deliberately reset only at a durable source boundary, not when each
    // individual stem arrives during one active processing job.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [file, jobId]);

    React.useEffect(() => {
        expectedTracksRef.current = Object.keys(sourceDescriptors);
        if (sourceTrackSignature.includes('Original') && sourceTrackSignature !== 'Original' && !mutedTracksRef.current.Original) {
            const nextMuted = { ...mutedTracksRef.current, Original: true };
            mutedTracksRef.current = nextMuted;
            setMutedTracks(nextMuted);
            applyMixState(nextMuted);
        }
    }, [sourceDescriptors, sourceTrackSignature]);

    React.useEffect(() => {
        const loadGeneration = loadGenerationRef.current;

        // A stem can disappear while it is downloading (for example when a
        // user leaves a history job). Abort it before it reaches the decoder;
        // File.arrayBuffer() itself is not abortable, so the load also checks
        // this state again immediately before decoding.
        loadControllersRef.current.forEach((entry, loadToken) => {
            const currentDescriptor = sourceDescriptors[entry.trackName];
            if (!currentDescriptor || currentDescriptor.key !== entry.key) {
                entry.controller.abort();
                loadControllersRef.current.delete(loadToken);
                inFlightAudioBytesRef.current.delete(loadToken);
                if (inFlightKeysRef.current.get(entry.trackName) === entry.key) {
                    inFlightKeysRef.current.delete(entry.trackName);
                }
            }
        });

        const entriesToLoad = Object.entries(sourceDescriptors).filter(([trackName, descriptor]) => (
            bufferKeysRef.current.get(trackName) !== descriptor.key
            && inFlightKeysRef.current.get(trackName) !== descriptor.key
        ));

        let releasedTrack = false;
        bufferKeysRef.current.forEach((_, trackName) => {
            if (!sourceDescriptors[trackName]) {
                const activeSource = sourcesRef.current.get(trackName);
                if (activeSource) {
                    activeSource.onended = null;
                    try { activeSource.stop(); } catch { /* Already stopped. */ }
                    try { activeSource.disconnect(); } catch { /* Already disconnected. */ }
                    try { activeSource.buffer = null; } catch { /* Older engines may reject it. */ }
                    sourcesRef.current.delete(trackName);
                }
                buffersRef.current.delete(trackName);
                bufferKeysRef.current.delete(trackName);
                gainNodesRef.current.get(trackName)?.disconnect();
                gainNodesRef.current.delete(trackName);
                releasedTrack = true;
                if (isMountedRef.current) {
                    setAudioLoadState((current) => {
                        const { [trackName]: _removedTrack, ...remaining } = current;
                        return remaining;
                    });
                }
            }
        });
        if (releasedTrack) publishAudioMemoryMetrics('released removed track');

        if (entriesToLoad.length === 0) return undefined;

        entriesToLoad.forEach(([trackName, descriptor]) => {
            inFlightKeysRef.current.set(trackName, descriptor.key);
            if (isMountedRef.current) {
                setAudioLoadState((current) => ({ ...current, [trackName]: 'loading' }));
            }
        });

        const loadTrack = async ([trackName, descriptor]) => {
            const controller = new AbortController();
            const loadToken = `${loadGeneration}:${trackName}:${loadSequenceRef.current += 1}`;
            loadControllersRef.current.set(loadToken, {
                controller,
                trackName,
                key: descriptor.key,
            });
            let audioBytes = null;
            let trackedTemporaryBytes = false;
            const isCurrentLoad = () => (
                isMountedRef.current
                && !controller.signal.aborted
                && loadGeneration === loadGenerationRef.current
                && currentDescriptorsRef.current[trackName]?.key === descriptor.key
                && inFlightKeysRef.current.get(trackName) === descriptor.key
            );
            try {
                // Queued work may no longer belong to the displayed job by
                // the time the single-file loader reaches it.
                if (!isCurrentLoad()) return;
                if (descriptor.kind === 'file') {
                    console.info(`[CloudDSP] Starting local audio decode for '${trackName}'.`);
                    audioBytes = await descriptor.source.arrayBuffer();
                } else {
                    console.info(`[CloudDSP] Starting S3 audio download for '${trackName}'.`);
                    // The decoded AudioBuffer is the intentional current-job
                    // cache. Do not also retain the encoded S3 response in the
                    // browser's HTTP memory cache.
                    const response = await fetch(descriptor.source, {
                        signal: controller.signal,
                        cache: 'no-store',
                    });
                    if (!response.ok) {
                        throw new Error(`S3 audio request failed (HTTP ${response.status}).`);
                    }
                    audioBytes = await response.arrayBuffer();
                    console.info(`[CloudDSP] S3 audio download complete for '${trackName}' (${audioBytes.byteLength} bytes).`);
                }

                // Never decode bytes for a stale job or an unmounted player.
                // Safari cannot abort decodeAudioData once it starts, so this
                // guard avoids a particularly expensive orphaned decode.
                if (!isCurrentLoad()) return;
                inFlightAudioBytesRef.current.set(loadToken, audioBytes.byteLength);
                trackedTemporaryBytes = true;
                publishAudioMemoryMetrics(`downloaded '${trackName}'`);

                const context = ensureAudioContext();
                const audioBuffer = await context.decodeAudioData(audioBytes);
                // Drop the raw encoded copy as soon as Web Audio has produced
                // PCM. This makes the lifetime explicit for Safari's GC.
                audioBytes = null;
                inFlightAudioBytesRef.current.delete(loadToken);
                trackedTemporaryBytes = false;
                if (!isCurrentLoad()) return;
                buffersRef.current.set(trackName, audioBuffer);
                bufferKeysRef.current.set(trackName, descriptor.key);
                ensureTrackGain(trackName, context);
                setDuration((currentDuration) => Math.max(currentDuration, audioBuffer.duration));
                if (isMountedRef.current) {
                    setAudioLoadState((current) => ({ ...current, [trackName]: 'ready' }));
                }
                publishAudioMemoryMetrics(`decoded '${trackName}'`);
                console.info(`[CloudDSP] Audio decode complete for '${trackName}'; ready for synchronized playback.`);
            } catch (error) {
                if (controller.signal.aborted || error?.name === 'AbortError') return;
                console.error(`[CloudDSP] Could not prepare '${trackName}' for playback:`, error);
                if (isCurrentLoad()) {
                    setAudioLoadState((current) => ({ ...current, [trackName]: 'failed' }));
                }
            } finally {
                // The ArrayBuffer is intentionally not kept in a ref. Clear
                // both local and diagnostic references on every exit path.
                audioBytes = null;
                if (trackedTemporaryBytes || inFlightAudioBytesRef.current.has(loadToken)) {
                    inFlightAudioBytesRef.current.delete(loadToken);
                    publishAudioMemoryMetrics(`finished '${trackName}'`);
                }
                loadControllersRef.current.delete(loadToken);
                if (inFlightKeysRef.current.get(trackName) === descriptor.key) {
                    inFlightKeysRef.current.delete(trackName);
                }
            }
        };

        // Do not create one "concurrency = 1" worker per snapshot. Each
        // snapshot can add a new stem while an older snapshot still has a
        // queued decode, so all work must append to this one persistent chain.
        entriesToLoad.forEach((entry) => {
            loadQueueRef.current = loadQueueRef.current
                .catch(() => undefined)
                .then(() => loadTrack(entry));
        });
        return undefined;
    }, [sourceDescriptors, jobId, setDuration]);

    React.useEffect(() => {
        activeMidiTracksRef.current = activeMidiTracks;
        applyMixState(undefined, undefined, activeMidiTracks);
    }, [activeMidiTracks]);

    React.useEffect(() => {
        const rate = originalBpm && bpm ? Math.max(0.5, Math.min(4, bpm / originalBpm)) : 1;
        const context = audioCtxRef.current;
        if (isPlayingRef.current && context) {
            const offset = currentTransportPosition();
            transportOffsetRef.current = offset;
            transportStartTimeRef.current = context.currentTime;
            setTransportStartTime(context.currentTime);
            sourcesRef.current.forEach((source) => source.playbackRate.setValueAtTime(rate, context.currentTime));
            updateTransportSnapshot({
                position: offset,
                offset,
                startTime: context.currentTime,
                rate,
                isPlaying: true,
            }, true);
            commitProgress(offset, true);
        } else {
            updateTransportSnapshot({ rate });
        }
        transportRateRef.current = rate;
    }, [bpm, originalBpm, currentTransportPosition]);

    React.useEffect(() => {
        if (!isPlaying) return undefined;
        let frameId;
        const updateProgress = () => {
            const position = currentTransportPosition();
            // This write is intentionally cheap and does not render React.
            // Direct playhead and scheduler consumers read it on every frame.
            commitProgress(position);
            if (durationRef.current && position >= durationRef.current) {
                stopPlaybackRef.current?.(true);
                return;
            }

            if (isCycling) {
                const beatsPerBar = parseInt(timeSignature.split('/')[0], 10) || 4;
                const cycleEnd = (cycleRegion.endBar * beatsPerBar) / (bpm / 60);
                const cycleStart = (cycleRegion.startBar * beatsPerBar) / (bpm / 60);
                if (position >= cycleEnd) {
                    schedulePlaybackRef.current?.(cycleStart);
                    frameId = requestAnimationFrame(updateProgress);
                    return;
                }
            }

            frameId = requestAnimationFrame(updateProgress);
        };
        frameId = requestAnimationFrame(updateProgress);
        return () => cancelAnimationFrame(frameId);
    }, [isPlaying, duration, isCycling, cycleRegion, bpm, timeSignature, currentTransportPosition]);

    const markPlayerUnmounted = () => {
        isMountedRef.current = false;
        loadGenerationRef.current += 1;
    };

    const releaseAudioResources = () => {
        loadControllersRef.current.forEach(({ controller }) => controller.abort());
        loadControllersRef.current.clear();
        inFlightKeysRef.current.clear();
        inFlightAudioBytesRef.current.clear();
        stopActiveSources();
        buffersRef.current.clear();
        bufferKeysRef.current.clear();
        gainNodesRef.current.forEach((gainNode) => {
            try { gainNode.disconnect(); } catch { /* Already disconnected. */ }
        });
        gainNodesRef.current.clear();
        transportStartTimeRef.current = null;
        transportOffsetRef.current = 0;
        isPlayingRef.current = false;
        updateTransportSnapshot({
            position: 0,
            offset: 0,
            startTime: null,
            rate: transportRateRef.current,
            isPlaying: false,
        }, true);
        const context = audioCtxRef.current;
        audioCtxRef.current = null;
        if (context && context.state !== 'closed') {
            void context.close().catch(() => {
                // Browsers may already be tearing down the context.
            });
        }
    };

    React.useEffect(() => {
        return () => {
            // Cancel stale work before it can enter Safari's decoder and close
            // the context on an actual workspace unmount so native Web Audio
            // allocations are eligible for release immediately.
            markPlayerUnmounted();
            releaseAudioResources();
        };
    // Refs intentionally keep this cleanup stable across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const togglePlay = async () => {
        if (isPlayingRef.current) {
            stopPlayback(false);
            return;
        }

        const expectedTracks = expectedTracksRef.current;
        const unavailableTracks = expectedTracks.filter((trackName) => !buffersRef.current.has(trackName));
        if (expectedTracks.length === 0 || unavailableTracks.length > 0) {
            console.warn(
                `[CloudDSP] Waiting for synchronized audio buffers: ${unavailableTracks.join(', ') || 'no audio tracks available'}.`,
            );
            return;
        }

        const context = ensureAudioContext();
        // Calling resume inside the click handler preserves Safari's user-
        // gesture requirement. Source nodes are scheduled only after it is
        // running, against one common future AudioContext time.
        if (context.state === 'suspended') await context.resume();
        applyMixState();
        schedulePlayback(transportOffsetRef.current);
    };

    const seekTo = (time) => {
        const clampedTime = Math.max(0, Math.min(Number(time) || 0, durationRef.current));
        if (isPlayingRef.current) {
            schedulePlayback(clampedTime);
        } else {
            transportOffsetRef.current = clampedTime;
            updateTransportSnapshot({
                position: clampedTime,
                offset: clampedTime,
                startTime: null,
                rate: transportRateRef.current,
                isPlaying: false,
            }, true);
            commitProgress(clampedTime, true);
        }
    };

    const handleGoToBeginning = () => seekTo(0);
    const handleSeek = (event) => seekTo(event?.target?.value);

    const toggleMute = (trackName) => {
        const nextMuted = { ...mutedTracksRef.current, [trackName]: !mutedTracksRef.current[trackName] };
        mutedTracksRef.current = nextMuted;
        setMutedTracks(nextMuted);
        applyMixState(nextMuted);
    };

    const toggleSolo = (trackName) => {
        const nextSoloed = { ...soloedTracksRef.current, [trackName]: !soloedTracksRef.current[trackName] };
        soloedTracksRef.current = nextSoloed;
        setSoloedTracks(nextSoloed);
        applyMixState(undefined, nextSoloed);
    };

    const setTrackGainDb = (trackName, decibels) => {
        const nextGainsDb = {
            ...trackGainsDbRef.current,
            [trackName]: clampTrackGainDb(decibels),
        };
        trackGainsDbRef.current = nextGainsDb;
        setTrackGainsDb(nextGainsDb);
        applyMixState(undefined, undefined, undefined, nextGainsDb);
    };

    const handleBpmMouseDown = (event, mode) => {
        event.preventDefault();
        dragStateRef.current = { isDragging: true, mode, startY: event.clientY, startBpm: bpm };
        document.body.style.cursor = 'ns-resize';
    };

    React.useEffect(() => {
        const handleMouseMove = (event) => {
            if (!dragStateRef.current.isDragging) return;
            const delta = Math.floor((dragStateRef.current.startY - event.clientY) / 4);
            const amount = dragStateRef.current.mode === 'dec' ? delta * 0.1 : delta;
            setBpm(Math.round(Math.max(30, Math.min(300, dragStateRef.current.startBpm + amount)) * 10) / 10);
        };
        const handleMouseUp = () => {
            if (!dragStateRef.current.isDragging) return;
            dragStateRef.current.isDragging = false;
            document.body.style.cursor = '';
        };
        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, []);

    const formatTime = (seconds) => {
        if (!Number.isFinite(seconds) || seconds <= 0) return '0:00';
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = Math.floor(seconds % 60);
        return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
    };

    const expectedTracks = expectedTracksRef.current;
    const isAudioReady = expectedTracks.length > 0 && expectedTracks.every((trackName) => buffersRef.current.has(trackName));

    return {
        audioCtxRef,
        transportRef,
        transportPositionRef,
        getTransportPosition,
        isPlaying,
        progress,
        duration,
        mutedTracks,
        soloedTracks,
        trackGainsDb,
        isCycling,
        setIsCycling,
        cycleRegion,
        setCycleRegion,
        bpm,
        timeSignature,
        setTimeSignature,
        setDuration,
        togglePlay,
        handleGoToBeginning,
        handleSeek,
        toggleMute,
        toggleSolo,
        setTrackGainDb,
        handleBpmMouseDown,
        setBpm,
        setOriginalBpm,
        originalBpm,
        formatTime,
        audioLoadState,
        audioMemoryMetrics,
        isAudioReady,
        transportStartTime,
    };
}
