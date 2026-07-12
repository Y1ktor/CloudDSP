import React from 'react';
import { SplendidGrandPiano, Soundfont } from 'smplr';
import { parseMidiFile, determineMasterBpm } from '../utils/MidiParser';

/**
 * useMidiManager Hook
 * 
 * Orchestrates the fetching, parsing, and caching of MIDI data. 
 * It manages the mock/production pipeline for converting binary `.mid` files into 
 * `@tonejs/midi` Javascript class instances. It is also responsible for executing the 
 * "smart voting" algorithm to determine the master BPM across all AI-generated stems.
 * 
 * @param {Object} stemUrls - Dictionary of track names to their audio URLs
 * @param {string} timeSignature - Global time signature string (e.g., '4/4')
 * @param {Function} setBpm - State setter for the active BPM
 * @param {Function} setOriginalBpm - State setter for the detected original BPM
 * @param {React.MutableRefObject} audioCtxRef - Reference to the global Web Audio context
 * @param {React.MutableRefObject} globalSynthRef - Reference to the piano synthesizer
 * @param {React.MutableRefObject} guitarSynthRef - Reference to the guitar synthesizer
 * @param {React.MutableRefObject} bassSynthRef - Reference to the bass synthesizer
 * @returns {Object} { parsedMidiStems, setParsedMidiStems, originalMidiStems, isMidiLoading }
 */
export function useMidiManager(stemUrls, timeSignature, setBpm, setOriginalBpm, audioCtxRef, globalSynthRef, guitarSynthRef, bassSynthRef) {
    const [parsedMidiStems, setParsedMidiStems] = React.useState({});
    const [originalMidiStems, setOriginalMidiStems] = React.useState({});
    const [isMidiLoading, setIsMidiLoading] = React.useState(true);
    const hasFetchedMidi = React.useRef(false);

    // MOCK: Fetch local MIDI files to test MIDI processing and smart BPM voting
    React.useEffect(() => {
        if (!stemUrls || hasFetchedMidi.current) return;
        
        const fetchAndParse = async () => {
            setIsMidiLoading(true);
            
            // Initialize AudioContext early so we can start downloading samples in the background!
            if (!audioCtxRef.current) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                audioCtxRef.current = new AudioContext();
            }
            if (!globalSynthRef.current) {
                globalSynthRef.current = new SplendidGrandPiano(audioCtxRef.current);
            }
            if (!guitarSynthRef.current) {
                guitarSynthRef.current = new Soundfont(audioCtxRef.current, { instrument: 'acoustic_guitar_nylon' });
            }
            if (!bassSynthRef.current) {
                bassSynthRef.current = new Soundfont(audioCtxRef.current, { instrument: 'acoustic_bass' });
            }

            // MOCK DELAY: wait 3 seconds to simulate AWS Basic Pitch cold start/processing
            await new Promise(resolve => setTimeout(resolve, 3000));
            
            const mockMidiFiles = {
                'bass': '/mock-midi/yosemite-bass-midi.mid',
                'drums': '/mock-midi/yosemite-drums-midi.mid',
                'guitar': '/mock-midi/yosemite-guitar-midi.mid',
                'other': '/mock-midi/yosemite-other-midi.mid',
                'piano': '/mock-midi/yosemite-piano-midi.mid',
                'vocals': '/mock-midi/yosemite-vocals-midi.mid'
            };
            
            try {
                const parsed = {};
                const parsedBackup = {};
                for (const [track, url] of Object.entries(mockMidiFiles)) {
                    const response = await fetch(url);
                    const arrayBuffer = await response.arrayBuffer();
                    
                    const data = await parseMidiFile(arrayBuffer.slice(0), timeSignature);
                    const backupData = await parseMidiFile(arrayBuffer.slice(0), timeSignature);
                    
                    parsed[track] = data;
                    parsedBackup[track] = backupData;
                }
                
                setParsedMidiStems(parsed);
                setOriginalMidiStems(parsedBackup);
                
                // Invoke our new hierarchy logic to find the best master BPM!
                const bestBpm = determineMasterBpm(parsed);
                
                // Update the hook state, causing the canvas to instantly recalculate!
                setOriginalBpm(bestBpm);
                setBpm(bestBpm);
                setIsMidiLoading(false);
                hasFetchedMidi.current = true;
                
                console.log("Mock MIDI loaded. Smart BPM chosen:", bestBpm);
            } catch (err) {
                console.error("Mock MIDI fetch failed:", err);
                setIsMidiLoading(false);
            }
        };

        fetchAndParse();
    }, [stemUrls, timeSignature, setBpm, setOriginalBpm, audioCtxRef, globalSynthRef, guitarSynthRef, bassSynthRef]);

    return { parsedMidiStems, setParsedMidiStems, originalMidiStems, isMidiLoading };
}
