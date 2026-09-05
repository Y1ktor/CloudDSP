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

    // Transport Control: Instantly seek playback to the beginning (0:00) and pause
    goToBeginningBtn.addEventListener('click', () => {
        audioElement.currentTime = 0;
        if (!audioElement.paused) audioElement.pause();
    });

    // Transport Control: Toggle Play/Pause state and conditionally stop active recordings
    playPauseBtn.addEventListener('click', () => {
        if (audioEngine && audioEngine.audioContext.state === 'suspended') {
            audioEngine.audioContext.resume();
        }
        if (audioElement.paused) {
            audioElement.play();
        } else {
            audioElement.pause();
            // Automatically stop recording if paused
            if (automationState.recordState === 'recording') {
                document.getElementById('record-btn').click();
            }
        }
    });

    // Audio Event: Automatically sync play icon to pause icon when audio begins playing
    audioElement.addEventListener('play', () => {
        playIcon.style.display = 'none';
        pauseIcon.style.display = 'block';
        if (audioEngine && audioEngine.audioContext.state === 'suspended') {
            audioEngine.audioContext.resume();
        }
    });

    // Audio Event: Automatically sync pause icon to play icon when audio halts
    audioElement.addEventListener('pause', () => {
        playIcon.style.display = 'block';
        pauseIcon.style.display = 'none';
    });

    // Audio Event: Fires continuously during playback to sync the HTML seek bar and time text
    audioElement.addEventListener('timeupdate', () => {
        if (audioElement.duration && !uiState.isDraggingPlayhead) {
            seekBar.max = audioElement.duration;
            seekBar.value = audioElement.currentTime;
            timeDisplay.innerText = `${formatTime(audioElement.currentTime)} / ${formatTime(audioElement.duration)}`;
        }
    });

    // Audio Event: Fires once when a new file loads to set up maximum duration limits
    audioElement.addEventListener('loadedmetadata', () => {
        seekBar.max = audioElement.duration;
        timeDisplay.innerText = `0:00 / ${formatTime(audioElement.duration)}`;
    });

    // Slider Event: Allows user to scrub through track visually via the HTML range input
    seekBar.addEventListener('input', () => {
        audioElement.currentTime = seekBar.value;
        
        // Force an immediate automation sync pass while dragging if paused
        if (audioElement.paused && automationState && automationState.activeData && automationState.activeData.frames && automationState.activeData.frames.length > 0) {
            if (ctx.forceAutomationSync) ctx.forceAutomationSync();
        }
    });

    // Slider Event: Maps HTML volume slider linearly to native audio output gain
    volumeSlider.addEventListener('input', () => {
        audioElement.volume = volumeSlider.value;
    });

    // Input Event: Handles user selecting a local audio file and mounting it for playback
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

    // Dropdown Event: Toggles the main automation dropdown menu visibility
    autoSelected.addEventListener('click', (e) => {
        e.stopPropagation();
        const isShowing = autoItems.style.display === 'block';
        autoItems.style.display = isShowing ? 'none' : 'block';
        importSubmenu.style.display = 'none';
    });

    // Dropdown Event: Toggles the secondary "Import" submenu visibility
    importOption.addEventListener('click', (e) => {
        e.stopPropagation();
        const isShowing = importSubmenu.style.display === 'block';
        importSubmenu.style.display = isShowing ? 'none' : 'block';
    });

    // Global Event: Closes all dropdown menus if the user clicks anywhere outside of them
    document.addEventListener('click', () => {
        autoItems.style.display = 'none';
        importSubmenu.style.display = 'none';
    });

    // Dropdown Event: Handles clicking standard options (New File, Save) inside the main menu
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
                
                // Dropdown Event: Handles loading a specific automation file from Session Storage
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
}