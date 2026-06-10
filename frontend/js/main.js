// main.js
// Controller file connecting UI, Audio, and Visualizer modules
import { initializeAudioEngine, rebuildAudioGraph } from './audio.js';
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
 * @param {boolean} hasToggle - Whether the band has toggleable containers (for pass/shelf).
 * @returns {Object} An object containing references to the interactive input sliders and their corresponding text label display elements.
 */
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
    
    if (ui.gain) {
        ui.labels.gain = document.getElementById(`${bandId}-gain-val`);
    }

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
    b4: getBandUI('b4', true)   // Lowpass/Highshelf
};

// Track the current mode of the outer filters
const filterModes = {
    b0: 'highpass',
    b4: 'lowpass'
};

let audioEngine = null;

// Track UI interaction state for the canvas
const uiState = {
    hoveredNode: -1,
    hoveredZone: -1,
    hoveredTopBand: -1,
    activeDragBand: -1,
    activeZoneDragBand: -1,
    dragStartX: 0,
    startQ: 1.0,
    bandStates: [true, true, true, true, true]
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
        
        // Determine Y-axis limits depending on what parameter we are controlling
        const filter = audioEngine.filters[uiState.activeDragBand];
        const isPassFilter = (filter.type === 'highpass' || filter.type === 'lowpass');
        const yLimit = isPassFilter ? 20 : 15;
        
        // Clamp the Y drag to the physical boundaries relative to the new usable area
        const minY = centerY - (yLimit * pxPerDb);
        const maxY = centerY - (-yLimit * pxPerDb);
        if (newY < minY) newY = minY;
        if (newY > maxY) newY = maxY;

        // Convert pixels back into audio values
        const newYValue = (centerY - newY) / pxPerDb;
        const newFreq = getFreqFromX(newX, canvas.width);
        
        // 1. Update the underlying Web Audio API Filter instantly
        filter.frequency.value = newFreq;

        // 2. Sync the HTML sliders and text labels
        const bandKey = `b${uiState.activeDragBand}`;
        
        // If it's a Cut filter, Y-axis controls Q. Otherwise, Y-axis controls Gain.
        if (filter.type === 'highpass' || filter.type === 'lowpass') {
            filter.Q.value = newYValue;
            if (sliders[bandKey].q) {
                sliders[bandKey].q.value = newYValue;
                sliders[bandKey].labels.q.innerText = newYValue.toFixed(1);
            }
        } else {
            filter.gain.value = newYValue;
            if (sliders[bandKey].gain) {
                sliders[bandKey].gain.value = newYValue;
                sliders[bandKey].labels.gain.innerText = newYValue.toFixed(1);
            }
        }
        
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
        
        // Clamp the Q-factor based on filter type. Cut filters have a +/- 20dB range. Others are 0.1 to 10.0.
        if (audioEngine.filters[uiState.activeZoneDragBand].type === 'highpass' || audioEngine.filters[uiState.activeZoneDragBand].type === 'lowpass') {
             if (newQ < -20.0) newQ = -20.0;
             if (newQ > 20.0) newQ = 20.0;
        } else {
             if (newQ < 0.1) newQ = 0.1;
             if (newQ > 10.0) newQ = 10.0;
        }
        
        // 1. Update the underlying Web Audio API Filter instantly
        const filter = audioEngine.filters[uiState.activeZoneDragBand];
        filter.Q.value = newQ;
        
        // 2. Sync the HTML slider and text label
        const bandKey = `b${uiState.activeZoneDragBand}`;
        
        // Safety check to ensure the band has a Q control currently visible
        if (sliders[bandKey].q) {
            sliders[bandKey].q.value = newQ;
            sliders[bandKey].labels.q.innerText = newQ.toFixed(1);
        }
        
        // Keep the cursor locked to horizontal resize during the drag
        canvas.style.cursor = 'ew-resize';

    } else {
        // We are just hovering, check if we are near any node or zone
        let hoveredNode = -1;
        let hoveredZone = -1;
        
        // 1. Check control circle hitboxes first (highest priority)
        audioEngine.filters.forEach((filter, index) => {
            const nodeX = getLogX(filter.frequency.value, canvas.width);
            
            // For Cut filters, Y-axis represents Q. For others, it represents Gain.
            let yValue = (filter.type === 'highpass' || filter.type === 'lowpass') ? filter.Q.value : filter.gain.value;
            // Re-add PADDING_TOP_PX to nodeY so it matches the physical canvas pixel exactly
            const nodeY = PADDING_TOP_PX + centerY - (yValue * pxPerDb);
            
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
                // Skip checking zones for Bands 0 and 4 entirely
                // (They either have no gain, or no Q-factor to stretch)
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
        
        // Check if hovering over canvas mode buttons or top band labels
        const blockWidth = (canvas.width / 5) - 40;
        const gap = 10;
        const totalBlocksWidth = (5 * blockWidth) + (4 * gap);
        const startX = canvas.width - 20 - totalBlocksWidth;
        
        let hoveredTopBand = -1;
        for (let i = 0; i < 5; i++) {
            const blockX = startX + (i * (blockWidth + gap));
            if (mouseX >= blockX && mouseX <= blockX + blockWidth && mouseY >= 10 && mouseY <= PADDING_TOP_PX - 10) {
                hoveredTopBand = i;
            }
        }
        uiState.hoveredTopBand = hoveredTopBand;
        
        const btnWidth = 100;
        const btnHeight = 20;
        const btnY = (PADDING_TOP_PX / 2) - (btnHeight / 2);
        
        const b0BlockX = startX;
        const b4BlockX = startX + (4 * (blockWidth + gap));
        
        const b0BtnX = b0BlockX + (blockWidth / 2) - (btnWidth / 2);
        const b4BtnX = b4BlockX + (blockWidth / 2) - (btnWidth / 2);
        
        const isHoveringB0Btn = mouseX >= b0BtnX && mouseX <= b0BtnX + btnWidth && mouseY >= btnY && mouseY <= btnY + btnHeight;
        const isHoveringB4Btn = mouseX >= b4BtnX && mouseX <= b4BtnX + btnWidth && mouseY >= btnY && mouseY <= btnY + btnHeight;

        // Change cursor to indicate interactability
        if (isHoveringB0Btn || isHoveringB4Btn) {
            canvas.style.cursor = 'pointer';
        } else if (hoveredZone !== -1 && hoveredNode === -1) {
            canvas.style.cursor = 'ew-resize';
        } else {
            canvas.style.cursor = 'default';
        }
    }
});

