// main.js
// The Orchestrator: Initializes DOM bindings, the Web Audio engine, and the centralized state context.
import { initializeAudioEngine } from './audio.js';
import { drawVisualizer } from './visualizer.js';
import { setupUI } from './ui.js';
import { setupInteractions } from './interactions.js';
import { setupAutomation } from './automation.js';

const audioElement = document.getElementById('audio-source');
const canvas = document.getElementById('visualizer');

// Initialize generic slider/state structures without depending on DOM elements
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
    recordState: 'idle', // 'idle', 'preparing', 'recording'
    data: [],
    activeData: null,
    currentFileKey: null
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