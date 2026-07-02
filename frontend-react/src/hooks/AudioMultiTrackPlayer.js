import React, { useState, useEffect, useRef } from 'react';

export function useAudioMultiTrackPlayer(stemUrls, file) {
    const audioRefs = useRef({});
    
    // Core Player State
    const [isPlaying, setIsPlaying] = useState(false);
    const [progress, setProgress] = useState(0);
    const [duration, setDuration] = useState(0);
    const [mutedTracks, setMutedTracks] = useState({});
    const [soloedTracks, setSoloedTracks] = useState({});
    const [isCycling, setIsCycling] = useState(false);
    
    // BPM & Time Signature State
    const [bpm, setBpm] = useState(120.0);
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

    const togglePlay = () => {
        if (!stemUrls) return;
        const nextState = !isPlaying;
        setIsPlaying(nextState);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) {
                if (nextState) audio.play();
                else audio.pause();
            }
        });
    };

    const handleGoToBeginning = () => {
        setProgress(0);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) audio.currentTime = 0;
        });
    };

    const handleSeek = (e) => {
        const time = Number(e.target.value);
        setProgress(time);
        Object.values(audioRefs.current).forEach(audio => {
            if (audio) {
                audio.currentTime = time;
            }
        });
    };

    const toggleMute = (trackName) => {
        setMutedTracks(prev => ({ ...prev, [trackName]: !prev[trackName] }));
    };

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

    // BPM Drag Logic
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

    // Master Playback Clock and Sync
    useEffect(() => {
        let interval;
        if (isPlaying) {
            const trackKeys = Object.keys(audioRefs.current);
            if (trackKeys.length === 0) return;
            const masterAudio = audioRefs.current[trackKeys[0]];
            
            interval = setInterval(() => {
                if (masterAudio) {
                    setProgress(masterAudio.currentTime);
                    if (masterAudio.duration && masterAudio.duration !== duration) {
                        setDuration(masterAudio.duration);
                    }
                    
                    // Anti-drift snapping
                    trackKeys.forEach(key => {
                        const audio = audioRefs.current[key];
                        if (audio && audio !== masterAudio) {
                            if (Math.abs(audio.currentTime - masterAudio.currentTime) > 0.1) {
                                audio.currentTime = masterAudio.currentTime;
                                if (audio.paused) {
                                    audio.play().catch(() => {});
                                }
                            }
                        }
                    });

                    if (masterAudio.ended) {
                        setIsPlaying(false);
                        setProgress(0);
                    }
                }
            }, 50);
        }
        return () => clearInterval(interval);
    }, [isPlaying, duration]);

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
        formatTime
    };
}
