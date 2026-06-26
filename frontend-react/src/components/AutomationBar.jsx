import React, { useState, useEffect } from 'react';

export default function AutomationBar({ ctxRef }) {
    const [isMenuOpen, setIsMenuOpen] = useState(false);
    const [isImportOpen, setIsImportOpen] = useState(false);
    const [selectedName, setSelectedName] = useState("New File");
    const [importOptions, setImportOptions] = useState([]);

    const { automationState } = ctxRef.current;

    useEffect(() => {
        const refreshOptions = () => {
            const options = [];
            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                if (key && key.startsWith('cloudDspAutomation_')) {
                    options.push({ key, name: key.replace('cloudDspAutomation_', '') });
                }
            }
            setImportOptions(options);
        };
        
        refreshOptions();
        
        // Bind callbacks so the Vanilla engine can trigger React state updates
        ctxRef.current.onAutomationSaved = refreshOptions;
        
        ctxRef.current.onNewAutomationFile = (timestamp) => {
            setSelectedName(timestamp);
            refreshOptions();
        };

        // Global click handler to close menus
        const handleGlobalClick = () => {
            setIsMenuOpen(false);
            setIsImportOpen(false);
        };
        document.addEventListener('click', handleGlobalClick);
        return () => document.removeEventListener('click', handleGlobalClick);
    }, []);

    const toggleMenu = (e) => {
        e.stopPropagation();
        setIsMenuOpen(!isMenuOpen);
        setIsImportOpen(false);
    };

    const toggleImport = (e) => {
        e.stopPropagation();
        setIsImportOpen(!isImportOpen);
    };

    const handleSelectNew = (e) => {
        e.stopPropagation();
        setSelectedName("New File");
        automationState.data = [];
        automationState.activeData = null;
        automationState.currentFileKey = null;
        setIsMenuOpen(false);
        setIsImportOpen(false);
        console.log("Started new automation file");
    };

    const handleSelectSave = (e) => {
        e.stopPropagation();
        setIsMenuOpen(false);
        setIsImportOpen(false);
        console.log("Manual save triggered");
        // Trigger save logic if mapped in ctx
        if (ctxRef.current.triggerSave) {
            ctxRef.current.triggerSave();
        }
    };

    const handleSelectImport = (e, option) => {
        e.stopPropagation();
        setSelectedName(option.name);
        setIsMenuOpen(false);
        setIsImportOpen(false);
        try {
            const savedData = sessionStorage.getItem(option.key);
            let parsed = JSON.parse(savedData);
            if (Array.isArray(parsed)) {
                automationState.activeData = {
                    regions: [{ start: parsed[0].timestamp, end: parsed[parsed.length - 1].timestamp }],
                    frames: parsed
                };
            } else {
                automationState.activeData = parsed;
            }
            automationState.currentFileKey = option.key;
            console.log(`Loaded ${option.key} from session storage`);
        } catch (err) {
            console.error(err);
        }
    };

    return (
        <div id="automation-bar">
            <span style={{ fontSize: 13, fontWeight: 'bold', color: '#aaa' }}>Automation:</span>
            <div id="automation-dropdown" className="custom-select">
                <div className="select-selected" onClick={toggleMenu}>
                    <div className="select-text-container">
                        <div className="select-text">{selectedName}</div>
                    </div>
                </div>
                {isMenuOpen && (
                    <ul className="select-items" style={{ display: 'block' }}>
                        <li onClick={handleSelectNew}>New File</li>
                        <li className="has-submenu" onClick={toggleImport}>
                            Import <span style={{ float: 'right', fontSize: 10, marginTop: 3 }}>▶</span>
                            {isImportOpen && (
                                <ul className="submenu" style={{ display: 'block' }}>
                                    {importOptions.length === 0 ? (
                                        <li className="disabled">None</li>
                                    ) : (
                                        importOptions.map(opt => (
                                            <li key={opt.key} onClick={(e) => handleSelectImport(e, opt)}>{opt.name}</li>
                                        ))
                                    )}
                                </ul>
                            )}
                        </li>
                        <li onClick={handleSelectSave}>Save</li>
                    </ul>
                )}
            </div>
            <div style={{ flexGrow: 1 }}></div>
        </div>
    );
}
