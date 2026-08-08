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

const START_LEAD_SECONDS = 0.08;
const MIX_RAMP_SECONDS = 0.005;
const DOWNLOAD_CONCURRENCY = 2;

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

async function mapWithConcurrency(items, concurrency, mapper) {
    let nextIndex = 0;
    const worker = async () => {
        while (nextIndex < items.length) {
            const item = items[nextIndex];
            nextIndex += 1;
            await mapper(item);
        }
    };
    await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
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
    const loadControllersRef = React.useRef(new Set());
    const inFlightKeysRef = React.useRef(new Map());
    const currentDescriptorsRef = React.useRef({});
    const expectedTracksRef = React.useRef([]);
    const loadGenerationRef = React.useRef(0);
    const transportStartTimeRef = React.useRef(null);
    const transportOffsetRef = React.useRef(0);
    const transportRateRef = React.useRef(1);
    const isPlayingRef = React.useRef(false);
    const mutedTracksRef = React.useRef({});
    const soloedTracksRef = React.useRef({});
    const activeMidiTracksRef = React.useRef(activeMidiTracks);

    const [isPlaying, setIsPlaying] = React.useState(false);
    const [progress, setProgress] = React.useState(0);
    const [duration, setDuration] = React.useState(0);
    const [mutedTracks, setMutedTracks] = React.useState({});
    const [soloedTracks, setSoloedTracks] = React.useState({});
    const [isCycling, setIsCycling] = React.useState(false);
    const [cycleRegion, setCycleRegion] = React.useState({ startBar: 0, endBar: 2 });
    const [bpm, setBpm] = React.useState(120.0);
    const [originalBpm, setOriginalBpm] = React.useState(null);
    const [timeSignature, setTimeSignature] = React.useState('4/4');
    const [audioLoadState, setAudioLoadState] = React.useState({});
    const [transportStartTime, setTransportStartTime] = React.useState(null);
    const dragStateRef = React.useRef({ isDragging: false, mode: null, startY: 0, startBpm: 0 });

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

    const ensureAudioContext = () => {
        if (!audioCtxRef.current) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioCtxRef.current = new AudioContext();
        }
        return audioCtxRef.current;
    };

    const currentTransportPosition = () => {
        const context = audioCtxRef.current;
        const startTime = transportStartTimeRef.current;
        if (!isPlayingRef.current || !context || startTime === null) return transportOffsetRef.current;
        const elapsed = Math.max(0, context.currentTime - startTime);
        return Math.min(duration || Infinity, transportOffsetRef.current + elapsed * transportRateRef.current);
    };

    const applyMixState = (nextMuted = mutedTracksRef.current, nextSoloed = soloedTracksRef.current, nextMidi = activeMidiTracksRef.current) => {
        const context = audioCtxRef.current;
        if (!context) return;
        const hasSolo = Object.values(nextSoloed).some(Boolean);
        const now = context.currentTime;
        gainNodesRef.current.forEach((gainNode, trackName) => {
            const shouldMute = nextMidi[trackName]
                || (hasSolo ? !nextSoloed[trackName] : Boolean(nextMuted[trackName]));
            const targetGain = shouldMute ? 0 : 1;
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
        setProgress(transportOffsetRef.current);
    };

    const schedulePlayback = (offset) => {
        const context = ensureAudioContext();
        const trackNames = expectedTracksRef.current;
        const startTime = context.currentTime + START_LEAD_SECONDS;
        const masterTrack = buffersRef.current.has('Original') ? 'Original' : trackNames[0];

        stopActiveSources();
        transportOffsetRef.current = Math.max(0, Math.min(offset, duration));
        transportStartTimeRef.current = startTime;
        setTransportStartTime(startTime);

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
                    setProgress(0);
                };
            }
            source.start(startTime, transportOffsetRef.current);
            sourcesRef.current.set(trackName, source);
        });

        applyMixState();
        isPlayingRef.current = true;
        setIsPlaying(true);
        setProgress(transportOffsetRef.current);
    };

    // A new upload or saved job must never reuse a prior job's buffers or
    // transport state. Source additions within the same job retain already
    // decoded tracks and simply expand the ready set.
    React.useEffect(() => {
        loadGenerationRef.current += 1;
        stopPlayback(true);
        buffersRef.current.clear();
        bufferKeysRef.current.clear();
        loadControllersRef.current.forEach((controller) => controller.abort());
        loadControllersRef.current.clear();
        inFlightKeysRef.current.clear();
        gainNodesRef.current.forEach((gainNode) => gainNode.disconnect());
        gainNodesRef.current.clear();
        const nextMuted = sourceTrackSignature.includes('Original') && sourceTrackSignature !== 'Original'
            ? { Original: true }
            : {};
        mutedTracksRef.current = nextMuted;
        soloedTracksRef.current = {};
        setMutedTracks(nextMuted);
        setSoloedTracks({});
        setDuration(0);
        setAudioLoadState({});
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
        const entriesToLoad = Object.entries(sourceDescriptors).filter(([trackName, descriptor]) => (
            bufferKeysRef.current.get(trackName) !== descriptor.key
            && inFlightKeysRef.current.get(trackName) !== descriptor.key
        ));

        bufferKeysRef.current.forEach((_, trackName) => {
            if (!sourceDescriptors[trackName]) {
                buffersRef.current.delete(trackName);
                bufferKeysRef.current.delete(trackName);
                gainNodesRef.current.get(trackName)?.disconnect();
                gainNodesRef.current.delete(trackName);
                setAudioLoadState((current) => {
                    const { [trackName]: _removedTrack, ...remaining } = current;
                    return remaining;
                });
            }
        });

        if (entriesToLoad.length === 0) return undefined;

        entriesToLoad.forEach(([trackName, descriptor]) => {
            inFlightKeysRef.current.set(trackName, descriptor.key);
            setAudioLoadState((current) => ({ ...current, [trackName]: 'loading' }));
        });

        const loadTrack = async ([trackName, descriptor]) => {
            const controller = new AbortController();
            loadControllersRef.current.add(controller);
            try {
                let audioBytes;
                if (descriptor.kind === 'file') {
                    console.info(`[CloudDSP] Starting local audio decode for '${trackName}'.`);
                    audioBytes = await descriptor.source.arrayBuffer();
                } else {
                    console.info(`[CloudDSP] Starting S3 audio download for '${trackName}'.`);
                    const response = await fetch(descriptor.source, { signal: controller.signal });
                    if (!response.ok) {
                        throw new Error(`S3 audio request failed (HTTP ${response.status}).`);
                    }
                    audioBytes = await response.arrayBuffer();
                    console.info(`[CloudDSP] S3 audio download complete for '${trackName}' (${audioBytes.byteLength} bytes).`);
                }

                const context = ensureAudioContext();
                const audioBuffer = await context.decodeAudioData(audioBytes);
                if (
                    controller.signal.aborted
                    || loadGeneration !== loadGenerationRef.current
                    || currentDescriptorsRef.current[trackName]?.key !== descriptor.key
                ) return;
                buffersRef.current.set(trackName, audioBuffer);
                bufferKeysRef.current.set(trackName, descriptor.key);
                ensureTrackGain(trackName, context);
                setDuration((currentDuration) => Math.max(currentDuration, audioBuffer.duration));
                setAudioLoadState((current) => ({ ...current, [trackName]: 'ready' }));
                console.info(`[CloudDSP] Audio decode complete for '${trackName}'; ready for synchronized playback.`);
            } catch (error) {
                if (controller.signal.aborted || error.name === 'AbortError') return;
                console.error(`[CloudDSP] Could not prepare '${trackName}' for playback:`, error);
                setAudioLoadState((current) => ({ ...current, [trackName]: 'failed' }));
            } finally {
                loadControllersRef.current.delete(controller);
                if (inFlightKeysRef.current.get(trackName) === descriptor.key) {
                    inFlightKeysRef.current.delete(trackName);
                }
            }
        };

        void mapWithConcurrency(entriesToLoad, DOWNLOAD_CONCURRENCY, loadTrack);
        return undefined;
    }, [sourceDescriptors, jobId]);

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
            setProgress(offset);
        }
        transportRateRef.current = rate;
    }, [bpm, originalBpm]);

    React.useEffect(() => {
        if (!isPlaying) return undefined;
        let frameId;
        const updateProgress = () => {
            const position = currentTransportPosition();
            if (duration && position >= duration) {
                stopPlayback(true);
                return;
            }

            if (isCycling) {
                const beatsPerBar = parseInt(timeSignature.split('/')[0], 10) || 4;
                const cycleEnd = (cycleRegion.endBar * beatsPerBar) / (bpm / 60);
                const cycleStart = (cycleRegion.startBar * beatsPerBar) / (bpm / 60);
                if (position >= cycleEnd) {
                    schedulePlayback(cycleStart);
                    frameId = requestAnimationFrame(updateProgress);
                    return;
                }
            }

            setProgress(position);
            frameId = requestAnimationFrame(updateProgress);
        };
        frameId = requestAnimationFrame(updateProgress);
        return () => cancelAnimationFrame(frameId);
    }, [isPlaying, duration, isCycling, cycleRegion, bpm, timeSignature]);

    React.useEffect(() => () => {
        stopPlayback(true);
        gainNodesRef.current.forEach((gainNode) => gainNode.disconnect());
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
        const clampedTime = Math.max(0, Math.min(Number(time) || 0, duration));
        if (isPlayingRef.current) {
            schedulePlayback(clampedTime);
        } else {
            transportOffsetRef.current = clampedTime;
            setProgress(clampedTime);
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
        isPlaying,
        progress,
        duration,
        mutedTracks,
        soloedTracks,
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
        handleBpmMouseDown,
        setBpm,
        setOriginalBpm,
        originalBpm,
        formatTime,
        audioLoadState,
        isAudioReady,
        transportStartTime,
    };
}
