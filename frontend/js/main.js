// main.js
// Controller file connecting UI, Audio, and Visualizer modules
import { initializeAudioEngine } from './audio.js';
import { drawVisualizer, getLogX, getFreqFromX } from './visualizer.js';

const audioElement = document.getElementById('audio-source');
const fileInput = document.getElementById('audio-upload');

// Handle local file uploads to preview audio instantly in the browser
fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        // Create a temporary local memory URL pointing to the uploaded file
        const blobUrl = URL.createObjectURL(file);
        
        // Update the audio element to play the user's file
        audioElement.src = blobUrl;
        
        // Inform the user
        console.log(`Loaded local file: ${file.name}`);
    }
});

/**
 * A helper function to group and retrieve the DOM elements associated with a specific EQ band.
 * 
 * @param {string} bandId - The prefix identifier for the HTML elements (e.g., 'b1', 'b2').
 * @param {boolean} hasGain - Whether the band has a gain slider (true for bell, false for HPF/LPF).
 * @returns {Object} An object containing references to the interactive input sliders and their corresponding text label display elements.
 */
const getBandUI = (bandId, hasGain = true) => {
    const ui = {
        freq: document.getElementById(`${bandId}-freq`),
        q: document.getElementById(`${bandId}-q`),
        labels: {
            freq: document.getElementById(`${bandId}-freq-val`),
            q: document.getElementById(`${bandId}-q-val`),
        }
    };
    if (hasGain) {
        ui.gain = document.getElementById(`${bandId}-gain`);
        ui.labels.gain = document.getElementById(`${bandId}-gain-val`);
    }
    return ui;
};

const sliders = {
    b0: getBandUI('b0', false), // Highpass
    b1: getBandUI('b1', true),  // Bell 1
    b2: getBandUI('b2', true),  // Bell 2
    b3: getBandUI('b3', true),  // Bell 3
    b4: getBandUI('b4', false)  // Lowpass
};

let audioEngine = null;

// Track UI interaction state for the canvas
const uiState = {
    hoveredNode: -1,
    hoveredZone: -1,
    activeDragBand: -1,
    activeZoneDragBand: -1,
    dragStartX: 0,
    startQ: 1.0
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
    const PADDING_TOP_PX = 60;
    const usableHeight = canvas.height - PADDING_BOTTOM_PX - PADDING_TOP_PX;
    const centerY = usableHeight / 2;
    const maxDeltaY = centerY - 2; 
    const pxPerDb = maxDeltaY / 15;

    if (uiState.activeDragBand !== -1) {
        // We are actively dragging a node in 2D space
        // Remove the top padding from the mouse physical Y coordinate to align with math
        let newY = mouseY - PADDING_TOP_PX;
        let newX = mouseX;
        
        // Clamp the Y drag to the +/- 15dB physical boundaries relative to the new usable area
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
        
    } else if (uiState.activeZoneDragBand !== -1) {
        // We are actively dragging the zone horizontally to change the Q-Factor
        // Calculate how far the mouse has moved since the initial click
        const deltaX = mouseX - uiState.dragStartX;
        
        // Dragging Right (positive delta) makes the curve WIDER (Lower Q)
        // Dragging Left (negative delta) makes the curve NARROWER (Higher Q)
        // We use a sensitivity multiplier (e.g., 0.02) so it doesn't jump too fast
        let newQ = uiState.startQ - (deltaX * 0.02);
        
        // Clamp the Q-factor to the limits defined in our HTML sliders (0.1 to 10.0)
        if (newQ < 0.1) newQ = 0.1;
        if (newQ > 10.0) newQ = 10.0;
        
        // 1. Update the underlying Web Audio API Filter instantly
        const filter = audioEngine.filters[uiState.activeZoneDragBand];
        filter.Q.value = newQ;
        
        // 2. Sync the HTML slider and text label
        const bandKey = `b${uiState.activeZoneDragBand + 1}`;
        sliders[bandKey].q.value = newQ;
        sliders[bandKey].labels.q.innerText = newQ.toFixed(1);
        
        // Keep the cursor locked to horizontal resize during the drag
        canvas.style.cursor = 'ew-resize';

    } else {
        // We are just hovering, check if we are near any node or zone
        let hoveredNode = -1;
        let hoveredZone = -1;
        
        // 1. Check control circle hitboxes first (highest priority)
        // Skip filters 0 and 4 because they are highpass/lowpass without gain
        audioEngine.filters.forEach((filter, index) => {
            if (index === 0 || index === 4) return;
            
            const nodeX = getLogX(filter.frequency.value, canvas.width);
            // Re-add PADDING_TOP_PX to nodeY so it matches the physical canvas pixel exactly
            const nodeY = PADDING_TOP_PX + centerY - (filter.gain.value * pxPerDb);
            
            const dx = mouseX - nodeX;
            const dy = mouseY - nodeY;
            const distance = Math.sqrt(dx * dx + dy * dy);
            
            if (distance <= 12) {
                hoveredNode = index;
            }
        });
        
        // 2. If no node is hovered, check rectangular filter zone hitboxes
        if (hoveredNode === -1) {
            const ZONE_WIDTH = 40; // Fixed horizontal width (+/- 20px)
            
            audioEngine.filters.forEach((filter, index) => {
                if (index === 0 || index === 4) return;
                
                const nodeX = getLogX(filter.frequency.value, canvas.width);
                const nodeY = PADDING_TOP_PX + centerY - (filter.gain.value * pxPerDb);
                const trueCenterY = PADDING_TOP_PX + centerY;
                
                const leftBound = nodeX - (ZONE_WIDTH / 2);
                const rightBound = nodeX + (ZONE_WIDTH / 2);
                
                // Vertical bounds: Between the true 0dB center line and the node's peak
                const topBound = Math.min(trueCenterY, nodeY);
                const bottomBound = Math.max(trueCenterY, nodeY);
                
                if (mouseX >= leftBound && mouseX <= rightBound && mouseY >= topBound && mouseY <= bottomBound) {
                    hoveredZone = index;
                }
            });
        }
        
        // Update state
        uiState.hoveredNode = hoveredNode;
        uiState.hoveredZone = hoveredZone;
        
        // Change cursor to indicate horizontal dragging when in the zone (but not on the node)
        if (hoveredZone !== -1 && hoveredNode === -1) {
            canvas.style.cursor = 'ew-resize';
        } else {
            canvas.style.cursor = 'default';
        }
    }
});

