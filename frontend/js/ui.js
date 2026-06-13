// ui.js
// Manages HTML DOM interactions, such as standard buttons, sliders, file uploads, and custom dropdowns.
import { formatTime, updateScrollingText } from './utils.js';

export function setupUI(ctx) {
    const { audioElement, audioEngine, uiState, sliders, automationState } = ctx;
    ctx.updateScrollingText = updateScrollingText;
    
    const fileInput = document.getElementById('audio-upload');
    const playPauseBtn = document.getElementById('play-pause-btn');
    const goToBeginningBtn = document.getElementById('go-to-beginning-btn');
    const playIcon = document.getElementById('play-icon');
    const pauseIcon = document.getElementById('pause-icon');
    const seekBar = document.getElementById('seek-bar');
    const timeDisplay = document.getElementById('time-display');
    const volumeSlider = document.getElementById('volume-slider');
    const fileNameDisplay = document.getElementById('file-name-display');

    goToBeginningBtn.addEventListener('click', () => {
        audioElement.currentTime = 0;
        if (!audioElement.paused) audioElement.pause();
    });

    playPauseBtn.addEventListener('click', () => {
        if (audioEngine && audioEngine.audioContext.state === 'suspended') {
            audioEngine.audioContext.resume();
        }
        if (audioElement.paused) audioElement.play();
        else audioElement.pause();
    });

    audioElement.addEventListener('play', () => {
        playIcon.style.display = 'none';
        pauseIcon.style.display = 'block';
        if (audioEngine && audioEngine.audioContext.state === 'suspended') {
            audioEngine.audioContext.resume();
        }
    });

    audioElement.addEventListener('pause', () => {
        playIcon.style.display = 'block';
        pauseIcon.style.display = 'none';
    });

    audioElement.addEventListener('timeupdate', () => {
        if (audioElement.duration && !uiState.isDraggingPlayhead) {
            seekBar.max = audioElement.duration;
            seekBar.value = audioElement.currentTime;
            timeDisplay.innerText = `${formatTime(audioElement.currentTime)} / ${formatTime(audioElement.duration)}`;
        }
    });

    audioElement.addEventListener('loadedmetadata', () => {
        seekBar.max = audioElement.duration;
        timeDisplay.innerText = `0:00 / ${formatTime(audioElement.duration)}`;
    });

    seekBar.addEventListener('input', () => {
        audioElement.currentTime = seekBar.value;
    });

    volumeSlider.addEventListener('input', () => {
        audioElement.volume = volumeSlider.value;
    });

    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            audioElement.src = URL.createObjectURL(file);
            fileNameDisplay.style.color = '#fff';
            updateScrollingText(fileNameDisplay, file.name, 'scrollFileName');
            console.log(`Loaded local file: ${file.name}`);
        }
    });

    const autoDropdown = document.getElementById('automation-dropdown');
    const autoSelected = autoDropdown.querySelector('.select-selected');
    const autoSelectedText = autoDropdown.querySelector('.select-text');
    const autoItems = autoDropdown.querySelector('.select-items');
    const importOption = document.getElementById('import-option');
    const importSubmenu = document.getElementById('import-submenu');

    autoSelected.addEventListener('click', (e) => {
        e.stopPropagation();
        const isShowing = autoItems.style.display === 'block';
        autoItems.style.display = isShowing ? 'none' : 'block';
        importSubmenu.style.display = 'none';
    });

    importOption.addEventListener('click', (e) => {
        e.stopPropagation();
        const isShowing = importSubmenu.style.display === 'block';
        importSubmenu.style.display = isShowing ? 'none' : 'block';
    });

    document.addEventListener('click', () => {
        autoItems.style.display = 'none';
        importSubmenu.style.display = 'none';
    });

    autoDropdown.querySelectorAll('.select-items > li').forEach(item => {
        if (item.id === 'import-option') return;
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const itemName = item.childNodes[0].nodeValue.trim();
            updateScrollingText(autoSelectedText, itemName, 'scrollAutoName');
            autoItems.style.display = 'none';
            importSubmenu.style.display = 'none';
            
            if (item.dataset.value === 'new') {
                automationState.data = [];
                automationState.activeData = null;
                automationState.currentFileKey = null;
                console.log("Started new automation file");
            } else if (item.dataset.value === 'save') {
                console.log("Manual save triggered");
            }
        });
    });

    ctx.populateImportMenu = () => {
        importSubmenu.innerHTML = '';
        let found = false;
        for (let i = 0; i < sessionStorage.length; i++) {
            const key = sessionStorage.key(i);
            if (key && key.startsWith('cloudDspAutomation_')) {
                found = true;
                const li = document.createElement('li');
                const displayName = key.replace('cloudDspAutomation_', '');
                li.innerText = displayName;
                
                li.addEventListener('click', (e) => {
                    e.stopPropagation();
                    updateScrollingText(autoSelectedText, displayName, 'scrollAutoName');
                    autoItems.style.display = 'none';
                    importSubmenu.style.display = 'none';
                    try {
                        const savedData = sessionStorage.getItem(key);
                        let parsed = JSON.parse(savedData);
                        if (Array.isArray(parsed)) {
                            // Upgrade legacy array format
                            automationState.activeData = {
                                regions: [{ start: parsed[0].timestamp, end: parsed[parsed.length - 1].timestamp }],
                                frames: parsed
                            };
                        } else {
                            automationState.activeData = parsed;
                        }
                        automationState.currentFileKey = key;
                        console.log(`Loaded ${key} from session storage`);
                    } catch (err) {}
                });
                importSubmenu.appendChild(li);
            }
        }
        if (!found) {
            const li = document.createElement('li');
            li.innerText = 'None';
            li.classList.add('disabled');
            importSubmenu.appendChild(li);
        }
    };
    ctx.populateImportMenu();

    [sliders.b0, sliders.b1, sliders.b2, sliders.b3, sliders.b4, sliders.b5].forEach((band, index) => {
        const filter = audioEngine.filters[index];
        band.freq.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value);
            filter.frequency.value = val;
            band.labels.freq.innerText = Math.round(val);
        });
        if (band.q) {
            band.q.addEventListener('input', (e) => {
                const val = parseFloat(e.target.value);
                filter.Q.value = val;
                band.labels.q.innerText = val.toFixed(1);
            });
        }
        if (band.gain) {
            band.gain.addEventListener('input', (e) => {
                const val = parseFloat(e.target.value);
                filter.gain.value = val;
                band.labels.gain.innerText = val.toFixed(1);
            });
        }
    });
}