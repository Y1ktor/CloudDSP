// main.js
// Controller file connecting UI, Audio, and Visualizer modules
import { initializeAudioEngine } from './audio.js';
import { drawVisualizer, getLogX, getFreqFromX } from './visualizer.js';

const audioElement = document.getElementById('audio-source');

/**
 * A helper function to group and retrieve the DOM elements associated with a specific EQ band.
 * 
 * @param {string} bandId - The prefix identifier for the HTML elements (e.g., 'b1', 'b2').
 * @returns {Object} An object containing references to the interactive input sliders and their corresponding text label display elements.
 */
const getBandUI = (bandId) => ({
    freq: document.getElementById(`${bandId}-freq`),
    q: document.getElementById(`${bandId}-q`),
    gain: document.getElementById(`${bandId}-gain`),
    labels: {
        freq: document.getElementById(`${bandId}-freq-val`),
        q: document.getElementById(`${bandId}-q-val`),
        gain: document.getElementById(`${bandId}-gain-val`)
    }
});

const sliders = {
    b1: getBandUI('b1'),
    b2: getBandUI('b2'),
    b3: getBandUI('b3')
};

let audioEngine = null;

// Track UI interaction state for the canvas
const uiState = {
    hoveredBand: -1,
    activeDragBand: -1
};

const canvas = document.getElementById('visualizer');

// Handle Canvas Mouse Interaction (Hit Detection & Dragging)
canvas.addEventListener('mousemove', (e) => {
    if (!audioEngine) return;
    
    // Get mouse coordinates relative to the physical screen size
    const rect = canvas.getBoundingClientRect();
    
    // Calculate the scaling ratio between the physical CSS size and the internal canvas resolution (800x300)
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    
    // Map the physical mouse coordinates to the internal canvas coordinates
    const mouseX = (e.clientX - rect.left) * scaleX;
    const mouseY = (e.clientY - rect.top) * scaleY;

    // Mathematical constants identical to visualizer.js
    const PADDING_BOTTOM_PX = 20;
    const usableHeight = canvas.height - PADDING_BOTTOM_PX;
    const centerY = usableHeight / 2;
    const maxDeltaY = centerY - 2; 
    const pxPerDb = maxDeltaY / 15;

    if (uiState.activeDragBand !== -1) {
        // We are actively dragging a node in 2D space
        let newY = mouseY;
        let newX = mouseX;
        
        // Clamp the Y drag to the +/- 15dB physical boundaries
        const minY = centerY - (15 * pxPerDb);
        const maxY = centerY - (-15 * pxPerDb);
        if (newY < minY) newY = minY;
        if (newY > maxY) newY = maxY;

        // Convert pixels back into audio values
        const newGain = (centerY - newY) / pxPerDb;
        const newFreq = getFreqFromX(newX, canvas.width);
        
        // 1. Update the underlying Web Audio API Filter instantly
        const filter = audioEngine.filters[uiState.activeDragBand];
        filter.gain.value = newGain;
        filter.frequency.value = newFreq;

        // 2. Sync the HTML sliders and text labels
        const bandKey = `b${uiState.activeDragBand + 1}`;
        
        sliders[bandKey].gain.value = newGain;
        sliders[bandKey].labels.gain.innerText = newGain.toFixed(1);
        
        sliders[bandKey].freq.value = newFreq;
        sliders[bandKey].labels.freq.innerText = Math.round(newFreq);
        
    } else {
        // We are just hovering, check if we are near any node
        let hovered = -1;
        audioEngine.filters.forEach((filter, index) => {
            const nodeX = getLogX(filter.frequency.value, canvas.width);
            const nodeY = centerY - (filter.gain.value * pxPerDb);
            
            // Calculate distance between mouse and node (Pythagorean theorem)
            const dx = mouseX - nodeX;
            const dy = mouseY - nodeY;
            const distance = Math.sqrt(dx * dx + dy * dy);
            
            // If mouse is within 12 pixels of the node center, it's a hit
            if (distance <= 12) {
                hovered = index;
            }
        });
        
        // Update state
        uiState.hoveredBand = hovered;
    }
});

canvas.addEventListener('mousedown', (e) => {
    if (uiState.hoveredBand !== -1) {
        uiState.activeDragBand = uiState.hoveredBand;
    }
});

canvas.addEventListener('mouseup', () => {
    uiState.activeDragBand = -1;
});

canvas.addEventListener('mouseleave', () => {
    uiState.activeDragBand = -1;
    uiState.hoveredBand = -1;
});

// Initialize on play to satisfy browser security requirements
audioElement.addEventListener('play', () => {
    if (!audioEngine) {
        // Boot up the audio context and routing
        audioEngine = initializeAudioEngine(audioElement, sliders);

        // Bind UI Sliders to the Web Audio Filters for each band
        [sliders.b1, sliders.b2, sliders.b3].forEach((band, index) => {
            const filter = audioEngine.filters[index];
            
            band.freq.addEventListener('input', (e) => {
                const value = parseFloat(e.target.value);
                filter.frequency.value = value;
                band.labels.freq.innerText = value;
            });
            
            band.q.addEventListener('input', (e) => {
                const value = parseFloat(e.target.value);
                filter.Q.value = value;
                band.labels.q.innerText = value.toFixed(1);
            });
            
            band.gain.addEventListener('input', (e) => {
                const value = parseFloat(e.target.value);
                filter.gain.value = value;
                band.labels.gain.innerText = value.toFixed(1);
            });
        });

        // Start the visualization loop
        drawVisualizer(
            audioEngine.preAnalyser, 
            audioEngine.preDataArray, 
            audioEngine.postAnalyser, 
            audioEngine.postDataArray, 
            audioEngine.audioContext, 
            audioEngine.filters,
            uiState // Pass interaction state to canvas
        );
    }
    
    // Ensure context resumes if the browser suspends it
    if (audioEngine.audioContext.state === 'suspended') {
        audioEngine.audioContext.resume();
    }
});