canvas.addEventListener('mousedown', (e) => {
    // Priority 1: Check if we hit a node (2D movement)
    if (uiState.hoveredNode !== -1) {
        uiState.activeDragBand = uiState.hoveredNode;
    } 
    // Priority 2: Check if we hit a zone (Horizontal Q-Factor stretching)
    else if (uiState.hoveredZone !== -1) {
        uiState.activeZoneDragBand = uiState.hoveredZone;
        
        // Record the starting X position and current Q value for delta math
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        uiState.dragStartX = (e.clientX - rect.left) * scaleX;
        uiState.startQ = audioEngine.filters[uiState.hoveredZone].Q.value;
    }
});

canvas.addEventListener('mouseup', () => {
    uiState.activeDragBand = -1;
    uiState.activeZoneDragBand = -1;
});

canvas.addEventListener('mouseleave', () => {
    uiState.activeDragBand = -1;
    uiState.activeZoneDragBand = -1;
    uiState.hoveredNode = -1;
    uiState.hoveredZone = -1;
    canvas.style.cursor = 'default';
});

// Boot up the audio context and routing immediately
audioEngine = initializeAudioEngine(audioElement, sliders);

// Bind UI Sliders to the Web Audio Filters for each band
[sliders.b0, sliders.b1, sliders.b2, sliders.b3, sliders.b4].forEach((band, index) => {
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
    
    if (band.gain) {
        band.gain.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            filter.gain.value = value;
            band.labels.gain.innerText = value.toFixed(1);
        });
    }
});

// Start the visualization loop immediately (even while context is suspended)
drawVisualizer(
    audioEngine.preAnalyser, 
    audioEngine.preDataArray, 
    audioEngine.postAnalyser, 
    audioEngine.postDataArray, 
    audioEngine.audioContext, 
    audioEngine.filters,
    uiState // Pass interaction state to canvas
);

// When the user actually plays the audio, resume the suspended context
audioElement.addEventListener('play', () => {
    if (audioEngine && audioEngine.audioContext.state === 'suspended') {
        audioEngine.audioContext.resume();
    }
});