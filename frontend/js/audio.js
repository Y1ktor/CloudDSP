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
    
    // Create Filters (Highpass, 3 Peaking, Lowpass)
    const b0Filter = audioContext.createBiquadFilter();
    b0Filter.type = 'highpass';
    b0Filter.frequency.value = sliders.b0.freq.value;
    b0Filter.Q.value = sliders.b0.q.value;
    
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

    const b4Filter = audioContext.createBiquadFilter();
    b4Filter.type = 'peaking';
    b4Filter.frequency.value = sliders.b4.freq.value;
    b4Filter.Q.value = sliders.b4.q.value;
    b4Filter.gain.value = sliders.b4.gain.value;

    const b5Filter = audioContext.createBiquadFilter();
    b5Filter.type = 'lowpass';
    b5Filter.frequency.value = sliders.b5.freq.value;
    b5Filter.Q.value = sliders.b5.q.value;

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
    source.connect(b0Filter);
    
    b0Filter.connect(b1Filter);
    b1Filter.connect(b2Filter);
    b2Filter.connect(b3Filter);
    b3Filter.connect(b4Filter);
    b4Filter.connect(b5Filter);
    
    // The end of the EQ chain goes into the postAnalyser, and then to the speakers
    b5Filter.connect(postAnalyser);
    postAnalyser.connect(audioContext.destination);

    return {
        audioContext,
        source,
        filters: [b0Filter, b1Filter, b2Filter, b3Filter, b4Filter, b5Filter],
        preAnalyser,
        preDataArray,
        postAnalyser,
        postDataArray
    };
}

/**
 * Rebuilds the audio routing graph, allowing specific EQ bands to be bypassed.
 * 
 * @param {Object} audioEngine - The core audio components object.
 * @param {Array<boolean>} bandStates - Array of 5 booleans representing the ON/OFF state of each band.
 */
export function rebuildAudioGraph(audioEngine, bandStates) {
    const { source, filters, preAnalyser, postAnalyser, audioContext } = audioEngine;
    
    // 1. Disconnect everything
    source.disconnect();
    filters.forEach(f => f.disconnect());
    postAnalyser.disconnect();

    // 2. Re-connect preAnalyser (always receives raw source)
    source.connect(preAnalyser);

    // 3. Rebuild the EQ chain
    let currentNode = source;
    for (let i = 0; i < 6; i++) {
        if (bandStates[i]) {
            currentNode.connect(filters[i]);
            currentNode = filters[i];
        }
    }

    // 4. Connect to postAnalyser and destination
    currentNode.connect(postAnalyser);
    postAnalyser.connect(audioContext.destination);
}