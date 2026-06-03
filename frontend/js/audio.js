// audio.js
// Handles Web Audio API setup, filters, and routing

export function initializeAudioEngine(audioElement, sliders) {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaElementSource(audioElement);
    
    // Create Filters
    const lowFilter = audioContext.createBiquadFilter();
    lowFilter.type = 'lowshelf';
    lowFilter.frequency.value = 100;
    lowFilter.gain.value = sliders.low.value;

    const midFilter = audioContext.createBiquadFilter();
    midFilter.type = 'peaking';
    midFilter.frequency.value = 1000;
    midFilter.Q.value = 1.0;
    midFilter.gain.value = sliders.mid.value;

    const highFilter = audioContext.createBiquadFilter();
    highFilter.type = 'highshelf';
    highFilter.frequency.value = 5000;
    highFilter.gain.value = sliders.high.value;

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
    source.connect(lowFilter);
    
    lowFilter.connect(midFilter);
    midFilter.connect(highFilter);
    
    // The end of the EQ chain goes into the postAnalyser, and then to the speakers
    highFilter.connect(postAnalyser);
    postAnalyser.connect(audioContext.destination);

    return {
        audioContext,
        filters: { low: lowFilter, mid: midFilter, high: highFilter },
        preAnalyser,
        preDataArray,
        postAnalyser,
        postDataArray
    };
}