canvas.addEventListener('mousedown', (e) => {
    if (!audioEngine) return;
    
    // Scale coordinates
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const mouseX = (e.clientX - rect.left) * scaleX;
    const mouseY = (e.clientY - rect.top) * scaleY;

    // Check if a Canvas Mode Button was clicked
    const PADDING_TOP_PX = 60;
    const blockWidth = (canvas.width / 5) - 40;
    const gap = 10;
    const totalBlocksWidth = (5 * blockWidth) + (4 * gap);
    const startX = canvas.width - 20 - totalBlocksWidth;
    
    const btnWidth = 100;
    const btnHeight = 20;
    const btnY = (PADDING_TOP_PX / 2) - (btnHeight / 2);

    // Band 0 Button Hitbox
    const b0BlockX = startX;
    const b0BtnX = b0BlockX + (blockWidth / 2) - (btnWidth / 2);
    if (mouseX >= b0BtnX && mouseX <= b0BtnX + btnWidth && mouseY >= btnY && mouseY <= btnY + btnHeight) {
        const filter = audioEngine.filters[0];
        const band = sliders.b0;
        if (filterModes.b0 === 'highpass') {
            filterModes.b0 = 'lowshelf';
            filter.type = 'lowshelf';
            band.gainContainer.style.display = 'block';
            band.qContainer.style.display = 'none';
            filter.gain.value = parseFloat(band.gain.value);
        } else {
            filterModes.b0 = 'highpass';
            filter.type = 'highpass';
            band.gainContainer.style.display = 'none';
            band.qContainer.style.display = 'block';
        }
        return; // Stop further hit detection if button was clicked
    }

    // Band 4 Button Hitbox
    const b4BlockX = startX + (4 * (blockWidth + gap));
    const b4BtnX = b4BlockX + (blockWidth / 2) - (btnWidth / 2);
    if (mouseX >= b4BtnX && mouseX <= b4BtnX + btnWidth && mouseY >= btnY && mouseY <= btnY + btnHeight) {
        const filter = audioEngine.filters[4];
        const band = sliders.b4;
        if (filterModes.b4 === 'lowpass') {
            filterModes.b4 = 'highshelf';
            filter.type = 'highshelf';
            band.gainContainer.style.display = 'block';
            band.qContainer.style.display = 'none';
            filter.gain.value = parseFloat(band.gain.value);
        } else {
            filterModes.b4 = 'lowpass';
            filter.type = 'lowpass';
            band.gainContainer.style.display = 'none';
            band.qContainer.style.display = 'block';
        }
        return; // Stop further hit detection if button was clicked
    }

    // Check if a Top Band Label was clicked (toggling band on/off)
    for (let i = 0; i < 5; i++) {
        const blockX = startX + (i * (blockWidth + gap));
        if (mouseX >= blockX && mouseX <= blockX + blockWidth && mouseY >= 10 && mouseY <= PADDING_TOP_PX - 10) {
            uiState.bandStates[i] = !uiState.bandStates[i];
            rebuildAudioGraph(audioEngine, uiState.bandStates);
            return; // Stop further hit detection
        }
    }

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
    uiState.hoveredTopBand = -1;
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
    
    if (band.q) {
        band.q.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            filter.Q.value = value;
            band.labels.q.innerText = value.toFixed(1);
        });
    }
    
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