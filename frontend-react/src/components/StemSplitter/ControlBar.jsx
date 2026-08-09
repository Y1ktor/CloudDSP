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
    executeLinkExtraction,
    file,
    errorMsg,
}) {
    const [showLinkPopup, setShowLinkPopup] = React.useState(false);
    const [linkInput, setLinkInput] = React.useState('');
    const [isSubmittingLink, setIsSubmittingLink] = React.useState(false);

    const handleLinkSubmit = async () => {
        const sourceUrl = linkInput.trim();
        if (!sourceUrl || isSubmittingLink) return;
        setIsSubmittingLink(true);
        try {
            const started = await executeLinkExtraction(sourceUrl);
            if (started) {
                setShowLinkPopup(false);
                setLinkInput('');
            }
        } finally {
            setIsSubmittingLink(false);
        }
    };

    return (
        <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '15px', flexWrap: 'wrap' }}>
                <label htmlFor="stem-upload" className="upload-btn" style={{ margin: 0, cursor: isSplitting ? 'not-allowed' : 'pointer', opacity: isSplitting ? 0.5 : 1 }}>Browse...</label>
                <input type="file" id="stem-upload" accept="audio/*" style={{ display: 'none' }} onChange={handleFileUpload} disabled={isSplitting} />
                
                <div id="file-name-container" onClick={() => !isSplitting && setShowLinkPopup(true)} style={{ flexGrow: 1, margin: 0, cursor: isSplitting ? 'not-allowed' : 'pointer' }} title="Click to paste a link">
                    <div id="file-name-display" style={{ color: (fileName === "No file loaded" || fileName === "Upload audio or paste a link") ? '#aaa' : '#fff' }}>
                        {fileName === "No file loaded" ? "Upload audio or paste a link" : fileName}
                    </div>
                </div>

                {showLinkPopup && (
                    <div style={{
                        position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                        backgroundColor: 'rgba(0,0,0,0.7)', zIndex: 9999,
                        display: 'flex', alignItems: 'center', justifyContent: 'center'
                    }} onClick={() => setShowLinkPopup(false)}>
                        <div style={{
                            background: '#1e1e1e', padding: '20px 24px', borderRadius: '12px',
                            width: '80%', maxWidth: '800px', display: 'flex', flexDirection: 'column', gap: '12px',
                            boxShadow: '0 12px 30px rgba(0,0,0,0.5)', border: '1px solid #333'
                        }} onClick={e => e.stopPropagation()}>
                            <p style={{ margin: 0, fontSize: '14px', color: '#ccc', fontWeight: '500' }}>
                                Paste a URL to extract audio directly
                            </p>
                            <div style={{ display: 'flex', gap: '10px' }}>
                                <input 
                                    type="text" 
                                    value={linkInput} 
                                    onChange={e => setLinkInput(e.target.value)} 
                                    onKeyDown={e => e.key === 'Enter' && handleLinkSubmit()}
                                    placeholder="https://www.youtube.com/watch?v=..."
                                    autoFocus
                                    style={{ 
                                        flexGrow: 1, padding: '10px 14px', borderRadius: '6px', border: '1px solid #444', 
                                        background: '#111', color: 'white', boxSizing: 'border-box',
                                        fontSize: '14px', outline: 'none'
                                    }}
                                />
                                <button
                                    type="button"
                                    onClick={handleLinkSubmit}
                                    disabled={!linkInput.trim() || isSubmittingLink}
                                    style={{
                                        background: !linkInput.trim() || isSubmittingLink ? '#555' : '#4CAF50', color: 'white',
                                        border: 'none', borderRadius: '6px', padding: '0 15px', fontSize: '13px', fontWeight: 'bold',
                                        cursor: !linkInput.trim() || isSubmittingLink ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap',
                                    }}
                                >
                                    {isSubmittingLink ? 'Starting…' : 'Extract & Split'}
                                </button>
                            </div>
                        </div>
                    </div>
                )}

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
                    onClick={() => file && executeStemSplit()}
                    disabled={isSplitting || !file}
                    style={{
                        background: isSplitting || !file ? '#555' : '#4CAF50',
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
