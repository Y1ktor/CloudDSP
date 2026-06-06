// audio.js
// Handles Web Audio API setup, filters, and routing

/**
 * Initializes the Web Audio API context, creates the EQ filter chain, and sets up the audio analysers.
 * 
 * @param {HTMLAudioElement} audioElement - The HTML <audio> element that serves as the raw audio source.
 * @param {Object} sliders - The grouped UI elements containing the user's EQ settings (frequency, Q, gain) for bands 1, 2, and 3.
 * @returns {Object} An object containing the core audio components needed for playback and visualization:
 *   - {AudioContext} audioContext - The primary Web Audio API context.
 *   - {Array<BiquadFilterNode>} filters - An array containing the 3 Peaking filter nodes.
 *   - {AnalyserNode} preAnalyser - The analyser connected before the EQ chain (measures raw audio).
 *   - {Uint8Array} preDataArray - The array buffer that will hold the raw audio FFT data.
 *   - {AnalyserNode} postAnalyser - The analyser connected after the EQ chain (measures EQ'd audio).
 *   - {Uint8Array} postDataArray - The array buffer that will hold the EQ'd audio FFT data.
 */
export function initializeAudioEngine(audioElement, sliders) {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaElementSource(audioElement);
    
    // Create Filters (All Peaking/Bell curves now)
    const b1Filter = audioContext.createBiquadFilter();
    b1Filter.type = 'peaking';
    b1Filter.frequency.value = sliders.b1.freq.value;
    b1Filter.Q.value = sliders.b1.q.value;
    b1Filter.gain.value = sliders.b1.gain.value;

    const b2Filter = audioContext.createBiquadFilter();
    b2Filter.type = 'peaking';
    b2Filter.frequency.value = sliders.b2.freq.value;
    b2Filter.Q.value = sliders.b2.q.value;
    b2Filter.gain.value = sliders.b2.gain.value;

    const b3Filter = audioContext.createBiquadFilter();
    b3Filter.type = 'peaking';
    b3Filter.frequency.value = sliders.b3.freq.value;
    b3Filter.Q.value = sliders.b3.q.value;
    b3Filter.gain.value = sliders.b3.gain.value;

    // Create Pre-EQ Analyser
    const preAnalyser = audioContext.createAnalyser();
    preAnalyser.fftSize = 4096; 
    const bufferLength = preAnalyser.frequencyBinCount;
    const preDataArray = new Uint8Array(bufferLength);

    // Create Post-EQ Analyser
    const postAnalyser = audioContext.createAnalyser();
    postAnalyser.fftSize = 4096; 
    const postDataArray = new Uint8Array(bufferLength);

    // Connect Audio Chain
    // Split the raw source: one copy goes to the preAnalyser, the other goes into the EQ chain
    source.connect(preAnalyser);
    source.connect(b1Filter);
    
    b1Filter.connect(b2Filter);
    b2Filter.connect(b3Filter);
    
    // The end of the EQ chain goes into the postAnalyser, and then to the speakers
    b3Filter.connect(postAnalyser);
    postAnalyser.connect(audioContext.destination);

    return {
        audioContext,
        filters: [b1Filter, b2Filter, b3Filter],
        preAnalyser,
        preDataArray,
        postAnalyser,
        postDataArray
    };
}