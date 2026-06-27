import React, { useEffect, useRef } from 'react';
import { drawVisualizer, initVisualizer } from '../vanilla/visualizer.js';
import { setupInteractions } from '../vanilla/interactions.js';

/**
 * EqCanvas.jsx
 * This component acts as the rendering surface for the Vanilla JS visualizer engine.
 * 
 * Usage:
 * It creates a standard HTML `<canvas>` element and passes its DOM reference back into 
 * the shared `ctxRef`. It then kickstarts the high-speed `requestAnimationFrame` drawing 
 * loop and attaches the custom mouse interaction physics (dragging nodes, hovering).
 * 
 * By using a Ref for the canvas, React never attempts to update the DOM inside the canvas 
 * itself, allowing the WebGL/Canvas API to run entirely independently at maximum performance.
 * 
 * @param {Object} props.ctxRef - The global state payload containing the AudioEngine and UI state.
 * @returns {JSX.Element} A div containing the raw canvas DOM node.
 */
export default function EqCanvas({ ctxRef }) {
    const canvasRef = useRef(null);

    useEffect(() => {
        if (!canvasRef.current || !ctxRef.current) return;
        
        // Link the canvas DOM element into the Vanilla context
        ctxRef.current.canvas = canvasRef.current;
        initVisualizer(canvasRef.current);
        
        // Initialize the Vanilla interaction listeners (drag/drop EQ nodes)
        setupInteractions(ctxRef.current);
        
        const { audioEngine, uiState, audioElement, automationState } = ctxRef.current;
        
        // Start the 60fps WebGL/Canvas visualizer loop
        // This runs entirely outside of React's render cycle!
        drawVisualizer(
            audioEngine.preAnalyser, 
            audioEngine.preDataArray, 
            audioEngine.postAnalyser, 
            audioEngine.postDataArray, 
            audioEngine.audioContext, 
            audioEngine.filters,
            uiState, 
            audioElement, 
            automationState 
        );
    }, [ctxRef]);

    return (
        <div className="canvas-container">
            <canvas ref={canvasRef} id="visualizer" width="800" height="400"></canvas>
        </div>
    );
}
