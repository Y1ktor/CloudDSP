// visualizer.js
// Handles all Canvas drawing and FFT analysis rendering

// ==========================================
// TUNABLE VISUALIZER CONSTANTS
// ==========================================
const MIN_FREQ_HZ = 20;               // Left boundary of the EQ graph
const MAX_FREQ_HZ = 20000;            // Right boundary of the EQ graph
const PADDING_BOTTOM_PX = 20;         // Space reserved at the bottom for text labels
const PADDING_TOP_PX = 60;            // Space reserved at the top (3x bottom padding)
const BASE_HEIGHT_RATIO = 0.7;        // Max height of bars relative to the center line (0.7 = 70%)
const BASE_VISUAL_MULTIPLIER = 1.1;   // General exaggeration for all audio bars
const EQ_DELTA_MULTIPLIER = 1.5;      // Exaggeration applied exclusively to changes caused by the EQ sliders
const GRID_FREQUENCIES = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
const BAND_COLORS = [
    '255, 165, 0',   // Band 0 (Light Orange)
    '255, 235, 59',  // Band 1 (Light Yellow)
    '100, 255, 100', // Band 2 (Light Green)
    '100, 200, 255', // Band 3 (Light Blue)
    '200, 100, 255'  // Band 4 (Light Purple)
];

const canvas = document.getElementById('visualizer');
const canvasCtx = canvas.getContext('2d');

/**
 * Maps a horizontal X-axis pixel coordinate back to its corresponding frequency (Hz) using the logarithmic scale.
 * This is the exact mathematical inverse of getLogX.
 * 
 * @param {number} x - The horizontal pixel coordinate on the canvas.
 * @param {number} width - The total width of the canvas in pixels.
 * @returns {number} The frequency in Hertz corresponding to that pixel.
 */
export function getFreqFromX(x, width) {
    if (x < 0) x = 0;
    if (x > width) x = width;

    const minLog = Math.log10(MIN_FREQ_HZ);
    const maxLog = Math.log10(MAX_FREQ_HZ);
    const percent = x / width;
    const logFreq = percent * (maxLog - minLog) + minLog;
    return Math.pow(10, logFreq);
}

/**
 * Maps a given frequency (Hz) to a horizontal X-axis pixel coordinate using a logarithmic scale.
 * This mimics human hearing and professional DAW layouts, dedicating more visual space to mid-range frequencies.
 * 
 * @param {number} frequency - The frequency in Hertz to be plotted (e.g., 1000).
 * @param {number} width - The total width of the canvas in pixels.
 * @returns {number} The exact horizontal pixel coordinate (X) where the frequency should be drawn.
 */
export function getLogX(frequency, width) {
    if (frequency < MIN_FREQ_HZ) frequency = MIN_FREQ_HZ;
    if (frequency > MAX_FREQ_HZ) frequency = MAX_FREQ_HZ;
    
    const minLog = Math.log10(MIN_FREQ_HZ);
    const maxLog = Math.log10(MAX_FREQ_HZ);
    const percent = (Math.log10(frequency) - minLog) / (maxLog - minLog);
    return percent * width;
}

/**
 * Draws the static background elements: the frequency grid lines, text labels, and the 0dB center line.
 * 
 * @param {CanvasRenderingContext2D} ctx - The 2D rendering context for the canvas.
 * @param {number} width - The total width of the canvas.
 * @param {number} usableHeight - The height of the canvas excluding the bottom text padding.
 * @param {number} centerY - The Y coordinate representing the 0dB baseline.
 */
