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
                let newStart = automationState.data[0].timestamp;
                let newEnd = automationState.data[automationState.data.length - 1].timestamp;
                
                if (!automationState.activeData || !automationState.activeData.frames) {
                    automationState.activeData = { regions: [], frames: [] };
                }
                
                let updatedRegions = [];
                let updatedFrames = [...automationState.activeData.frames];
                
                for (let region of automationState.activeData.regions) {
                    if (newStart >= region.start && newStart <= region.end) {
                        // Old region is cut off by the new recording start point
                        updatedRegions.push({ start: region.start, end: newStart });
                        updatedFrames = updatedFrames.filter(f => !(f.timestamp >= newStart && f.timestamp <= region.end));
                    } else if (newEnd >= region.start && newStart < region.start) {
                        // The new recording completely overran this later region, erase it
                        updatedFrames = updatedFrames.filter(f => !(f.timestamp >= region.start && f.timestamp <= region.end));
                    } else {
                        // Untouched region
                        updatedRegions.push(region);
                    }
                }
                
                // Add the newly recorded region and frames
                updatedRegions.push({ start: newStart, end: newEnd });
                updatedFrames.push(...automationState.data);
                updatedFrames.sort((a, b) => a.timestamp - b.timestamp);
                
                // Clean and merge any touching regions just in case
                updatedRegions.sort((a, b) => a.start - b.start);
                let mergedRegions = [];
                for (let r of updatedRegions) {
                    if (mergedRegions.length === 0) {
                        mergedRegions.push({...r});
                    } else {
                        let last = mergedRegions[mergedRegions.length - 1];
                        if (r.start <= last.end) {
                            last.end = Math.max(last.end, r.end);
                        } else {
                            mergedRegions.push({...r});
                        }
                    }
                }
                
                automationState.activeData.regions = mergedRegions;
                automationState.activeData.frames = updatedFrames;

                const dataStr = JSON.stringify(automationState.activeData);
                
                let saveKey = automationState.currentFileKey;
                if (!saveKey) {
                    // New file, generate timestamp key
                    const timestamp = new Date().toISOString().replace(/T/, '_').replace(/\..+/, '').replace(/:/g, '-');
                    saveKey = `cloudDspAutomation_${timestamp}`;
                    automationState.currentFileKey = saveKey;
                    
                    const autoSelected = document.querySelector('#automation-dropdown .select-selected');
                    const autoSelectedText = document.querySelector('#automation-dropdown .select-text');
                    if (autoSelected && autoSelectedText && ctx.updateScrollingText) {
                         ctx.updateScrollingText(autoSelectedText, timestamp, 'scrollAutoName');
                    } else if (autoSelected) {
                         autoSelected.innerText = timestamp;
                    }
                    console.log("Automatically applied newly recorded automation data.");
                } else {
                    console.log(`Overwrote existing automation data in ${saveKey}.`);
                }

                sessionStorage.setItem(saveKey, dataStr);
                if (ctx.populateImportMenu) ctx.populateImportMenu();
            }
        }
    });

    const automationPlaybackLoop = () => {
        requestAnimationFrame(automationPlaybackLoop);
        
        if (!audioElement.paused && automationState.recordState !== 'recording' && automationState.activeData && automationState.activeData.frames && automationState.activeData.frames.length > 0) {
            const currentTime = audioElement.currentTime;
            let closestFrame = automationState.activeData.frames[0];
            let minDiff = Infinity;
            
            for (let i = 0; i < automationState.activeData.frames.length; i++) {
                const frame = automationState.activeData.frames[i];
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