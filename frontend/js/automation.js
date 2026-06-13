// automation.js
// Manages the state machine for EQ recording and the live playback polling engine.
import { rebuildAudioGraph } from './audio.js';

export function setupAutomation(ctx) {
    const { audioElement, audioEngine, uiState, sliders, automationState } = ctx;
    const recordBtn = document.getElementById('record-btn');
    let countdownInterval = null;
    let recordingInterval = null;

    const updateRecordUI = (content) => { recordBtn.innerHTML = content; };
    const circleIcon = `<div style="width: 14px; height: 14px; background-color: #ff3b3b; border-radius: 50%;"></div>`;

    recordBtn.addEventListener('click', () => {
        if (automationState.recordState === 'idle') {
            automationState.recordState = 'preparing';
            let count = 3;
            updateRecordUI(`<span class="countdown-text">${count}</span>`);
            
            countdownInterval = setInterval(() => {
                count--;
                if (count > 0) {
                    updateRecordUI(`<span class="countdown-text">${count}</span>`);
                } else {
                    clearInterval(countdownInterval);
                    automationState.recordState = 'recording';
                    updateRecordUI(circleIcon);
                    recordBtn.classList.add('is-recording');
                    
                    automationState.data = [];
                    recordingInterval = setInterval(() => {
                        if (audioEngine) {
                            automationState.data.push({
                                timestamp: audioElement.currentTime,
                                globalPower: uiState.globalPower,
                                bands: audioEngine.filters.map((filter, idx) => ({
                                    state: uiState.bandStates[idx],
                                    type: filter.type,
                                    freq: filter.frequency.value,
                                    q: filter.Q.value,
                                    gain: filter.gain ? filter.gain.value : 0
                                }))
                            });
                        }
                    }, 50);

                    if (audioEngine && audioEngine.audioContext.state === 'suspended') audioEngine.audioContext.resume();
                    audioElement.play();
                }
            }, 1000);
        } else if (automationState.recordState === 'preparing') {
            clearInterval(countdownInterval);
            automationState.recordState = 'idle';
            updateRecordUI(circleIcon);
        } else if (automationState.recordState === 'recording') {
            automationState.recordState = 'idle';
            recordBtn.classList.remove('is-recording');
            clearInterval(recordingInterval);
            audioElement.pause();
            
            if (automationState.data.length > 0) {
                const dataStr = JSON.stringify(automationState.data);
                const timestamp = new Date().toISOString().replace(/T/, '_').replace(/\..+/, '').replace(/:/g, '-');
                const saveKey = `cloudDspAutomation_${timestamp}`;
                sessionStorage.setItem(saveKey, dataStr);
                
                // If we recorded from a blank slate (New File), auto-apply the new data
                if (!automationState.activeData || automationState.activeData.length === 0) {
                    automationState.activeData = [...automationState.data];
                    const autoSelected = document.querySelector('#automation-dropdown .select-selected');
                    const autoSelectedText = document.querySelector('#automation-dropdown .select-text');
                    if (autoSelected && autoSelectedText && ctx.updateScrollingText) {
                         ctx.updateScrollingText(autoSelectedText, timestamp, 'scrollAutoName');
                    } else if (autoSelected) {
                         autoSelected.innerText = timestamp;
                    }
                    console.log("Automatically applied newly recorded automation data.");
                }

                if (ctx.populateImportMenu) ctx.populateImportMenu();
            }
        }
    });

    const automationPlaybackLoop = () => {
        requestAnimationFrame(automationPlaybackLoop);
        
        if (!audioElement.paused && automationState.recordState !== 'recording' && automationState.activeData && automationState.activeData.length > 0) {
            const currentTime = audioElement.currentTime;
            let closestFrame = automationState.activeData[0];
            let minDiff = Infinity;
            
            for (let i = 0; i < automationState.activeData.length; i++) {
                const frame = automationState.activeData[i];
                const diff = Math.abs(frame.timestamp - currentTime);
                if (diff < minDiff) {
                    minDiff = diff;
                    closestFrame = frame;
                } else if (diff > minDiff) {
                    break; 
                }
            }
            
            let routingChanged = false;
            if (uiState.globalPower !== closestFrame.globalPower) {
                uiState.globalPower = closestFrame.globalPower;
                routingChanged = true;
            }
            
            for (let i = 0; i < 6; i++) {
                if (uiState.bandStates[i] !== closestFrame.bands[i].state) {
                    uiState.bandStates[i] = closestFrame.bands[i].state;
                    routingChanged = true;
                }
            }
            
            if (routingChanged) rebuildAudioGraph(audioEngine, uiState.bandStates);
            
            closestFrame.bands.forEach((bandData, index) => {
                const filter = audioEngine.filters[index];
                const bandUI = sliders[`b${index}`];
                
                filter.frequency.value = bandData.freq;
                filter.Q.value = bandData.q;
                if (filter.gain) filter.gain.value = bandData.gain;
                
                if (bandUI.freq.value != bandData.freq) {
                    bandUI.freq.value = bandData.freq;
                    bandUI.labels.freq.innerText = Math.round(bandData.freq);
                }
                if (bandUI.q && bandUI.q.value != bandData.q) {
                    bandUI.q.value = bandData.q;
                    bandUI.labels.q.innerText = bandData.q.toFixed(1);
                }
                if (bandUI.gain && bandUI.gain.value != bandData.gain) {
                    bandUI.gain.value = bandData.gain;
                    bandUI.labels.gain.innerText = bandData.gain.toFixed(1);
                }
            });
        }
    };
    automationPlaybackLoop();
}