function drawGridAndLabels(ctx, width, usableHeight, centerY) {
    ctx.fillStyle = 'rgba(255, 255, 255, 0.5)';
    ctx.font = '10px Arial';
    
    GRID_FREQUENCIES.forEach(freq => {
        const x = getLogX(freq, width);
        
        ctx.beginPath();
        // Start drawing from PADDING_TOP_PX instead of 0
        ctx.moveTo(x, PADDING_TOP_PX);
        ctx.lineTo(x, PADDING_TOP_PX + usableHeight);
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
        ctx.lineWidth = 1;
        ctx.stroke();
        
        let label = freq >= 1000 ? `${freq/1000}k` : freq;
        
        if (freq === MIN_FREQ_HZ) {
            ctx.textAlign = 'left';
        } else if (freq === MAX_FREQ_HZ) {
            ctx.textAlign = 'right';
        } else {
            ctx.textAlign = 'center';
        }
        // Text is drawn relative to the bottom padding boundary
        ctx.fillText(label, x, PADDING_TOP_PX + usableHeight + PADDING_BOTTOM_PX - 5);
    });

    // Draw the Top Boundary Line (Sealing the wavebar space)
    ctx.beginPath();
    ctx.moveTo(0, PADDING_TOP_PX);
    ctx.lineTo(width, PADDING_TOP_PX);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)'; // Matches vertical grid lines
    ctx.lineWidth = 1;
    ctx.stroke();

    // Draw the Base Gain Line (0dB)
    ctx.beginPath();
    ctx.moveTo(0, PADDING_TOP_PX + centerY);
    ctx.lineTo(width, PADDING_TOP_PX + centerY);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    ctx.lineWidth = 1;
    ctx.stroke();
}

/**
 * Draws the top navigation bar, including the 5 colored sections, text labels, and canvas buttons.
 * 
 * @param {CanvasRenderingContext2D} ctx - The 2D rendering context for the canvas.
 * @param {number} width - The total width of the canvas.
 * @param {Array<BiquadFilterNode>} filters - The array of active Web Audio EQ filters (used to get the mode for buttons).
 */
function drawTopNavBar(ctx, width, filters, uiState) {
    // Draw Power Button
    const powerX = 40;
    const powerY = PADDING_TOP_PX / 2;
    const powerRadius = 12;
    
    let powerOn = uiState && uiState.globalPower !== undefined ? uiState.globalPower : true;
    let powerHovered = uiState && uiState.hoveredPowerBtn;
    
    let powerOpacity = powerOn ? 0.6 : 0.4;
    if (powerHovered) powerOpacity += 0.3;
    
    const powerColor = powerOn ? `100, 255, 100` : `255, 100, 100`; // Green for ON, Red for OFF
    
    ctx.beginPath();
    // Draw arc from top-right down to top-left, leaving a gap at the top
    ctx.arc(powerX, powerY, powerRadius, -Math.PI * 0.35, Math.PI * 1.35);
    ctx.strokeStyle = `rgba(${powerColor}, ${powerOpacity})`;
    ctx.lineWidth = 2.5;
    ctx.stroke();

    ctx.beginPath();
    // Draw the vertical line through the gap
    ctx.moveTo(powerX, powerY - powerRadius + 2);
    ctx.lineTo(powerX, powerY + 2);
    ctx.stroke();

    // Draw 5 Background Sections in the Top Padding
    const blockWidth = (width / 5) - 40; // Maintain original block width
    const gap = 10; // Reduced gap between bands
    const totalBlocksWidth = (5 * blockWidth) + (4 * gap);
    const startX = width - 20 - totalBlocksWidth; // Anchor exactly to the right edge (minus 20px padding)
    
    const sectionLabels = [
        "", // Band 0 has HTML buttons overlaid
        "Band 1",
        "Band 2",
        "Band 3",
        ""  // Band 4 has HTML buttons overlaid
    ];

    for (let i = 0; i < 5; i++) {
        const blockX = startX + (i * (blockWidth + gap));
        
        // Draw the colored background block
        let isBandOn = uiState && uiState.bandStates ? uiState.bandStates[i] : true;
        let opacity = isBandOn ? 0.6 : 0.2;
        if (uiState && uiState.hoveredTopBand === i) {
            opacity = isBandOn ? 0.8 : 0.5;
        }
        ctx.fillStyle = `rgba(${BAND_COLORS[i]}, ${opacity})`;
        ctx.fillRect(blockX, 10, blockWidth, PADDING_TOP_PX - 20);

        if (sectionLabels[i]) {
            // Draw the text labels directly onto the canvas for the middle bands
            ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'; // Bright white text for readability against colors
            ctx.font = 'bold 12px Arial';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(sectionLabels[i], blockX + (blockWidth / 2), PADDING_TOP_PX / 2);
        } else {
            // Draw canvas-based Mode Toggle buttons for Bands 0 and 4
            const modeName = filters[i].type;
            const btnText = `Mode: ${modeName.charAt(0).toUpperCase() + modeName.slice(1)}`;
            
            // Button Box
            const btnWidth = 100;
            const btnHeight = 20;
            const btnX = blockX + (blockWidth / 2) - (btnWidth / 2);
            const btnY = (PADDING_TOP_PX / 2) - (btnHeight / 2);
            
            ctx.fillStyle = 'rgba(255, 255, 255, 0.2)';
            ctx.fillRect(btnX, btnY, btnWidth, btnHeight);
            
            // Button Border
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
            ctx.lineWidth = 1;
            ctx.strokeRect(btnX, btnY, btnWidth, btnHeight);
            
            // Button Text
            ctx.fillStyle = 'white';
            ctx.font = '12px Arial';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(btnText, btnX + (btnWidth / 2), btnY + (btnHeight / 2));
        }
    }
}

