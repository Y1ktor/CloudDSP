import React, { useRef, useState, useEffect } from 'react';
import AudioPlayerBar from './components/AudioPlayerBar';
import AutomationBar from './components/AutomationBar';
import EqCanvas from './components/EqCanvas';

import { initializeAudioEngine } from './vanilla/audio.js';
import { setupAutomation } from './vanilla/automation.js';
import { updateScrollingText } from './vanilla/utils.js';

import './assets/css/styles.css';

/**
 * App.jsx
 * This is the primary orchestrator component for the CloudDSP frontend.
 * It serves as a bridge between the new React architecture and the legacy Vanilla JS audio engine.
 * 
 * Usage:
 * Instead of rewriting the entire highly-optimized Web Audio and Canvas visualizer engine into React hooks 
 * (which would be slow and cause unnecessary re-renders), this component creates a shared `ctxRef` object.
 * This `ctxRef` acts as an "escape hatch" payload containing the raw audio context, element, and state buffers,
 * allowing the vanilla scripts to run at 60fps outside of React's jurisdiction, while still letting 
 * React child components (`<AudioPlayerBar>`, `<AutomationBar>`) read and write to it.
 *
 * @returns {JSX.Element} The rendered layout combining the React toolbars and the Vanilla canvas.
 */
export default function App() {
    const [isReady, setIsReady] = useState(false);
    
    // The core Vanilla Context Object (The Escape Hatch)
    const ctxRef = useRef(null);

    useEffect(() => {
        // Initialize the Web Audio nodes exactly like the original main.js
        // We create an audio element here instead of placing it in the DOM since WebAudio can pipe it in memory
        const audioElement = document.createElement('audio');
        
        const sliders = {
            b0: { freq: { value: 20 }, q: { value: 0 }, gain: { value: 0 }, gainContainer: { style: {} }, qContainer: { style: {} }, labels: { freq: {}, q: {}, gain: {} } },
            b1: { freq: { value: 100 }, q: { value: 1.0 }, gain: { value: 0 }, labels: { freq: {}, q: {}, gain: {} } },
            b2: { freq: { value: 500 }, q: { value: 1.0 }, gain: { value: 0 }, labels: { freq: {}, q: {}, gain: {} } },
            b3: { freq: { value: 2000 }, q: { value: 1.0 }, gain: { value: 0 }, labels: { freq: {}, q: {}, gain: {} } },
            b4: { freq: { value: 5000 }, q: { value: 1.0 }, gain: { value: 0 }, labels: { freq: {}, q: {}, gain: {} } },
            b5: { freq: { value: 20000 }, q: { value: 0 }, gain: { value: 0 }, gainContainer: { style: {} }, qContainer: { style: {} }, labels: { freq: {}, q: {}, gain: {} } }
        };

        const filterModes = {
            b0: 'highpass',
            b5: 'lowpass'
        };

        const uiState = {
            hoveredNode: -1,
            hoveredZone: -1,
            hoveredTopBand: -1,
            hoveredBadge: -1,
            hoveredPowerBtn: false,
            hoveredPlayhead: false,
            globalPower: true,
            activeDragBand: -1,
            activeZoneDragBand: -1,
            isDraggingPlayhead: false,
            dragStartX: 0,
            startQ: 1.0,
            bandStates: [true, true, true, true, true, true],
            savedBandStates: null
        };

        const automationState = {
            recordState: 'idle',
            data: [],
            activeData: null,
            currentFileKey: null
        };

        const audioEngine = initializeAudioEngine(audioElement, sliders);

        ctxRef.current = {
            audioElement,
            audioEngine,
            uiState,
            sliders,
            filterModes,
            automationState,
            updateScrollingText
        };
        
        // Setup backend automation bindings (which mutated the ctx payload in vanilla)
        setupAutomation(ctxRef.current);
        
        setIsReady(true);
    }, []);

    if (!isReady) return <div style={{ color: 'white', padding: 20 }}>Loading Audio Engine...</div>;

    return (
        <div>
            <h1>CloudDSP Interactive EQ</h1>
            <AudioPlayerBar ctxRef={ctxRef} />
            <AutomationBar ctxRef={ctxRef} />
            <EqCanvas ctxRef={ctxRef} />
        </div>
    );
}
