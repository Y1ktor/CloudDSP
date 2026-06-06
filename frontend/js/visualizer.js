// visualizer.js
// Handles all Canvas drawing and FFT analysis rendering

// ==========================================
// TUNABLE VISUALIZER CONSTANTS
// ==========================================
const MIN_FREQ_HZ = 20;               // Left boundary of the EQ graph
const MAX_FREQ_HZ = 20000;            // Right boundary of the EQ graph
const PADDING_BOTTOM_PX = 20;         // Space reserved at the bottom for text labels
const BASE_HEIGHT_RATIO = 0.7;        // Max height of bars relative to the center line (0.7 = 70%)
const BASE_VISUAL_MULTIPLIER = 1.1;   // General exaggeration for all audio bars
const EQ_DELTA_MULTIPLIER = 1.5;      // Exaggeration applied exclusively to changes caused by the EQ sliders
const GRID_FREQUENCIES = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];

const canvas = document.getElementById('visualizer');
const canvasCtx = canvas.getContext('2d');

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
 * Maps a horizontal X-axis pixel coordinate back to its corresponding frequency (Hz) using the logarithmic scale.
 * This is the exact mathematical inverse of getLogX.
 * 
 * @param {number} x - The horizontal pixel coordinate on the canvas.
 * @param {number} width - The total width of the canvas in pixels.
 * @returns {number} The frequency in Hertz corresponding to that pixel.
 */
export function getFreqFromX(x, width) {
    // Clamp X to the canvas boundaries
    if (x < 0) x = 0;
    if (x > width) x = width;

    const minLog = Math.log10(MIN_FREQ_HZ);
    const maxLog = Math.log10(MAX_FREQ_HZ);
    
    // Calculate the percentage across the canvas
    const percent = x / width;
    
    // Reverse the logarithmic calculation
    const logFreq = percent * (maxLog - minLog) + minLog;
    return Math.pow(10, logFreq);
}

/**
 * The main recursive animation loop that draws the spectrum analyzer and EQ curves onto the canvas.
 * It schedules itself to run on the browser's next animation frame (typically 60fps).
 * 
 * @param {AnalyserNode} preAnalyser - The Web Audio node providing raw audio FFT data.
 * @param {Uint8Array} preDataArray - The array buffer into which the raw audio FFT data is written.
 * @param {AnalyserNode} postAnalyser - The Web Audio node providing EQ-modified audio FFT data.
 * @param {Uint8Array} postDataArray - The array buffer into which the EQ-modified audio FFT data is written.
 * @param {AudioContext} audioContext - The main audio context, used here to retrieve the sample rate.
 * @param {Array<BiquadFilterNode>} filters - The array of active Web Audio EQ filters, used to calculate the response curve.
 * @param {Object} uiState - State object tracking user mouse interaction (hovered or actively dragged bands).
 * @returns {void} This function does not return a value; it recursively calls itself via requestAnimationFrame.
 */
