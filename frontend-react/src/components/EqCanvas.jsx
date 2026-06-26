import React, { useEffect, useRef } from 'react';
import { drawVisualizer, initVisualizer } from '../vanilla/visualizer.js';
import { setupInteractions } from '../vanilla/interactions.js';

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
