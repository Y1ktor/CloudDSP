/**
 * ControlBar.jsx
 * 
 * Top-level control bar for the StemSplitter component.
 * Handles file uploading, mode selection, and the main split trigger.
 */
import React from 'react';

/**
 * ControlBar
 * 
 * @param {Object} props - Component props
 * @param {boolean} props.isSplitting - Is AWS processing running
 * @param {Function} props.handleFileUpload - Callback when a file is selected
 * @param {string} props.fileName - The name of the currently selected file
 * @param {string} props.splitMode - The active demucs split mode
 * @param {Function} props.setSplitMode - State setter for split mode
 * @param {Function} props.executeStemSplit - Function to trigger the WebSocket & upload
 * @param {File} props.file - The active file object
 * @param {string} props.errorMsg - Error message string (if any)
 */
export default function ControlBar({
    isSplitting,
    handleFileUpload,
    fileName,
    splitMode,
    setSplitMode,
    executeStemSplit,
    file,
    errorMsg
}) {
    return (
        <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '15px', flexWrap: 'wrap' }}>
                <label htmlFor="stem-upload" className="upload-btn" style={{ margin: 0, cursor: isSplitting ? 'not-allowed' : 'pointer', opacity: isSplitting ? 0.5 : 1 }}>Browse...</label>
                <input type="file" id="stem-upload" accept="audio/*" style={{ display: 'none' }} onChange={handleFileUpload} disabled={isSplitting} />
                
                <div id="file-name-container" style={{ flexGrow: 1, margin: 0 }}>
                    <div id="file-name-display" style={{ color: fileName === "No file loaded" ? '#aaa' : '#fff' }}>{fileName}</div>
                </div>

                <div style={{ width: 'auto', minWidth: '150px' }}>
                    <select 
                        value={splitMode} 
                        onChange={(e) => setSplitMode(e.target.value)}
                        disabled={isSplitting}
                        style={{
                            background: '#222',
                            color: '#fff',
                            border: '1px solid #444',
                            padding: '8px 10px',
                            borderRadius: '4px',
                            cursor: isSplitting ? 'not-allowed' : 'pointer',
                            outline: 'none',
                            width: '100%',
                            fontSize: '13px',
                            fontFamily: 'inherit',
                            opacity: isSplitting ? 0.5 : 1
                        }}
                    >
                        <option value="6-stems">6 Stems (Vocals / Drums / Bass / Piano / Guitar / Other)</option>
                        <option value="4-stems">4 Stems (Vocals / Drums / Bass / Other)</option>
                        <option value="2-stems">2 Stems (Vocals / Instrumental)</option>
                    </select>
                </div>

                <button 
                    onClick={executeStemSplit}
                    disabled={isSplitting || !file}
                    style={{
                        background: isSplitting ? '#555' : (!file ? '#555' : '#4CAF50'),
                        color: 'white',
                        border: 'none',
                        padding: '8px 16px',
                        borderRadius: '4px',
                        cursor: isSplitting || !file ? 'not-allowed' : 'pointer',
                        fontWeight: 'bold',
                        transition: 'background-color 0.2s',
                        fontSize: '13px'
                    }}
                >
                    {isSplitting ? 'Processing...' : 'Upload & Split'}
                </button>
            </div>
            
            {/* Error Message */}
            {errorMsg && (
                <div style={{ color: '#ff6b6b', background: '#3b2222', padding: '10px', borderRadius: '4px', border: '1px solid #ff4444' }}>
                    {errorMsg}
                </div>
            )}
        </>
    );
}