/**
 * Calculates and draws the bouncing FFT audio bars, exaggerating the visual impact of the EQ delta.
 * 
 * @param {CanvasRenderingContext2D} ctx - The 2D rendering context for the canvas.
 * @param {AnalyserNode} preAnalyser - Analyser node for the raw audio.
 * @param {Uint8Array} preDataArray - Buffer containing raw audio FFT data.
 * @param {AnalyserNode} postAnalyser - Analyser node for the EQ'd audio.
 * @param {Uint8Array} postDataArray - Buffer containing EQ'd audio FFT data.
 * @param {number} sampleRate - The audio context's sample rate (used to calculate Nyquist).
 * @param {number} width - The total width of the canvas.
 * @param {number} usableHeight - The height of the canvas excluding the bottom text padding.
 */
function drawAudioBars(ctx, preAnalyser, preDataArray, postAnalyser, postDataArray, sampleRate, width, usableHeight) {
    const nyquist = sampleRate / 2;
    const bufferLength = preAnalyser.frequencyBinCount;

    for (let i = 0; i < bufferLength; i++) {
        const binFrequency = i * (nyquist / bufferLength);
        if (binFrequency < MIN_FREQ_HZ) continue;

        const x = getLogX(binFrequency, width);
        const nextBinFrequency = (i + 1) * (nyquist / bufferLength);
        const nextX = getLogX(nextBinFrequency, width);
        const barWidth = Math.max(1, nextX - x);

        const maxBarHeight = usableHeight * BASE_HEIGHT_RATIO; 
        
        const preVolume = preDataArray[i] / 255;
        const postVolume = postDataArray[i] / 255;
        
        const eqDelta = postVolume - preVolume;
        const exaggeratedDelta = eqDelta * EQ_DELTA_MULTIPLIER;
        
        let finalNormalizedVolume = preVolume + exaggeratedDelta;
        if (finalNormalizedVolume < 0) finalNormalizedVolume = 0;
        
        let barHeight = Math.pow(finalNormalizedVolume, 2) * maxBarHeight;
        barHeight *= BASE_VISUAL_MULTIPLIER;
        if (barHeight > usableHeight) barHeight = usableHeight;

        const percentX = x / width;
        const r = 255 * (1 - percentX);
        const g = 255 * (percentX < 0.5 ? percentX * 2 : (1 - percentX) * 2);
        const b = 255 * percentX;

        ctx.fillStyle = `rgb(${r},${g},${b})`;
        // Shift the entire bar block downward by PADDING_TOP_PX
        ctx.fillRect(x, PADDING_TOP_PX + usableHeight - barHeight, barWidth, barHeight);
    }
}

