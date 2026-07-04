/**
 * AudioMultiTrackPlayer.js
 * 
 * Core playback engine for CloudDSP. This hook manages the synchronization of multiple audio stems 
 * using the Web Audio API (AudioContext). It delegates phase-locking to the native C++ audio thread, 
 * eliminating drift and Javascript Event Loop throttling. UI updates are handled via requestAnimationFrame,
 * and the Page Visibility API is integrated to save energy when the tab is backgrounded.
 */
import React, { useState, useEffect, useRef } from 'react';

/**
 * Custom hook to manage multitrack audio playback, synchronization, and BPM scaling.
 * 
 * @param {Object} stemUrls - Dictionary of track names to their audio URLs (e.g., { 'vocals': 'url', 'drums': 'url' })
 * @param {File} file - The original user-uploaded File object.
 * @returns {Object} An object containing all playback state, refs, and controller functions.
 */
export function useAudioMultiTrackPlayer(stemUrls, file) {
    const audioRefs = useRef({});
    const audioCtxRef = useRef(null);
    const sourceNodesRef = useRef({});
    
    // Core Player State
    const [isPlaying, setIsPlaying] = useState(false);
    const [progress, setProgress] = useState(0);
    const [duration, setDuration] = useState(0);
    const [mutedTracks, setMutedTracks] = useState({});
    const [soloedTracks, setSoloedTracks] = useState({});
    const [isCycling, setIsCycling] = useState(false);
    
    // BPM & Time Signature State
    const [bpm, setBpm] = useState(120.0);
    const [originalBpm, setOriginalBpm] = useState(null);
    const [timeSignature, setTimeSignature] = useState("4/4");
    const dragStateRef = useRef({ isDragging: false, mode: null, startY: 0, startBpm: 0 });

    // Local file handling for the "Original" track
    const [originalUrl, setOriginalUrl] = useState(null);

    useEffect(() => {
        if (file) {
            const url = URL.createObjectURL(file);
            setOriginalUrl(url);
            return () => URL.revokeObjectURL(url);
        } else {
            setOriginalUrl(null);
        }
    }, [file]);
    
    // Reset player state when new stems arrive
    useEffect(() => {
        if (stemUrls) {
            setIsPlaying(false);
            setProgress(0);
            setDuration(0);
            setMutedTracks({ 'Original': true });
            setSoloedTracks({});
        }
    }, [stemUrls]);

    /**
     * Toggles play/pause for all loaded audio tracks simultaneously.
     * Initializes the AudioContext upon the first user interaction, and hard-syncs 
     * track timestamps to guarantee phase alignment before waking decoders.
     */
    const togglePlay = () => {
        if (!stemUrls) return;
        const nextState = !isPlaying;

        // 1. Initialize AudioContext strictly upon user gesture
        if (!audioCtxRef.current) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioCtxRef.current = new AudioContext();
        }

        // 2. Hook up HTML5 <audio> to the Web Audio API Graph to lock their clocks!
        Object.keys(audioRefs.current).forEach(trackName => {
            const audio = audioRefs.current[trackName];
            if (audio && !sourceNodesRef.current[trackName]) {
                try {
                    const source = audioCtxRef.current.createMediaElementSource(audio);
                    source.connect(audioCtxRef.current.destination);
                    sourceNodesRef.current[trackName] = source;
                } catch (e) {
                    // Ignore if already connected
                }
            }
        });

        // 3. Resume the suspended context
        if (nextState && audioCtxRef.current.state === 'suspended') {
            audioCtxRef.current.resume();
        }

        // 4. Hard-sync physical timestamps to prevent buffer flush stuttering
        if (nextState) {
            const trackKeys = Object.keys(audioRefs.current);
            if (trackKeys.length > 0) {
                const masterTime = audioRefs.current[trackKeys[0]].currentTime;
                Object.values(audioRefs.current).forEach(audio => {
                    if (audio) audio.currentTime = masterTime;
                });
            }
        }

        setIsPlaying(nextState);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) {
                if (nextState) audio.play().catch(() => {});
                else audio.pause();
            }
        });
    };

    /**
     * Seeks all tracks back to 0:00.
     */
    const handleGoToBeginning = () => {
        setProgress(0);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) audio.currentTime = 0;
        });
    };

    /**
     * Seeks all tracks to a specific timestamp.
     * 
     * @param {Event} e - The input change event from the progress slider.
     */
    const handleSeek = (e) => {
        const time = Number(e.target.value);
        setProgress(time);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) {
                audio.currentTime = time;
            }
        });
    };

    /**
     * Toggles the mute state of a specific track.
     * 
     * @param {string} trackName - The name of the track to mute/unmute.
     */
    const toggleMute = (trackName) => {
        setMutedTracks(prev => ({ ...prev, [trackName]: !prev[trackName] }));
    };

    /**
     * Toggles the solo state of a specific track.
     * 
     * @param {string} trackName - The name of the track to solo/unsolo.
     */
    const toggleSolo = (trackName) => {
        setSoloedTracks(prev => ({ ...prev, [trackName]: !prev[trackName] }));
    };

    // Apply mute/solo logic to HTML audio elements
    useEffect(() => {
        const isAnySoloed = Object.values(soloedTracks).some(isSoloed => isSoloed);
        
        Object.keys(audioRefs.current).forEach(trackName => {
            const audio = audioRefs.current[trackName];
            if (audio) {
                if (isAnySoloed) {
                    audio.muted = !soloedTracks[trackName];
                } else {
                    audio.muted = !!mutedTracks[trackName];
                }
            }
        });
    }, [mutedTracks, soloedTracks]);

    /**
     * Initiates the BPM dragging interaction.
     * 
     * @param {Event} e - The mouse down event.
     * @param {string} mode - 'int' for integer adjustments, 'dec' for decimal adjustments.
     */
    const handleBpmMouseDown = (e, mode) => {
        e.preventDefault();
        dragStateRef.current = {
            isDragging: true,
            mode: mode,
            startY: e.clientY,
            startBpm: bpm
        };
        document.body.style.cursor = 'ns-resize';
    };

    useEffect(() => {
        const handleMouseMove = (e) => {
            if (!dragStateRef.current.isDragging) return;
            const dy = dragStateRef.current.startY - e.clientY; 
            const mode = dragStateRef.current.mode;
            let newBpm = dragStateRef.current.startBpm;

            if (mode === 'int') {
                const deltaInt = Math.floor(dy / 4); 
                newBpm += deltaInt;
            } else if (mode === 'dec') {
                const deltaDec = Math.floor(dy / 4) * 0.1;
                newBpm += deltaDec;
            }

            newBpm = Math.max(30, Math.min(300, newBpm));
            setBpm(Math.round(newBpm * 10) / 10);
        };

        const handleMouseUp = () => {
            if (dragStateRef.current.isDragging) {
                dragStateRef.current.isDragging = false;
                document.body.style.cursor = '';
            }
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, [bpm]);

    // Apply playback rate dynamically based on BPM
    useEffect(() => {
        if (originalBpm && bpm) {
            const rate = bpm / originalBpm;
            // HTML5 Audio playbackRate safely supports 0.5 to 4.0 natively in most browsers
            const safeRate = Math.max(0.5, Math.min(4.0, rate)); 
            Object.values(audioRefs.current).forEach(audio => {
                if (audio) audio.playbackRate = safeRate;
            });
        }
    }, [bpm, originalBpm]);

    // Visual UI Clock (requestAnimationFrame automatically pauses in the background!)
    useEffect(() => {
        let animationFrameId;
        if (isPlaying) {
            const trackKeys = Object.keys(audioRefs.current);
            if (trackKeys.length === 0) return;
            const masterAudio = audioRefs.current[trackKeys[0]];
            
            const updateVisualProgress = () => {
                if (masterAudio) {
                    setProgress(masterAudio.currentTime);
                    if (masterAudio.duration && masterAudio.duration !== duration) {
                        setDuration(masterAudio.duration);
                    }
                    if (masterAudio.ended) {
                        setIsPlaying(false);
                        setProgress(0);
                    }
                }
                animationFrameId = requestAnimationFrame(updateVisualProgress);
            };
            animationFrameId = requestAnimationFrame(updateVisualProgress);
        }
        return () => cancelAnimationFrame(animationFrameId);
    }, [isPlaying, duration]);

    // Page Visibility API - Suspend process when hidden and not playing
    useEffect(() => {
        const handleVisibilityChange = () => {
            if (document.hidden && !isPlaying && audioCtxRef.current) {
                // Backgrounded and paused: Explicitly suspend the audio context to save battery
                audioCtxRef.current.suspend();
            }
        };
        document.addEventListener("visibilitychange", handleVisibilityChange);
        return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
    }, [isPlaying]);

    /**
     * Formats a raw second count into a standard "M:SS" string.
     * 
     * @param {number} seconds - The time in seconds.
     * @returns {string} The formatted time string.
     */
    const formatTime = (seconds) => {
        if (!seconds || isNaN(seconds)) return "0:00";
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    };

    return {
        audioRefs,
        originalUrl,
        isPlaying,
        progress,
        duration,
        mutedTracks,
        soloedTracks,
        isCycling,
        setIsCycling,
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
        formatTime,
        setBpm,
        originalBpm,
        setOriginalBpm
    };
}
