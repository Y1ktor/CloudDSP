// main.js
// Controller file connecting UI, Audio, and Visualizer modules
import { initializeAudioEngine, rebuildAudioGraph } from './audio.js';
import { drawVisualizer, getLogX, getFreqFromX } from './visualizer.js';

const audioElement = document.getElementById('audio-source');
const fileInput = document.getElementById('audio-upload');

// Custom Audio Player UI Elements
const playPauseBtn = document.getElementById('play-pause-btn');
const goToBeginningBtn = document.getElementById('go-to-beginning-btn');
const recordBtn = document.getElementById('record-btn');
const playIcon = document.getElementById('play-icon');
const pauseIcon = document.getElementById('pause-icon');
const seekBar = document.getElementById('seek-bar');
const timeDisplay = document.getElementById('time-display');
const volumeSlider = document.getElementById('volume-slider');
const fileNameDisplay = document.getElementById('file-name-display');
const canvasContainer = document.querySelector('.canvas-container');

// Recording State Machine
let recordState = 'idle'; // 'idle', 'preparing', 'recording'
let countdownInterval = null;
let recordingInterval = null;
let automationData = [];

const updateRecordUI = (content) => {
    recordBtn.innerHTML = content;
};

const circleIcon = `<div style="width: 14px; height: 14px; background-color: #ff3b3b; border-radius: 50%;"></div>`;

recordBtn.addEventListener('click', () => {
    if (recordState === 'idle') {
        // Start prep
        recordState = 'preparing';
        let count = 3;
        updateRecordUI(`<span class="countdown-text">${count}</span>`);
        
        countdownInterval = setInterval(() => {
            count--;
            if (count > 0) {
                updateRecordUI(`<span class="countdown-text">${count}</span>`);
            } else {
                // Prep finished, start recording
                clearInterval(countdownInterval);
                recordState = 'recording';
                updateRecordUI(circleIcon);
                recordBtn.classList.add('is-recording');
                
                // Clear previous automation data
                automationData = [];

                // Start capturing EQ data every 50ms
                recordingInterval = setInterval(() => {
                    if (audioEngine) {
                        const snapshot = {
                            timestamp: audioElement.currentTime,
                            globalPower: uiState.globalPower,
                            bands: audioEngine.filters.map((filter, idx) => ({
                                state: uiState.bandStates[idx],
                                type: filter.type,
                                freq: filter.frequency.value,
                                q: filter.Q.value,
                                gain: filter.gain ? filter.gain.value : 0
                            }))
                        };
                        automationData.push(snapshot);
                    }
                }, 50);

                // Start playback automatically
                if (audioEngine && audioEngine.audioContext.state === 'suspended') {
                    audioEngine.audioContext.resume();
                }
                audioElement.play();
            }
        }, 1000);
        
    } else if (recordState === 'preparing') {
        // Cancel prep
        clearInterval(countdownInterval);
        recordState = 'idle';
        updateRecordUI(circleIcon);
        
    } else if (recordState === 'recording') {
        // Stop recording
        recordState = 'idle';
        recordBtn.classList.remove('is-recording');
        clearInterval(recordingInterval);
        
        // Pause playback automatically
        audioElement.pause();
        
        // Save automation data to browser Session Storage
        if (automationData.length > 0) {
            const dataStr = JSON.stringify(automationData);
            const timestamp = new Date().toISOString().replace(/T/, '_').replace(/\..+/, '').replace(/:/g, '-');
            const saveKey = `cloudDspAutomation_${timestamp}`;
            sessionStorage.setItem(saveKey, dataStr);
            console.log(`Saved automation data (${automationData.length} frames) to sessionStorage.`);
            if (typeof populateImportMenu === 'function') populateImportMenu();
        }
    }
});