/**
 * Calculates and draws the polynomial EQ response curves (both individual faint curves and the solid combined curve).
 * 
 * @param {CanvasRenderingContext2D} ctx - The 2D rendering context for the canvas.
 * @param {Array<BiquadFilterNode>} filters - The array of EQ filter nodes.
 * @param {number} width - The total width of the canvas.
 * @param {number} centerY - The Y coordinate representing the 0dB baseline.
 * @param {Object} uiState - The current user interaction state to highlight hovered zones.
 */
function drawEQCurves(ctx, filters, width, centerY, uiState) {
    const numPoints = width;
    const frequencyArray = new Float32Array(numPoints);
    
    const minLog = Math.log10(MIN_FREQ_HZ);
    const maxLog = Math.log10(MAX_FREQ_HZ);
    for (let i = 0; i < numPoints; i++) {
        const percent = i / numPoints;
        const logFreq = percent * (maxLog - minLog) + minLog;
        frequencyArray[i] = Math.pow(10, logFreq);
    }

    const magResponses = filters.map(filter => {
        const magResponse = new Float32Array(numPoints);
        const phaseResponse = new Float32Array(numPoints);
        filter.getFrequencyResponse(frequencyArray, magResponse, phaseResponse);
        return magResponse;
    });

    const maxDeltaY = centerY - 2; 
    const pxPerDb = maxDeltaY / 15;

    // Draw Combined Polynomial DAW-style EQ Curve (Background)
    ctx.beginPath();
    for (let i = 0; i < numPoints; i++) {
        let totalMag = 1.0; 
        for (let j = 0; j < magResponses.length; j++) {
            if (uiState && uiState.bandStates && !uiState.bandStates[j]) continue;
            totalMag *= magResponses[j][i];
        }
        
        const db = 20 * Math.log10(totalMag);
        const yOffset = db * pxPerDb;
        // Shift targetY down by PADDING_TOP_PX
        const targetY = PADDING_TOP_PX + centerY - yOffset;
        
        if (i === 0) {
            ctx.moveTo(i, targetY);
        } else {
            ctx.lineTo(i, targetY);
        }
    }

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)';
    ctx.lineWidth = 3;
    ctx.stroke();
    
    // Fill down to the adjusted centerY
    ctx.lineTo(width, PADDING_TOP_PX + centerY);
    ctx.lineTo(0, PADDING_TOP_PX + centerY);
    ctx.fillStyle = 'rgba(255, 255, 255, 0.1)';
    ctx.fill();

    // Draw Individual Faint Filter Curves (Foreground)
    magResponses.forEach((magResponse, filterIndex) => {
        ctx.beginPath();
        for (let i = 0; i < numPoints; i++) {
            const db = 20 * Math.log10(magResponse[i]);
            const yOffset = db * pxPerDb;
            // Shift targetY down by PADDING_TOP_PX
            const targetY = PADDING_TOP_PX + centerY - yOffset;
            
            if (i === 0) {
                ctx.moveTo(i, targetY);
            } else {
                ctx.lineTo(i, targetY);
            }
        }

        let lineWidth = 1.5;
        let isBandOn = uiState && uiState.bandStates ? uiState.bandStates[filterIndex] : true;
        let opacity = isBandOn ? 0.25 : 0.2; 
        
        if (uiState && (uiState.hoveredZone === filterIndex || uiState.hoveredTopBand === filterIndex)) {
            lineWidth = 1.5; 
            opacity = isBandOn ? 0.8 : 0.2;   
        }

        ctx.strokeStyle = `rgba(${BAND_COLORS[filterIndex]}, ${opacity})`;
        ctx.lineWidth = lineWidth;
        ctx.stroke();
    });
}

