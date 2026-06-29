import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter';

const WEBSOCKET_URL = "wss://placeholder-websocket-api.execute-api.us-east-1.amazonaws.com/dev";

// A simple top-level navigation component
function NavBar() {
    const location = useLocation();
    
    const navStyle = {
        background: '#222',
        padding: '15px 20px',
        display: 'flex',
        gap: '20px',
        borderBottom: '1px solid #444',
        marginBottom: '40px',
        alignItems: 'center',
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)'
    };

    const getLinkStyle = (path) => ({
        color: location.pathname === path ? '#fff' : '#aaa',
        textDecoration: 'none',
        fontWeight: 'bold',
        padding: '8px 16px',
        borderRadius: '4px',
        background: location.pathname === path ? '#4CAF50' : 'transparent',
        transition: 'all 0.2s ease-in-out'
    });

    return (
        <div style={navStyle}>
            <div style={{ color: '#fff', fontWeight: '900', fontSize: '20px', marginRight: '20px', letterSpacing: '1px' }}>
                CloudDSP
            </div>
            <Link to="/" style={getLinkStyle('/')}>Interactive EQ</Link>
            <Link to="/stems" style={getLinkStyle('/stems')}>Stem Splitter</Link>
        </div>
    );
}

export default function App() {
    // =========================================================
    // HOISTED STEM SPLITTER STATE
    // This lives here globally so it survives page navigation!
    // =========================================================
    const [stemFile, setStemFile] = useState(null);
    const [stemFileName, setStemFileName] = useState("No file loaded");
    const [splitMode, setSplitMode] = useState("6-stems");
    
    const [isSplitting, setIsSplitting] = useState(false);
    const [statusMessage, setStatusMessage] = useState("");
    const [stemUrls, setStemUrls] = useState(null);
    const [errorMsg, setErrorMsg] = useState("");
    
    // NEW: Store the Connection ID instantly so we don't wait for it later
    const [awsConnectionId, setAwsConnectionId] = useState(null);
    
    const socketRef = useRef(null);

    // Global cleanup just in case App ever unmounts entirely
    useEffect(() => {
        return () => {
            if (socketRef.current) socketRef.current.close();
        };
    }, []);

    // NEW: We trigger this silently in the background when the user opens the Stem Splitter page
    const connectWebSocket = React.useCallback(() => {
        // Don't reconnect if we are already connected or connecting
        if (socketRef.current && (socketRef.current.readyState === WebSocket.OPEN || socketRef.current.readyState === WebSocket.CONNECTING)) {
            return;
        }
        
        console.log("Initiating background WebSocket connection...");
        socketRef.current = new WebSocket(WEBSOCKET_URL);
        
        socketRef.current.onopen = () => {
            console.log("WebSocket opened globally in App.jsx.");
            // Send a tiny JSON packet to explicitly trigger the $default route on AWS
            socketRef.current.send(JSON.stringify({ action: "echo" }));
        };
        
        socketRef.current.onerror = (err) => {
            console.error("WebSocket Error:", err);
            setErrorMsg("Failed to connect to cloud WebSocket server.");
        };

        socketRef.current.onclose = () => {
            console.log("WebSocket connection gracefully closed by App.jsx.");
            setAwsConnectionId(null);
        };

        socketRef.current.onmessage = async (event) => {
            try {
                const data = JSON.parse(event.data);
                
                // Phase 1: Silent Background Connection ID Echo
                if (data.type === "connected") {
                    setAwsConnectionId(data.connectionId);
                    console.log(`Connected globally in background! ID: ${data.connectionId}`);
                }
                
                // Phase 3: Stems Finished Processing
                else if (data.type === "stems_ready") {
                    console.log("Stems received globally from AWS Batch:", data.urls);
                    setStemUrls(data.urls);
                    setIsSplitting(false);
                    setStatusMessage("Complete!");
                    socketRef.current.close();
                }
                
                // Phase 4: Server Error
                else if (data.type === "error") {
                    setErrorMsg(data.message || "An AWS backend error occurred.");
                    setIsSplitting(false);
                    socketRef.current.close();
                }
            } catch (err) {
                console.error("Failed to parse websocket message:", err);
            }
        };
    }, []); // Use useCallback to stabilize the reference

    const closeWebSocket = React.useCallback(() => {
        if (socketRef.current) {
            console.log("Closing WebSocket to save idle AWS connection costs...");
            socketRef.current.close();
        }
    }, []);


    const executeStemSplit = async () => {
        if (!stemFile) {
            setErrorMsg("Please select a file first.");
            return;
        }
        
        if (!awsConnectionId) {
            setErrorMsg("Still establishing secure connection to AWS... please try again in a few seconds.");
            connectWebSocket();
            return;
        }
        
        setIsSplitting(true);
        setErrorMsg("");
        setStatusMessage("Generating secure S3 upload link...");
        setStemUrls(null);
        
        try {
            // ==========================================
            // 1. ACTUAL S3 PRESIGNED URL FETCH
            // ==========================================
            const fileType = stemFile.type || 'application/octet-stream';
            const res = await fetch(`https://placeholder-http-api.execute-api.us-east-1.amazonaws.com/upload-url?filename=${encodeURIComponent(stemFileName)}&filetype=${encodeURIComponent(fileType)}&connectionId=${encodeURIComponent(awsConnectionId)}&stemMode=${encodeURIComponent(splitMode)}`);
            const data = await res.json();
            
            if (data.error) {
                throw new Error(data.error);
            }
            
            const uploadUrl = data.uploadUrl;
            
            setStatusMessage("Uploading audio to S3...");
            
            // ==========================================
            // 2. ACTUAL S3 UPLOAD LOGIC
            // ==========================================
            const uploadRes = await fetch(uploadUrl, {
                method: 'PUT',
                body: stemFile,
                headers: { 
                    'Content-Type': fileType,
                    'x-amz-meta-connection-id': awsConnectionId,
                    'x-amz-meta-stem-mode': splitMode
                }
            });
            
            if (!uploadRes.ok) {
                throw new Error("Failed to upload to S3. Check your CORS configuration.");
            }
            
            setStatusMessage("Processing Stems on AWS Batch GPUs...");

        } catch (err) {
            console.error("Upload error:", err);
            setErrorMsg("Failed to upload audio to cloud.");
            setIsSplitting(false);
        }
    };

    // Bundle the state to pass down as props to the StemSplitter component
    const stemProps = {
        file: stemFile, setFile: setStemFile,
        fileName: stemFileName, setFileName: setStemFileName,
        splitMode, setSplitMode,
        isSplitting, statusMessage, stemUrls, errorMsg, setErrorMsg, setStemUrls,
        executeStemSplit, connectWebSocket, closeWebSocket
    };

    return (
        <BrowserRouter>
            <div style={{ minHeight: '100vh' }}>
                <NavBar />
                <Routes>
                    <Route path="/" element={<EqPage />} />
                    <Route path="/stems" element={
                        <div style={{ display: 'flex', justifyContent: 'center' }}>
                            <StemSplitter {...stemProps} />
                        </div>
                    } />
                </Routes>
            </div>
        </BrowserRouter>
    );
}
