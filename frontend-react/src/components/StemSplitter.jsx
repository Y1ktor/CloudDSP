import React, { useState } from 'react';

export default function StemSplitter() {
    const [fileName, setFileName] = useState("No file loaded");
    const [splitMode, setSplitMode] = useState("6-stems");
    const [isSplitting, setIsSplitting] = useState(false);

    const handleFileUpload = (e) => {
        const file = e.target.files[0];
        if (file) setFileName(file.name);
    };

    return (
        <div style={{
            background: '#333',
            color: 'white',
            padding: '20px',
            borderRadius: '5px',
            width: '90vw',
            maxWidth: '1200px',
            margin: '0 auto 40px auto',
            boxSizing: 'border-box',
            boxShadow: '0 8px 16px rgba(0,0,0,0.5)',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px'
        }}>
            <h2 style={{ margin: 0, fontSize: '18px', borderBottom: '1px solid #555', paddingBottom: '10px' }}>
                Stem Splitting & Audio-to-MIDI
            </h2>
            
            {/* Top Control Bar (Styled like AudioPlayerBar) */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '15px', flexWrap: 'wrap' }}>
                <label htmlFor="stem-upload" className="upload-btn" style={{ margin: 0 }}>Browse...</label>
                <input type="file" id="stem-upload" accept="audio/*" style={{ display: 'none' }} onChange={handleFileUpload} />
                
                <div id="file-name-container" style={{ flexGrow: 1, margin: 0 }}>
                    <div id="file-name-display" style={{ color: fileName === "No file loaded" ? '#aaa' : '#fff' }}>{fileName}</div>
                </div>

                <div style={{ width: 'auto', minWidth: '150px' }}>
                    <select 
                        value={splitMode} 
                        onChange={(e) => setSplitMode(e.target.value)}
                        style={{
                            background: '#222',
                            color: '#fff',
                            border: '1px solid #444',
                            padding: '8px 10px',
                            borderRadius: '4px',
                            cursor: 'pointer',
                            outline: 'none',
                            width: '100%',
                            fontSize: '13px',
                            fontFamily: 'inherit'
                        }}
                    >
                        <option value="6-stems">6 Stems (Vocals / Drums / Bass / Piano / Guitar / Other)</option>
                        <option value="4-stems">4 Stems (Vocals / Drums / Bass / Other)</option>
                        <option value="2-stems">2 Stems (Vocals / Instrumental)</option>
                    </select>
                </div>

                <button 
                    onClick={() => setIsSplitting(!isSplitting)}
                    style={{
                        background: isSplitting ? '#555' : '#4CAF50',
                        color: 'white',
                        border: 'none',
                        padding: '8px 16px',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        fontWeight: 'bold',
                        transition: 'background-color 0.2s',
                        fontSize: '13px'
                    }}
                >
                    {isSplitting ? 'Processing...' : 'Execute'}
                </button>
            </div>

            {/* Results / Track Area Mockup */}
            <div style={{
                background: '#222',
                borderRadius: '4px',
                padding: '20px',
                minHeight: '150px',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
                alignItems: 'center',
                color: '#777',
                border: '1px dashed #444'
            }}>
                {isSplitting ? (
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
                        <div style={{ 
                            width: '40px', height: '40px', 
                            border: '4px solid #444', borderTop: '4px solid #4CAF50', 
                            borderRadius: '50%', animation: 'spin 1s linear infinite' 
                        }} />
                        <div>Running ML Inference... (UI Mockup)</div>
                        <style>{`
                            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                        `}</style>
                    </div>
                ) : (
                    <div>Stem extraction and MIDI results will appear here as downloadable multitracks</div>
                )}
            </div>
        </div>
    );
}