/**
 * Draws the interactive control nodes (circles) at the peaks of each EQ filter curve.
 * 
 * @param {CanvasRenderingContext2D} ctx - The 2D rendering context for the canvas.
 * @param {Array<BiquadFilterNode>} filters - The array of EQ filter nodes to determine node positions.
 * @param {number} width - The total width of the canvas.
 * @param {number} centerY - The Y coordinate representing the 0dB baseline.
 * @param {Object} uiState - The current user interaction state to handle hover and active styling.
 */
function drawControlNodes(ctx, filters, width, centerY, uiState) {
    const maxDeltaY = centerY - 2; 
    const pxPerDb = maxDeltaY / 15;
    
    filters.forEach((filter, index) => {
        const freq = filter.frequency.value;
        let yOffset;
        
        if (filter.type === 'highpass' || filter.type === 'lowpass') {
            // For Cut filters, Y-axis represents Q (Resonance)
            yOffset = filter.Q.value * pxPerDb;
        } else {
            // For Bell/Shelf filters, Y-axis represents Gain
            yOffset = filter.gain.value * pxPerDb;
        }

        const x = getLogX(freq, width);
        // Shift targetY down by PADDING_TOP_PX
        const targetY = PADDING_TOP_PX + centerY - yOffset;

        let radius = 6;
        let opacity = 0.4; 
        let isInteractive = false;
        
        if (uiState.hoveredNode === index || uiState.activeDragBand === index) {
            opacity = 1.0;
            isInteractive = true;
            if (uiState.activeDragBand === index) radius = 8;
        }

        ctx.beginPath();
        ctx.arc(x, targetY, radius, 0, 2 * Math.PI);
        ctx.fillStyle = `rgba(${BAND_COLORS[index]}, ${opacity})`;
        ctx.fill();
        
        // Only draw the white border ring if the node is actively hovered or dragged
        if (isInteractive) {
            ctx.lineWidth = 2;
            ctx.strokeStyle = `rgba(255, 255, 255, ${opacity + 0.2})`;
            ctx.stroke();
        }
    });
}

/**
 * The main recursive animation loop that orchestrates the drawing of all visualizer layers.
 * 
 * @param {AnalyserNode} preAnalyser - Analyser node for the raw audio.
 * @param {Uint8Array} preDataArray - Buffer containing raw audio FFT data.
 * @param {AnalyserNode} postAnalyser - Analyser node for the EQ'd audio.
 * @param {Uint8Array} postDataArray - Buffer containing EQ'd audio FFT data.
 * @param {AudioContext} audioContext - The main audio context, used here to retrieve the sample rate.
 * @param {Array<BiquadFilterNode>} filters - The array of active Web Audio EQ filters.
 * @param {Object} uiState - State object tracking user mouse interaction.
 */
export function drawVisualizer(preAnalyser, preDataArray, postAnalyser, postDataArray, audioContext, filters, uiState) {
    requestAnimationFrame(() => drawVisualizer(preAnalyser, preDataArray, postAnalyser, postDataArray, audioContext, filters, uiState));

    preAnalyser.getByteFrequencyData(preDataArray);
    postAnalyser.getByteFrequencyData(postDataArray);

    canvasCtx.fillStyle = '#222';
    canvasCtx.fillRect(0, 0, canvas.width, canvas.height);

    // The usable height for rendering EQ and bars excludes BOTH top and bottom padding
    const usableHeight = canvas.height - PADDING_BOTTOM_PX - PADDING_TOP_PX;
    
    // centerY represents the 0dB line RELATIVE to the top of the usable area.
    // When drawing, we will physically shift it down by PADDING_TOP_PX.
    const centerY = usableHeight / 2;

    drawGridAndLabels(canvasCtx, canvas.width, usableHeight, centerY);
    drawTopNavBar(canvasCtx, canvas.width, filters, uiState);
    drawAudioBars(canvasCtx, preAnalyser, preDataArray, postAnalyser, postDataArray, audioContext.sampleRate, canvas.width, usableHeight);
    drawEQCurves(canvasCtx, filters, canvas.width, centerY, uiState);
    if (uiState) {
        drawControlNodes(canvasCtx, filters, canvas.width, centerY, uiState);
    }
}