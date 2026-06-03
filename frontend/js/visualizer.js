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

// Helper function to map a frequency to an X coordinate using a logarithmic scale
function getLogX(frequency, width) {
    if (frequency < MIN_FREQ_HZ) frequency = MIN_FREQ_HZ;
    if (frequency > MAX_FREQ_HZ) frequency = MAX_FREQ_HZ;
    
    const minLog = Math.log10(MIN_FREQ_HZ);
    const maxLog = Math.log10(MAX_FREQ_HZ);
    const percent = (Math.log10(frequency) - minLog) / (maxLog - minLog);
    return percent * width;
}

export function drawVisualizer(preAnalyser, preDataArray, postAnalyser, postDataArray, audioContext, sliders) {
    requestAnimationFrame(() => drawVisualizer(preAnalyser, preDataArray, postAnalyser, postDataArray, audioContext, sliders));

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
        
        // Ensure it doesn't drop below 0 if we applied an extreme negative EQ
        if (finalNormalizedVolume < 0) finalNormalizedVolume = 0;
        
        // Apply the exponential visual curve to the final exaggerated volume
        let barHeight = Math.pow(finalNormalizedVolume, 2) * maxBarHeight;
        
        // Base visual multiplier
        barHeight *= BASE_VISUAL_MULTIPLIER;
        if (barHeight > usableHeight) barHeight = usableHeight;

        const percentX = x / canvas.width;
        const r = 255 * (1 - percentX);
        const g = 255 * (percentX < 0.5 ? percentX * 2 : (1 - percentX) * 2);
        const b = 255 * percentX;

        canvasCtx.fillStyle = `rgb(${r},${g},${b})`;
        canvasCtx.fillRect(x, usableHeight - barHeight, barWidth, barHeight);
    }

    // 4. Draw EQ Setting Overlays
    const lowGain = parseFloat(sliders.low.value);
    const midGain = parseFloat(sliders.mid.value);
    const highGain = parseFloat(sliders.high.value);

    const maxDeltaY = centerY - 2; 
    const pxPerDb = maxDeltaY / 15;

    const lowStartX = getLogX(20, canvas.width);
    const lowEndX = getLogX(200, canvas.width);
    const midStartX = getLogX(200, canvas.width);
    const midEndX = getLogX(3000, canvas.width);
    const highStartX = getLogX(3000, canvas.width);
    const highEndX = getLogX(20000, canvas.width);

    const drawEqLine = (startX, endX, gainValue, color) => {
        const yOffset = gainValue * pxPerDb;
        const targetY = centerY - yOffset;
        const width = endX - startX;

        canvasCtx.beginPath();
        canvasCtx.moveTo(startX, targetY);
        canvasCtx.lineTo(endX, targetY);
        canvasCtx.strokeStyle = color;
        canvasCtx.lineWidth = 3;
        canvasCtx.stroke();
        
        canvasCtx.beginPath();
        canvasCtx.moveTo(startX + (width/2), centerY);
        canvasCtx.lineTo(startX + (width/2), targetY);
        canvasCtx.strokeStyle = color.replace('1)', '0.2)');
        canvasCtx.lineWidth = 1;
        canvasCtx.stroke();
    };

    drawEqLine(lowStartX, lowEndX, lowGain, 'rgba(255, 50, 50, 1)');
    drawEqLine(midStartX, midEndX, midGain, 'rgba(50, 255, 50, 1)');
    drawEqLine(highStartX, highEndX, highGain, 'rgba(50, 150, 255, 1)');
}