export function drawVisualizer(preAnalyser, preDataArray, postAnalyser, postDataArray, audioContext, filters, uiState) {
    requestAnimationFrame(() => drawVisualizer(preAnalyser, preDataArray, postAnalyser, postDataArray, audioContext, filters, uiState));

    // Fetch both raw audio data and EQ-modified audio data
    preAnalyser.getByteFrequencyData(preDataArray);
    postAnalyser.getByteFrequencyData(postDataArray);

    canvasCtx.fillStyle = '#222';
    canvasCtx.fillRect(0, 0, canvas.width, canvas.height);

    const usableHeight = canvas.height - PADDING_BOTTOM_PX;
    const centerY = usableHeight / 2;

    // 1. Draw Frequency Grid Lines & Labels
    canvasCtx.fillStyle = 'rgba(255, 255, 255, 0.5)';
    canvasCtx.font = '10px Arial';
    
    GRID_FREQUENCIES.forEach(freq => {
        const x = getLogX(freq, canvas.width);
        
        canvasCtx.beginPath();
        canvasCtx.moveTo(x, 0);
        canvasCtx.lineTo(x, usableHeight);
        canvasCtx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
        canvasCtx.lineWidth = 1;
        canvasCtx.stroke();
        
        let label = freq >= 1000 ? `${freq/1000}k` : freq;
        
        if (freq === MIN_FREQ_HZ) {
            canvasCtx.textAlign = 'left';
        } else if (freq === MAX_FREQ_HZ) {
            canvasCtx.textAlign = 'right';
        } else {
            canvasCtx.textAlign = 'center';
        }
        canvasCtx.fillText(label, x, canvas.height - 5);
    });

    // 2. Draw the Base Gain Line (0dB)
    canvasCtx.beginPath();
    canvasCtx.moveTo(0, centerY);
    canvasCtx.lineTo(canvas.width, centerY);
    canvasCtx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    canvasCtx.lineWidth = 1;
    canvasCtx.stroke();

    // 3. Draw Audio Bars
    const nyquist = audioContext.sampleRate / 2;
    const bufferLength = preAnalyser.frequencyBinCount;

    for (let i = 0; i < bufferLength; i++) {
        const binFrequency = i * (nyquist / bufferLength);
        if (binFrequency < MIN_FREQ_HZ) continue;

        const x = getLogX(binFrequency, canvas.width);
        const nextBinFrequency = (i + 1) * (nyquist / bufferLength);
        const nextX = getLogX(nextBinFrequency, canvas.width);
        const barWidth = Math.max(1, nextX - x);

        const maxBarHeight = usableHeight * BASE_HEIGHT_RATIO; 
        
        // Normalize both raw and EQ'd volumes to 0.0 - 1.0
        const preVolume = preDataArray[i] / 255;
        const postVolume = postDataArray[i] / 255;
        
        // Calculate exactly how much the EQ changed this frequency
        const eqDelta = postVolume - preVolume;
        
        // Exaggerate ONLY the EQ change
        const exaggeratedDelta = eqDelta * EQ_DELTA_MULTIPLIER;
        
        // Reapply the exaggerated change to the raw track volume
        let finalNormalizedVolume = preVolume + exaggeratedDelta;
        
        if (finalNormalizedVolume < 0) finalNormalizedVolume = 0;
        
        let barHeight = Math.pow(finalNormalizedVolume, 2) * maxBarHeight;
        barHeight *= BASE_VISUAL_MULTIPLIER;
        if (barHeight > usableHeight) barHeight = usableHeight;

        const percentX = x / canvas.width;
        const r = 255 * (1 - percentX);
        const g = 255 * (percentX < 0.5 ? percentX * 2 : (1 - percentX) * 2);
        const b = 255 * percentX;

        canvasCtx.fillStyle = `rgb(${r},${g},${b})`;
        canvasCtx.fillRect(x, usableHeight - barHeight, barWidth, barHeight);
    }

    // 4. Draw Polynomial DAW-style EQ Curve
    const numPoints = canvas.width;
    const frequencyArray = new Float32Array(numPoints);
    
    // Generate the array of frequencies mapped exactly to our canvas X pixels
    const minLog = Math.log10(MIN_FREQ_HZ);
    const maxLog = Math.log10(MAX_FREQ_HZ);
    for (let i = 0; i < numPoints; i++) {
        const percent = i / numPoints;
        const logFreq = percent * (maxLog - minLog) + minLog;
        frequencyArray[i] = Math.pow(10, logFreq);
    }

    // Get frequency response for each filter
    const magResponses = filters.map(filter => {
        const magResponse = new Float32Array(numPoints);
        const phaseResponse = new Float32Array(numPoints);
        filter.getFrequencyResponse(frequencyArray, magResponse, phaseResponse);
        return magResponse;
    });

    const maxDeltaY = centerY - 2; 
    const pxPerDb = maxDeltaY / 15;

    // 4A. Draw Combined Polynomial DAW-style EQ Curve (Background)
    canvasCtx.beginPath();
    for (let i = 0; i < numPoints; i++) {
        // Multiply the linear amplitude of all active filters
        let totalMag = 1.0; 
        for (let j = 0; j < magResponses.length; j++) {
            totalMag *= magResponses[j][i];
        }
        
        // Convert the combined linear amplitude back to Decibels (dB)
        const db = 20 * Math.log10(totalMag);
        
        // Map dB to the canvas Y pixel coordinate
        const yOffset = db * pxPerDb;
        const targetY = centerY - yOffset;
        
        if (i === 0) {
            canvasCtx.moveTo(i, targetY);
        } else {
            canvasCtx.lineTo(i, targetY);
        }
    }

    // Style and stroke the main curve line
    canvasCtx.strokeStyle = 'rgba(255, 255, 255, 0.9)'; // Bright white line
    canvasCtx.lineWidth = 3;
    canvasCtx.stroke();
    
    // Fill a faint transparent color underneath the curve down to the 0dB center line
    canvasCtx.lineTo(canvas.width, centerY);
    canvasCtx.lineTo(0, centerY);
    canvasCtx.fillStyle = 'rgba(255, 255, 255, 0.1)';
    canvasCtx.fill();

    // 4B. Draw Individual Faint Filter Curves (Foreground)
    const filterColors = [
        'rgba(255, 50, 50, 0.6)',   // Band 1 (Red, faint)
        'rgba(50, 255, 50, 0.6)',   // Band 2 (Green, faint)
        'rgba(50, 150, 255, 0.6)'   // Band 3 (Blue, faint)
    ];

    magResponses.forEach((magResponse, filterIndex) => {
        canvasCtx.beginPath();
        for (let i = 0; i < numPoints; i++) {
            // Convert linear amplitude to Decibels (dB)
            const db = 20 * Math.log10(magResponse[i]);
            
            // Map dB to the canvas Y pixel coordinate
            const yOffset = db * pxPerDb;
            const targetY = centerY - yOffset;
            
            if (i === 0) {
                canvasCtx.moveTo(i, targetY);
            } else {
                canvasCtx.lineTo(i, targetY);
            }
        }
        canvasCtx.strokeStyle = filterColors[filterIndex];
        canvasCtx.lineWidth = 1.5;
        canvasCtx.stroke();
    });

    // 5. Draw UI Control Circles
    if (uiState) {
        const nodeColors = [
            '255, 50, 50',   // Band 1 RGB
            '50, 255, 50',   // Band 2 RGB
            '50, 150, 255'   // Band 3 RGB
        ];

        filters.forEach((filter, index) => {
            const freq = filter.frequency.value;
            const gain = filter.gain.value;

            const x = getLogX(freq, canvas.width);
            const yOffset = gain * pxPerDb;
            const targetY = centerY - yOffset;

            // Default state: semi-transparent, standard size
            let radius = 6;
            let opacity = 0.4; 
            
            // Interaction state: fully opaque and slightly larger when active/hovered
            if (uiState.hoveredBand === index || uiState.activeDragBand === index) {
                opacity = 1.0;
                if (uiState.activeDragBand === index) radius = 8;
            }

            canvasCtx.beginPath();
            canvasCtx.arc(x, targetY, radius, 0, 2 * Math.PI);
            canvasCtx.fillStyle = `rgba(${nodeColors[index]}, ${opacity})`;
            canvasCtx.fill();
            canvasCtx.lineWidth = 2;
            canvasCtx.strokeStyle = `rgba(255, 255, 255, ${opacity + 0.2})`;
            canvasCtx.stroke();
        });
    }
}