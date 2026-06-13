// interactions.js
// Handles all canvas hit detection, complex mouse events, and 2D/horizontal dragging math.
import { getFreqFromX, getLogX } from './visualizer.js';
import { formatTime } from './utils.js';
import { rebuildAudioGraph } from './audio.js';

export function setupInteractions(ctx) {
    const { canvas, audioEngine, uiState, sliders, filterModes, audioElement } = ctx;
    const seekBar = document.getElementById('seek-bar');
    const timeDisplay = document.getElementById('time-display');

    canvas.addEventListener('mousemove', (e) => {
        if (!audioEngine) return;
        
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const mouseX = (e.clientX - rect.left) * scaleX;
        const mouseY = (e.clientY - rect.top) * scaleY;

        const PADDING_BOTTOM_PX = 20;
        const PADDING_TOP_PX = 40;
        const usableHeight = canvas.height - PADDING_BOTTOM_PX - PADDING_TOP_PX;
        const centerY = usableHeight / 2;
        const maxDeltaY = centerY - 2; 
        const pxPerDb = maxDeltaY / 15;

        if (uiState.isDraggingPlayhead) {
            let percent = mouseX / canvas.width;
            if (percent < 0) percent = 0;
            if (percent > 1) percent = 1;
            if (audioElement.duration) {
                audioElement.currentTime = percent * audioElement.duration;
                seekBar.value = audioElement.currentTime;
                timeDisplay.innerText = `${formatTime(audioElement.currentTime)} / ${formatTime(audioElement.duration)}`;
            }
            return;
        }

        if (uiState.activeDragBand !== -1) {
            let newY = mouseY - PADDING_TOP_PX;
            let newX = mouseX;
            
            const filter = audioEngine.filters[uiState.activeDragBand];
            const isPassFilter = (filter.type === 'highpass' || filter.type === 'lowpass');
            const yLimit = isPassFilter ? 20 : 15;
            
            const minY = centerY - (yLimit * pxPerDb);
            const maxY = centerY - (-yLimit * pxPerDb);
            if (newY < minY) newY = minY;
            if (newY > maxY) newY = maxY;

            const newYValue = (centerY - newY) / pxPerDb;
            const newFreq = getFreqFromX(newX, canvas.width);
            
            filter.frequency.value = newFreq;
            const bandKey = `b${uiState.activeDragBand}`;
            
            if (filter.type === 'highpass' || filter.type === 'lowpass') {
                filter.Q.value = newYValue;
                if (sliders[bandKey].q) {
                    sliders[bandKey].q.value = newYValue;
                    sliders[bandKey].labels.q.innerText = newYValue.toFixed(1);
                }
            } else {
                filter.gain.value = newYValue;
                if (sliders[bandKey].gain) {
                    sliders[bandKey].gain.value = newYValue;
                    sliders[bandKey].labels.gain.innerText = newYValue.toFixed(1);
                }
            }
            
            sliders[bandKey].freq.value = newFreq;
            sliders[bandKey].labels.freq.innerText = Math.round(newFreq);
            
        } else if (uiState.activeZoneDragBand !== -1) {
            const deltaX = mouseX - uiState.dragStartX;
            let newQ = uiState.startQ - (deltaX * 0.02);
            
            if (audioEngine.filters[uiState.activeZoneDragBand].type === 'highpass' || audioEngine.filters[uiState.activeZoneDragBand].type === 'lowpass') {
                 if (newQ < -20.0) newQ = -20.0;
                 if (newQ > 20.0) newQ = 20.0;
            } else {
                 if (newQ < 0.1) newQ = 0.1;
                 if (newQ > 10.0) newQ = 10.0;
            }
            
            const filter = audioEngine.filters[uiState.activeZoneDragBand];
            filter.Q.value = newQ;
            const bandKey = `b${uiState.activeZoneDragBand}`;
            
            if (sliders[bandKey].q) {
                sliders[bandKey].q.value = newQ;
                sliders[bandKey].labels.q.innerText = newQ.toFixed(1);
            }
            canvas.style.cursor = 'ew-resize';

        } else {
            let hoveredNode = -1;
            let hoveredZone = -1;
            
            // Prevent hovering EQ nodes/zones if automation is active and we are not recording
            const { automationState } = ctx;
            const isAutomationActiveAndLocked = automationState && 
                                                automationState.activeData && 
                                                (automationState.activeData.frames && automationState.activeData.frames.length > 0) &&
                                                automationState.recordState !== 'recording';

            if (!isAutomationActiveAndLocked) {
                audioEngine.filters.forEach((filter, index) => {
                    const nodeX = getLogX(filter.frequency.value, canvas.width);
                    let yValue = (filter.type === 'highpass' || filter.type === 'lowpass') ? filter.Q.value : filter.gain.value;
                    const nodeY = PADDING_TOP_PX + centerY - (yValue * pxPerDb);
                    
                    const dx = mouseX - nodeX;
                    const dy = mouseY - nodeY;
                    if (Math.sqrt(dx * dx + dy * dy) <= 12) {
                        hoveredNode = index;
                    }
                });
                
                if (hoveredNode === -1) {
                    const ZONE_WIDTH = 40;
                    audioEngine.filters.forEach((filter, index) => {
                        if (index === 0 || index === 5) return;
                        
                        const nodeX = getLogX(filter.frequency.value, canvas.width);
                        const nodeY = PADDING_TOP_PX + centerY - (filter.gain.value * pxPerDb);
                        const trueCenterY = PADDING_TOP_PX + centerY;
                        
                        const leftBound = nodeX - (ZONE_WIDTH / 2);
                        const rightBound = nodeX + (ZONE_WIDTH / 2);
                        const topBound = Math.min(trueCenterY, nodeY);
                        const bottomBound = Math.max(trueCenterY, nodeY);
                        
                        if (mouseX >= leftBound && mouseX <= rightBound && mouseY >= topBound && mouseY <= bottomBound) {
                            hoveredZone = index;
                        }
                    });
                }
            }
            
            uiState.hoveredNode = hoveredNode;
            uiState.hoveredZone = hoveredZone;
            
            const blockWidth = (canvas.width / 8) - 10;
            const gap = 8;
            const totalBlocksWidth = (6 * blockWidth) + (5 * gap);
            const startX = canvas.width - 20 - totalBlocksWidth;
            
            let hoveredTopBand = -1;
            let hoveredBadge = -1;
            for (let i = 0; i < 6; i++) {
                const blockX = startX + (i * (blockWidth + gap));
                if (mouseX >= blockX && mouseX <= blockX + blockWidth && mouseY >= 10 && mouseY <= PADDING_TOP_PX - 10) {
                    let hitBadge = false;
                    if (i === 0 || i === 5) {
                        const badgeW = 24;
                        const badgeH = 16;
                        const badgeX = blockX + (blockWidth / 2) + 10;
                        const badgeY = (PADDING_TOP_PX / 2) - (badgeH / 2);
                        if (mouseX >= badgeX && mouseX <= badgeX + badgeW && mouseY >= badgeY && mouseY <= badgeY + badgeH) {
                            hoveredBadge = i;
                            hitBadge = true;
                        }
                    }
                    if (!hitBadge) hoveredTopBand = i;
                }
            }
            uiState.hoveredTopBand = hoveredTopBand;
            uiState.hoveredBadge = hoveredBadge;
            
            const powerX = 40;
            const powerY = PADDING_TOP_PX / 2;
            const distToPower = Math.sqrt(Math.pow(mouseX - powerX, 2) + Math.pow(mouseY - powerY, 2));
            uiState.hoveredPowerBtn = distToPower <= 16;

            let hoveredPlayhead = false;
            if (audioElement.duration) {
                const playheadX = (audioElement.currentTime / audioElement.duration) * canvas.width;
                const playheadY = PADDING_TOP_PX + usableHeight;
                if (Math.sqrt(Math.pow(mouseX - playheadX, 2) + Math.pow(mouseY - playheadY, 2)) <= 12) {
                    hoveredPlayhead = true;
                }
            }
            uiState.hoveredPlayhead = hoveredPlayhead;

            if (uiState.hoveredPowerBtn || uiState.hoveredBadge !== -1 || hoveredTopBand !== -1) {
                canvas.style.cursor = 'pointer';
            } else if ((hoveredZone !== -1 && hoveredNode === -1) || uiState.hoveredPlayhead) {
                canvas.style.cursor = 'ew-resize';
            } else {
                canvas.style.cursor = 'default';
            }
        }
    });

    canvas.addEventListener('mousedown', (e) => {
        if (!audioEngine) return;
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const mouseX = (e.clientX - rect.left) * scaleX;
        const mouseY = (e.clientY - rect.top) * scaleY;

        if (uiState.hoveredPlayhead) {
            uiState.isDraggingPlayhead = true;
            return; 
        }

        const PADDING_TOP_PX = 40;
        const blockWidth = (canvas.width / 8) - 10;
        const gap = 8;
        const totalBlocksWidth = (6 * blockWidth) + (5 * gap);
        const startX = canvas.width - 20 - totalBlocksWidth;
        
        for (let i of [0, 5]) {
            const blockX = startX + (i * (blockWidth + gap));
            const badgeW = 24;
            const badgeH = 16;
            const badgeX = blockX + (blockWidth / 2) + 10;
            const badgeY = (PADDING_TOP_PX / 2) - (badgeH / 2);
            
            if (mouseX >= badgeX && mouseX <= badgeX + badgeW && mouseY >= badgeY && mouseY <= badgeY + badgeH) {
                const filter = audioEngine.filters[i];
                const band = sliders[`b${i}`];
                if (i === 0) {
                    filterModes.b0 = filterModes.b0 === 'highpass' ? 'lowshelf' : 'highpass';
                    filter.type = filterModes.b0;
                    band.gainContainer.style.display = filter.type === 'lowshelf' ? 'block' : 'none';
                    band.qContainer.style.display = filter.type === 'lowshelf' ? 'none' : 'block';
                    if(filter.type === 'lowshelf') filter.gain.value = parseFloat(band.gain.value);
                } else if (i === 5) {
                    filterModes.b5 = filterModes.b5 === 'lowpass' ? 'highshelf' : 'lowpass';
                    filter.type = filterModes.b5;
                    band.gainContainer.style.display = filter.type === 'highshelf' ? 'block' : 'none';
                    band.qContainer.style.display = filter.type === 'highshelf' ? 'none' : 'block';
                    if(filter.type === 'highshelf') filter.gain.value = parseFloat(band.gain.value);
                }
                return; 
            }
        }

        const powerX = 40;
        const powerY = PADDING_TOP_PX / 2;
        if (Math.sqrt(Math.pow(mouseX - powerX, 2) + Math.pow(mouseY - powerY, 2)) <= 16) {
            uiState.globalPower = !uiState.globalPower;
            if (!uiState.globalPower) {
                uiState.savedBandStates = [...uiState.bandStates];
                uiState.bandStates.fill(false);
            } else {
                if (uiState.savedBandStates) {
                    uiState.bandStates = [...uiState.savedBandStates];
                    uiState.savedBandStates = null;
                } else {
                    uiState.bandStates.fill(true);
                }
            }
            rebuildAudioGraph(audioEngine, uiState.bandStates);
            return;
        }

        for (let i = 0; i < 6; i++) {
            const blockX = startX + (i * (blockWidth + gap));
            if (mouseX >= blockX && mouseX <= blockX + blockWidth && mouseY >= 10 && mouseY <= PADDING_TOP_PX - 10) {
                uiState.bandStates[i] = !uiState.bandStates[i];
                if (uiState.bandStates[i]) {
                    uiState.globalPower = true;
                    uiState.savedBandStates = null;
                } else if (uiState.bandStates.every(state => !state)) {
                    uiState.globalPower = false;
                    uiState.savedBandStates = null;
                }
                rebuildAudioGraph(audioEngine, uiState.bandStates);
                return;
            }
        }

        // Prevent dragging EQ nodes/zones if automation is active and we are not recording
        const { automationState } = ctx;
        const isAutomationActiveAndLocked = automationState && 
                                            automationState.activeData && 
                                            (automationState.activeData.frames && automationState.activeData.frames.length > 0) &&
                                            automationState.recordState !== 'recording';
                                            
        if (isAutomationActiveAndLocked) {
            return; // Exit mousedown early, blocking the drag initiation
        }

        if (uiState.hoveredNode !== -1) {
            uiState.activeDragBand = uiState.hoveredNode;
        } else if (uiState.hoveredZone !== -1) {
            uiState.activeZoneDragBand = uiState.hoveredZone;
            uiState.dragStartX = mouseX;
            uiState.startQ = audioEngine.filters[uiState.hoveredZone].Q.value;
        }
    });

    canvas.addEventListener('mouseup', () => {
        uiState.activeDragBand = -1;
        uiState.activeZoneDragBand = -1;
        uiState.isDraggingPlayhead = false;
    });

    canvas.addEventListener('mouseleave', () => {
        uiState.activeDragBand = -1;
        uiState.activeZoneDragBand = -1;
        uiState.isDraggingPlayhead = false;
        uiState.hoveredNode = -1;
        uiState.hoveredZone = -1;
        uiState.hoveredTopBand = -1;
        uiState.hoveredBadge = -1;
        uiState.hoveredPowerBtn = false;
        uiState.hoveredPlayhead = false;
        canvas.style.cursor = 'default';
    });
}