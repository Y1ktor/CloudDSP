// main.js
// Controller file connecting UI, Audio, and Visualizer modules
import { initializeAudioEngine } from './audio.js';
import { drawVisualizer } from './visualizer.js';

const audioElement = document.getElementById('audio-source');

// Grouped UI Elements
const sliders = {
    low: document.getElementById('low-eq'),
    mid: document.getElementById('mid-eq'),
    high: document.getElementById('high-eq')
};

const labels = {
    low: document.getElementById('low-val'),
    mid: document.getElementById('mid-val'),
    high: document.getElementById('high-val')
};

let audioEngine = null;

// Initialize on play to satisfy browser security requirements
audioElement.addEventListener('play', () => {
    if (!audioEngine) {
        // Boot up the audio context and routing
        audioEngine = initializeAudioEngine(audioElement, sliders);

        // Bind UI Sliders to the Web Audio Filters
        sliders.low.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            audioEngine.filters.low.gain.value = value;
            labels.low.innerText = value.toFixed(1);
        });

        sliders.mid.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            audioEngine.filters.mid.gain.value = value;
            labels.mid.innerText = value.toFixed(1);
        });

        sliders.high.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            audioEngine.filters.high.gain.value = value;
            labels.high.innerText = value.toFixed(1);
        });

        // Start the visualization loop
        drawVisualizer(
            audioEngine.preAnalyser, 
            audioEngine.preDataArray, 
            audioEngine.postAnalyser, 
            audioEngine.postDataArray, 
            audioEngine.audioContext, 
            sliders
        );
    }
    
    // Ensure context resumes if the browser suspends it
    if (audioEngine.audioContext.state === 'suspended') {
        audioEngine.audioContext.resume();
    }
});