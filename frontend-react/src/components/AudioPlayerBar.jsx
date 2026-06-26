import React, { useEffect, useState } from 'react';
import { formatTime } from '../vanilla/utils.js';

export default function AudioPlayerBar({ ctxRef }) {
    const [isPlaying, setIsPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [fileName, setFileName] = useState("No file loaded");
    const [volume, setVolume] = useState(1);
    const [recordState, setRecordState] = useState('idle');
    const [countdown, setCountdown] = useState(3);

    const { audioElement, audioEngine, uiState, automationState } = ctxRef.current;
    
    // Listen for events from the Vanilla audio element and Vanilla automation state
    useEffect(() => {
        ctxRef.current.onPlayheadScrub = (time) => setCurrentTime(time);
        
        ctxRef.current.onRecordCountdown = (count) => {
            setRecordState('preparing');
            setCountdown(count);
        };
        ctxRef.current.onRecordStart = () => {
            setRecordState('recording');
        };
        ctxRef.current.onRecordStop = () => {
            setRecordState('idle');
        };

        const handleTimeUpdate = () => {
            if (!uiState.isDraggingPlayhead) {
                setCurrentTime(audioElement.currentTime);
                setDuration(audioElement.duration || 0);
            }
        };
        const handlePlay = () => setIsPlaying(true);
        const handlePause = () => setIsPlaying(false);
        const handleLoaded = () => setDuration(audioElement.duration || 0);

        audioElement.addEventListener('timeupdate', handleTimeUpdate);
        audioElement.addEventListener('play', handlePlay);
        audioElement.addEventListener('pause', handlePause);
        audioElement.addEventListener('loadedmetadata', handleLoaded);

        return () => {
            audioElement.removeEventListener('timeupdate', handleTimeUpdate);
            audioElement.removeEventListener('play', handlePlay);
            audioElement.removeEventListener('pause', handlePause);
            audioElement.removeEventListener('loadedmetadata', handleLoaded);
        };
    }, [audioElement, uiState]);

    const handlePlayPause = () => {
        if (audioEngine.audioContext.state === 'suspended') {
            audioEngine.audioContext.resume();
        }
        if (audioElement.paused) {
            audioElement.play();
        } else {
            audioElement.pause();
            if (automationState.recordState === 'recording') {
                toggleRecord(); // Assuming logic is wired up later
            }
        }
    };

    const handleSeek = (e) => {
        const val = parseFloat(e.target.value);
        audioElement.currentTime = val;
        setCurrentTime(val);
        if (audioElement.paused && automationState.activeData?.frames?.length > 0) {
            if (ctxRef.current.forceAutomationSync) ctxRef.current.forceAutomationSync();
        }
    };

    const handleVolume = (e) => {
        const val = parseFloat(e.target.value);
        audioElement.volume = val;
        setVolume(val);
    };

    const handleFileUpload = (e) => {
        const file = e.target.files[0];
        if (file) {
            audioElement.src = URL.createObjectURL(file);
            setFileName(file.name);
        }
    };

    const handleGoToBeginning = () => {
        audioElement.currentTime = 0;
        if (!audioElement.paused) audioElement.pause();
    };

    const toggleRecord = () => {
        if (ctxRef.current.recordBtnHandler) {
            ctxRef.current.recordBtnHandler();
        }
    };

    return (
        <div id="custom-audio-player">
            <button id="go-to-beginning-btn" title="Go to Beginning" onClick={handleGoToBeginning}>
                <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
            </button>

            <button id="play-pause-btn" title="Play/Pause" onClick={handlePlayPause}>
                {isPlaying ? (
                    <svg id="pause-icon" viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                ) : (
                    <svg id="play-icon" viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                )}
            </button>
            
            <button id="record-btn" title="Record" onClick={toggleRecord} className={recordState === 'recording' ? 'is-recording' : ''}>
                {recordState === 'preparing' ? (
                    <span className="countdown-text" style={{ fontSize: 14, fontWeight: 'bold' }}>{countdown}</span>
                ) : (
                    <div style={{ width: 14, height: 14, backgroundColor: '#ff3b3b', borderRadius: '50%' }}></div>
                )}
            </button>
            
            <span id="time-display">{formatTime(currentTime)} / {formatTime(duration)}</span>
            
            <input type="range" id="seek-bar" value={currentTime} max={duration || 0} step="0.1" onChange={handleSeek} />
            
            <label htmlFor="audio-upload" className="upload-btn">Browse...</label>
            <input type="file" id="audio-upload" accept="audio/*" style={{ display: 'none' }} onChange={handleFileUpload} />
            
            <div id="file-name-container">
                <div id="file-name-display" style={{ color: fileName === "No file loaded" ? 'inherit' : '#fff' }}>{fileName}</div>
            </div>

            <label htmlFor="volume-slider" className="vol-label">Vol</label>
            <input type="range" id="volume-slider" min="0" max="1" value={volume} step="0.01" onChange={handleVolume} />
        </div>
    );
}
