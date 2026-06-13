// main.js
// The Orchestrator: Initializes DOM bindings, the Web Audio engine, and the centralized state context.
import { initializeAudioEngine } from './audio.js';
import { drawVisualizer } from './visualizer.js';
import { setupUI } from './ui.js';
import { setupInteractions } from './interactions.js';
import { setupAutomation } from './automation.js';

const audioElement = document.getElementById('audio-source');
const canvas = document.getElementById('visualizer');

const getBandUI = (bandId, hasToggle = false) => {
    const ui = {
        freq: document.getElementById(`${bandId}-freq`),
        q: document.getElementById(`${bandId}-q`),
        gain: document.getElementById(`${bandId}-gain`), // Always grab it, even if hidden initially
        labels: {
            freq: document.getElementById(`${bandId}-freq-val`),
            q: document.getElementById(`${bandId}-q-val`)
        }
    };
    if (ui.gain) ui.labels.gain = document.getElementById(`${bandId}-gain-val`);
    if (hasToggle) {
        ui.gainContainer = document.getElementById(`${bandId}-gain-container`);
        ui.qContainer = document.getElementById(`${bandId}-q-container`);
    }
    return ui;
};

const sliders = {
    b0: getBandUI('b0', true),  // Highpass/Lowshelf
    b1: getBandUI('b1'),        // Bell 1
    b2: getBandUI('b2'),        // Bell 2
    b3: getBandUI('b3'),        // Bell 3
    b4: getBandUI('b4'),        // Bell 4
    b5: getBandUI('b5', true)   // Lowpass/Highshelf
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
    recordState: 'idle', // 'idle', 'preparing', 'recording'
    data: [],
    activeData: null
};

// Boot up the audio context and routing immediately
const audioEngine = initializeAudioEngine(audioElement, sliders);

// Centralized Context Payload to share states safely
const ctx = {
    audioElement,
    canvas,
    audioEngine,
    uiState,
    sliders,
    filterModes,
    automationState
};

// Initialize Modules
setupUI(ctx);
setupInteractions(ctx);
setupAutomation(ctx);

// Start the visualization loop immediately (even while context is suspended)
drawVisualizer(
    audioEngine.preAnalyser, 
    audioEngine.preDataArray, 
    audioEngine.postAnalyser, 
    audioEngine.postDataArray, 
    audioEngine.audioContext, 
    audioEngine.filters,
    uiState, // Pass interaction state to canvas
    audioElement, // Pass audio element for playhead
    automationState // Pass automation state for interval drawing
);