// Helper to format seconds to M:SS
const formatTime = (timeInSeconds) => {
    if (isNaN(timeInSeconds)) return "0:00";
    const minutes = Math.floor(timeInSeconds / 60);
    const seconds = Math.floor(timeInSeconds % 60);
    return `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
};

// Go to Beginning
goToBeginningBtn.addEventListener('click', () => {
    audioElement.currentTime = 0;
    if (!audioElement.paused) {
        audioElement.pause();
    }
});

// Play/Pause Toggle
playPauseBtn.addEventListener('click', () => {
    if (audioEngine && audioEngine.audioContext.state === 'suspended') {
        audioEngine.audioContext.resume();
    }
    if (audioElement.paused) {
        audioElement.play();
    } else {
        audioElement.pause();
    }
});

// Update UI when audio plays or pauses
audioElement.addEventListener('play', () => {
    playIcon.style.display = 'none';
    pauseIcon.style.display = 'block';
});
audioElement.addEventListener('pause', () => {
    playIcon.style.display = 'block';
    pauseIcon.style.display = 'none';
});

// Sync Seek Bar & Time Display
audioElement.addEventListener('timeupdate', () => {
    if (audioElement.duration) {
        seekBar.max = audioElement.duration;
        seekBar.value = audioElement.currentTime;
        timeDisplay.innerText = `${formatTime(audioElement.currentTime)} / ${formatTime(audioElement.duration)}`;
    }
});

// Handle Metadata Loaded (duration available)
audioElement.addEventListener('loadedmetadata', () => {
    seekBar.max = audioElement.duration;
    timeDisplay.innerText = `0:00 / ${formatTime(audioElement.duration)}`;
});

// Scrubbing / Seeking
seekBar.addEventListener('input', () => {
    audioElement.currentTime = seekBar.value;
});

// Volume Control
volumeSlider.addEventListener('input', () => {
    audioElement.volume = volumeSlider.value;
});

// Helper to apply marquee animation to overflowing text
const updateScrollingText = (textElement, newText, animPrefix) => {
    textElement.innerText = newText;
    textElement.style.animation = 'none';
    setTimeout(() => {
        const containerWidth = textElement.parentElement.clientWidth;
        const textWidth = textElement.scrollWidth;
        if (textWidth > containerWidth) {
            const animName = `${animPrefix}_${new Date().getTime()}`;
            const styleSheet = document.createElement('style');
            styleSheet.innerText = `
                @keyframes ${animName} {
                    0%, 15% { transform: translateX(0); }
                    85%, 100% { transform: translateX(-${textWidth - containerWidth + 10}px); }
                }
            `;
            document.head.appendChild(styleSheet);
            textElement.style.animation = `${animName} 4s linear infinite alternate`;
        }
    }, 50);
};

// Handle local file uploads to preview audio instantly in the browser
fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        // Create a temporary local memory URL pointing to the uploaded file
        const blobUrl = URL.createObjectURL(file);
        
        // Update the audio element to play the user's file
        audioElement.src = blobUrl;
        
        // Update File Name Display with scrolling helper
        fileNameDisplay.style.color = '#fff';
        updateScrollingText(fileNameDisplay, file.name, 'scrollFileName');
        
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
    b4: getBandUI('b4'),        // Bell 4
    b5: getBandUI('b5', true)   // Lowpass/Highshelf
};

// Track the current mode of the outer filters
const filterModes = {
    b0: 'highpass',
    b5: 'lowpass'
};

let audioEngine = null;

// Track UI interaction state for the canvas
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
    const PADDING_TOP_PX = 40;
    const usableHeight = canvas.height - PADDING_BOTTOM_PX - PADDING_TOP_PX;
    const centerY = usableHeight / 2;
    const maxDeltaY = centerY - 2; 
    const pxPerDb = maxDeltaY / 15;

    // Handle Playhead Dragging Priority
    if (uiState.isDraggingPlayhead) {
        let percent = mouseX / canvas.width;
        if (percent < 0) percent = 0;
        if (percent > 1) percent = 1;
        if (audioElement.duration) {
            audioElement.currentTime = percent * audioElement.duration;
            // Update the HTML seek bar to stay in sync
            seekBar.value = audioElement.currentTime;
            timeDisplay.innerText = `${formatTime(audioElement.currentTime)} / ${formatTime(audioElement.duration)}`;
        }
        return; // Skip other hit detection
    }

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
                // Skip checking zones for Bands 0 and 5 entirely
                // (They either have no gain, or no Q-factor to stretch)
                if (index === 0 || index === 5) return;
                
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
        const blockWidth = (canvas.width / 8) - 10;
        const gap = 8;
        const totalBlocksWidth = (6 * blockWidth) + (5 * gap);
        const startX = canvas.width - 20 - totalBlocksWidth;
        
        let hoveredTopBand = -1;
        let hoveredBadge = -1;
        for (let i = 0; i < 6; i++) {
            const blockX = startX + (i * (blockWidth + gap));
            if (mouseX >= blockX && mouseX <= blockX + blockWidth && mouseY >= 10 && mouseY <= PADDING_TOP_PX - 10) {
                
                // Check explicitly if we hit the right-side badge area for Bands 0 and 5
                let hitBadge = false;
                if (i === 0 || i === 5) {
                    const badgeW = 24;
                    const badgeH = 16;
                    const badgeX = blockX + (blockWidth / 2) + 10;
                    const badgeY = (PADDING_TOP_PX / 2) - (badgeH / 2);
                    if (mouseX >= badgeX && mouseX <= badgeX + badgeW && mouseY >= badgeY && mouseY <= badgeY + badgeH) {
                        hoveredBadge = i;
                        hitBadge = true;
                    }
                }
                
                // If not hitting the badge, then we are hovering the main band
                if (!hitBadge) {
                    hoveredTopBand = i;
                }
            }
        }
        uiState.hoveredTopBand = hoveredTopBand;
        uiState.hoveredBadge = hoveredBadge;
        
        // Check if hovering over power button
        const powerX = 40;
        const powerY = PADDING_TOP_PX / 2;
        const powerRadius = 12;
        const distToPower = Math.sqrt(Math.pow(mouseX - powerX, 2) + Math.pow(mouseY - powerY, 2));
        uiState.hoveredPowerBtn = distToPower <= powerRadius + 4;

        // Check if hovering over playhead
        let hoveredPlayhead = false;
        if (audioElement.duration) {
            const playheadX = (audioElement.currentTime / audioElement.duration) * canvas.width;
            const playheadY = PADDING_TOP_PX + usableHeight;
            const distToPlayhead = Math.sqrt(Math.pow(mouseX - playheadX, 2) + Math.pow(mouseY - playheadY, 2));
            if (distToPlayhead <= 12) {
                hoveredPlayhead = true;
            }
        }
        uiState.hoveredPlayhead = hoveredPlayhead;

        // Change cursor to indicate interactability
        if (uiState.hoveredPowerBtn || uiState.hoveredBadge !== -1 || hoveredTopBand !== -1) {
            canvas.style.cursor = 'pointer';
        } else if ((hoveredZone !== -1 && hoveredNode === -1) || uiState.hoveredPlayhead) {
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

    // Handle Playhead Dragging Priority
    if (uiState.hoveredPlayhead) {
        uiState.isDraggingPlayhead = true;
        return; // Stop further hit detection
    }

    // Check if a Canvas Mode Button was clicked
    const PADDING_TOP_PX = 40;
    const blockWidth = (canvas.width / 8) - 10;
    const gap = 8;
    const totalBlocksWidth = (6 * blockWidth) + (5 * gap);
    const startX = canvas.width - 20 - totalBlocksWidth;
    
    // Check Badge Hits for Mode Toggles (Bands 0 and 5)
    for (let i of [0, 5]) {
        const blockX = startX + (i * (blockWidth + gap));
        const badgeW = 24;
        const badgeH = 16;
        const badgeX = blockX + (blockWidth / 2) + 10;
        const badgeY = (PADDING_TOP_PX / 2) - (badgeH / 2);
        
        if (mouseX >= badgeX && mouseX <= badgeX + badgeW && mouseY >= badgeY && mouseY <= badgeY + badgeH) {
            const filter = audioEngine.filters[i];
            const band = sliders[`b${i}`];
            if (i === 0) {
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
            } else if (i === 5) {
                if (filterModes.b5 === 'lowpass') {
                    filterModes.b5 = 'highshelf';
                    filter.type = 'highshelf';
                    band.gainContainer.style.display = 'block';
                    band.qContainer.style.display = 'none';
                    filter.gain.value = parseFloat(band.gain.value);
                } else {
                    filterModes.b5 = 'lowpass';
                    filter.type = 'lowpass';
                    band.gainContainer.style.display = 'none';
                    band.qContainer.style.display = 'block';
                }
            }
            return; // Stop further hit detection
        }
    }

    // Check Global Power Button Click
    const powerX = 40;
    const powerY = PADDING_TOP_PX / 2;
    const powerRadius = 12;
    const distToPower = Math.sqrt(Math.pow(mouseX - powerX, 2) + Math.pow(mouseY - powerY, 2));
    if (distToPower <= powerRadius + 4) {
        uiState.globalPower = !uiState.globalPower;
        
        if (!uiState.globalPower) {
            // Turning OFF: Save current state, then turn all off
            uiState.savedBandStates = [...uiState.bandStates];
            uiState.bandStates.fill(false);
        } else {
            // Turning ON: Restore saved state if exists, else turn all on
            if (uiState.savedBandStates) {
                uiState.bandStates = [...uiState.savedBandStates];
                uiState.savedBandStates = null;
            } else {
                uiState.bandStates.fill(true);
            }
        }
        
        rebuildAudioGraph(audioEngine, uiState.bandStates);
        return;
    }

    // Check if a Top Band Label was clicked (toggling band on/off)
    for (let i = 0; i < 6; i++) {
        const blockX = startX + (i * (blockWidth + gap));
        if (mouseX >= blockX && mouseX <= blockX + blockWidth && mouseY >= 10 && mouseY <= PADDING_TOP_PX - 10) {
            uiState.bandStates[i] = !uiState.bandStates[i];
            
            if (uiState.bandStates[i]) {
                // Turned a band ON: ensure global power is on and discard any saved state
                uiState.globalPower = true;
                uiState.savedBandStates = null;
            } else if (uiState.bandStates.every(state => !state)) {
                // Turned the LAST active band OFF: set global power off and clear saved state
                uiState.globalPower = false;
                uiState.savedBandStates = null;
            }

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
    uiState.isDraggingPlayhead = false;
});

canvas.addEventListener('mouseleave', () => {
    uiState.activeDragBand = -1;
    uiState.activeZoneDragBand = -1;
    uiState.isDraggingPlayhead = false;
    uiState.hoveredNode = -1;
    uiState.hoveredZone = -1;
    uiState.hoveredTopBand = -1;
    uiState.hoveredBadge = -1;
    uiState.hoveredPowerBtn = false;
    uiState.hoveredPlayhead = false;
    canvas.style.cursor = 'default';
});

// Boot up the audio context and routing immediately
audioEngine = initializeAudioEngine(audioElement, sliders);

// Bind UI Sliders to the Web Audio Filters for each band
[sliders.b0, sliders.b1, sliders.b2, sliders.b3, sliders.b4, sliders.b5].forEach((band, index) => {
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
    uiState, // Pass interaction state to canvas
    audioElement // Pass audio element for playhead
);

// When the user actually plays the audio, resume the suspended context
audioElement.addEventListener('play', () => {
    if (audioEngine && audioEngine.audioContext.state === 'suspended') {
        audioEngine.audioContext.resume();
    }
});

// Custom Automation Dropdown Logic
const autoDropdown = document.getElementById('automation-dropdown');
const autoSelected = autoDropdown.querySelector('.select-selected');
const autoSelectedText = autoDropdown.querySelector('.select-text');
const autoItems = autoDropdown.querySelector('.select-items');
const importOption = document.getElementById('import-option');
const importSubmenu = document.getElementById('import-submenu');

autoSelected.addEventListener('click', (e) => {
    e.stopPropagation();
    const isShowing = autoItems.style.display === 'block';
    autoItems.style.display = isShowing ? 'none' : 'block';
    importSubmenu.style.display = 'none'; // Hide submenu when toggling main menu
});

importOption.addEventListener('click', (e) => {
    e.stopPropagation();
    const isShowing = importSubmenu.style.display === 'block';
    importSubmenu.style.display = isShowing ? 'none' : 'block';
});

document.addEventListener('click', () => {
    autoItems.style.display = 'none';
    importSubmenu.style.display = 'none';
});

autoDropdown.querySelectorAll('.select-items > li').forEach(item => {
    if (item.id === 'import-option') return;
    item.addEventListener('click', (e) => {
        e.stopPropagation();
        const itemName = item.childNodes[0].nodeValue.trim();
        updateScrollingText(autoSelectedText, itemName, 'scrollAutoName');
        autoItems.style.display = 'none';
        importSubmenu.style.display = 'none';
        
        if (item.dataset.value === 'new') {
            automationData = [];
            console.log("Started new automation file");
        } else if (item.dataset.value === 'save') {
            console.log("Manual save triggered");
        }
    });
});

function populateImportMenu() {
    importSubmenu.innerHTML = '';
    let found = false;
    for (let i = 0; i < sessionStorage.length; i++) {
        const key = sessionStorage.key(i);
        if (key && key.startsWith('cloudDspAutomation_')) {
            found = true;
            const li = document.createElement('li');
            const displayName = key.replace('cloudDspAutomation_', '');
            li.innerText = displayName;
            
            li.addEventListener('click', (e) => {
                e.stopPropagation();
                updateScrollingText(autoSelectedText, displayName, 'scrollAutoName');
                autoItems.style.display = 'none';
                importSubmenu.style.display = 'none';
                // Future: load automation data and apply it
                console.log(`Loaded ${key} from session storage`);
            });
            importSubmenu.appendChild(li);
        }
    }
    if (!found) {
        const li = document.createElement('li');
        li.innerText = 'None';
        li.classList.add('disabled');
        importSubmenu.appendChild(li);
    }
}

// Initialize the import list on load
populateImportMenu();