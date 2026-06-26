// automation.js
// Manages the state machine for EQ recording and the live playback polling engine.
import { rebuildAudioGraph } from './audio.js';

export function setupAutomation(ctx) {
    const { audioElement, audioEngine, uiState, sliders, automationState } = ctx;
    let countdownInterval = null;
    let recordingInterval = null;

    // Expose the record button logic for React to call
    ctx.recordBtnHandler = () => {
        if (automationState.recordState === 'idle') {
            automationState.recordState = 'preparing';
            let count = 3;
            // React can listen to this if needed, but for now we just wait 3s
            if (ctx.onRecordCountdown) ctx.onRecordCountdown(count);
            
            countdownInterval = setInterval(() => {
                count--;
                if (count > 0) {
                    if (ctx.onRecordCountdown) ctx.onRecordCountdown(count);
                } else {
                    clearInterval(countdownInterval);
                    automationState.recordState = 'recording';
                    if (ctx.onRecordStart) ctx.onRecordStart();
                    
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
            if (ctx.onRecordStop) ctx.onRecordStop();
        } else if (automationState.recordState === 'recording') {
            automationState.recordState = 'idle';
            if (ctx.onRecordStop) ctx.onRecordStop();
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
                    
                    if (ctx.onNewAutomationFile) ctx.onNewAutomationFile(timestamp);
                    console.log("Automatically applied newly recorded automation data.");
                } else {
                    console.log(`Overwrote existing automation data in ${saveKey}.`);
                }

                sessionStorage.setItem(saveKey, dataStr);
                // Trigger React to re-fetch import options
                if (ctx.onAutomationSaved) ctx.onAutomationSaved();
            }
        }
    };

    const syncAutomationToTime = () => {
        if (!automationState.activeData || !automationState.activeData.frames || automationState.activeData.frames.length === 0) return;
        
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
            
            filter.frequency.value = bandData.freq;
            filter.Q.value = bandData.q;
            if (filter.gain) filter.gain.value = bandData.gain;
        });
    };

    // Expose sync function to context for scrubbing
    ctx.forceAutomationSync = syncAutomationToTime;

    const automationPlaybackLoop = () => {
        requestAnimationFrame(automationPlaybackLoop);
        
        if (!audioElement.paused && automationState.recordState !== 'recording' && automationState.activeData && automationState.activeData.frames && automationState.activeData.frames.length > 0) {
            syncAutomationToTime();
        }
    };
    automationPlaybackLoop();
}