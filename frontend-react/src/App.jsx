import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH63ZKRSFTB5&Signature=lnOj8zONgwcS%2F6CY3HNob4%2BSLm8%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEkaCXVzLWVhc3QtMSJGMEQCIFNlKvqOEvCLHDd7P9%2Fk4II4xIWnJnlCzZvyZmcYsVITAiAz2%2Fm54BxG1zf%2F7%2BSMkCtpQth4ipTTSSxHfoSoA7hb%2BCqQBAgREAAaDDUxMjM4MzkyNjE5OSIMJ%2FhDiin1PZT1x%2F6lKu0Dqzq78zWHg91Jhqqd1ub5tN3l13fjA4Fq0K3vARCUHijrbIbHMxH%2FGmINDPg5w%2FbY0RdbK0Ht0VBRZfPc%2FUU3z1YVpJq3df%2F9nJo2LeHqj2kAKHn%2FYuPH0tRAFdjKkYvdAyOJkTygV1H4SzX1e2HyvVL1akYut4gVTxMHtCOpz08oep1u0aA1HgkL1hvTYln0VMaZkMWjN5svv%2F8P4bEjC95%2FOjYGgJxjAFVGCw8D3JCmq57Ggs7vH53iFiJ4D5u3TbRFyM1MJWapKtqRymWa0ZF4OYODxViExKJAnzm9JwDwPYgQpHooIObFisfdfYTgRW8PNJwgkH0JZolj9PnJXn7oVcRys1O68tkWufr5kyHaB57zYtTHmSfRDpzo2QUg9snoawmeGqPYZfwByrdJflQSXylUiHZ3chAcwLinPs%2BQJdc0E%2FZ7OWe%2FGHWvz1hgowelU0tL%2BxN%2Bl%2BL%2BS%2F922mmemoUwns%2BY1w%2FGR0a53CygZYumxbeCRDEc20tFNgLO2N9Xowh%2FsneYA9hCvjhOfuOak4c4yfbzFjvrXgDafStaFGuONI%2FoIBtsJpPiQGr1ZFjHRKtmvTm8PIta4OMNLEc1gccAFaJldVpIUcZgLt%2BHjf%2B5CbxId%2FrA0mbTdFmBONY8Q0yLuFc35v55ajDJvJ%2FSBjqjAfg0kFUdLIy9frOOR8D0D9ICH15kbOS3YmTej9GPnPCDCH2FTCXocoFRD6TWTtJ7JZUmdownRJeaEvx6GqCY96twJFxp0WSTZo1Ggg1%2FMNJqnDFMacSPFLexhd4MmRV7gK8ZIDATjx965r8n4SIMX147iwCl8bCZ5BmKYcnmMncBH0jPVLy5e2eJJZluZR%2FuF8ZgwVoq4JFXEyzbQBdIt8cgNUI%3D&Expires=1783109260", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH63ZKRSFTB5&Signature=d9Au3tSn4nY2qqE%2FnzwyiKCsJeE%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEkaCXVzLWVhc3QtMSJGMEQCIFNlKvqOEvCLHDd7P9%2Fk4II4xIWnJnlCzZvyZmcYsVITAiAz2%2Fm54BxG1zf%2F7%2BSMkCtpQth4ipTTSSxHfoSoA7hb%2BCqQBAgREAAaDDUxMjM4MzkyNjE5OSIMJ%2FhDiin1PZT1x%2F6lKu0Dqzq78zWHg91Jhqqd1ub5tN3l13fjA4Fq0K3vARCUHijrbIbHMxH%2FGmINDPg5w%2FbY0RdbK0Ht0VBRZfPc%2FUU3z1YVpJq3df%2F9nJo2LeHqj2kAKHn%2FYuPH0tRAFdjKkYvdAyOJkTygV1H4SzX1e2HyvVL1akYut4gVTxMHtCOpz08oep1u0aA1HgkL1hvTYln0VMaZkMWjN5svv%2F8P4bEjC95%2FOjYGgJxjAFVGCw8D3JCmq57Ggs7vH53iFiJ4D5u3TbRFyM1MJWapKtqRymWa0ZF4OYODxViExKJAnzm9JwDwPYgQpHooIObFisfdfYTgRW8PNJwgkH0JZolj9PnJXn7oVcRys1O68tkWufr5kyHaB57zYtTHmSfRDpzo2QUg9snoawmeGqPYZfwByrdJflQSXylUiHZ3chAcwLinPs%2BQJdc0E%2FZ7OWe%2FGHWvz1hgowelU0tL%2BxN%2Bl%2BL%2BS%2F922mmemoUwns%2BY1w%2FGR0a53CygZYumxbeCRDEc20tFNgLO2N9Xowh%2FsneYA9hCvjhOfuOak4c4yfbzFjvrXgDafStaFGuONI%2FoIBtsJpPiQGr1ZFjHRKtmvTm8PIta4OMNLEc1gccAFaJldVpIUcZgLt%2BHjf%2B5CbxId%2FrA0mbTdFmBONY8Q0yLuFc35v55ajDJvJ%2FSBjqjAfg0kFUdLIy9frOOR8D0D9ICH15kbOS3YmTej9GPnPCDCH2FTCXocoFRD6TWTtJ7JZUmdownRJeaEvx6GqCY96twJFxp0WSTZo1Ggg1%2FMNJqnDFMacSPFLexhd4MmRV7gK8ZIDATjx965r8n4SIMX147iwCl8bCZ5BmKYcnmMncBH0jPVLy5e2eJJZluZR%2FuF8ZgwVoq4JFXEyzbQBdIt8cgNUI%3D&Expires=1783109260", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH63ZKRSFTB5&Signature=kTn5S9rSR2vr93QPNIvx6wRhaCc%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEkaCXVzLWVhc3QtMSJGMEQCIFNlKvqOEvCLHDd7P9%2Fk4II4xIWnJnlCzZvyZmcYsVITAiAz2%2Fm54BxG1zf%2F7%2BSMkCtpQth4ipTTSSxHfoSoA7hb%2BCqQBAgREAAaDDUxMjM4MzkyNjE5OSIMJ%2FhDiin1PZT1x%2F6lKu0Dqzq78zWHg91Jhqqd1ub5tN3l13fjA4Fq0K3vARCUHijrbIbHMxH%2FGmINDPg5w%2FbY0RdbK0Ht0VBRZfPc%2FUU3z1YVpJq3df%2F9nJo2LeHqj2kAKHn%2FYuPH0tRAFdjKkYvdAyOJkTygV1H4SzX1e2HyvVL1akYut4gVTxMHtCOpz08oep1u0aA1HgkL1hvTYln0VMaZkMWjN5svv%2F8P4bEjC95%2FOjYGgJxjAFVGCw8D3JCmq57Ggs7vH53iFiJ4D5u3TbRFyM1MJWapKtqRymWa0ZF4OYODxViExKJAnzm9JwDwPYgQpHooIObFisfdfYTgRW8PNJwgkH0JZolj9PnJXn7oVcRys1O68tkWufr5kyHaB57zYtTHmSfRDpzo2QUg9snoawmeGqPYZfwByrdJflQSXylUiHZ3chAcwLinPs%2BQJdc0E%2FZ7OWe%2FGHWvz1hgowelU0tL%2BxN%2Bl%2BL%2BS%2F922mmemoUwns%2BY1w%2FGR0a53CygZYumxbeCRDEc20tFNgLO2N9Xowh%2FsneYA9hCvjhOfuOak4c4yfbzFjvrXgDafStaFGuONI%2FoIBtsJpPiQGr1ZFjHRKtmvTm8PIta4OMNLEc1gccAFaJldVpIUcZgLt%2BHjf%2B5CbxId%2FrA0mbTdFmBONY8Q0yLuFc35v55ajDJvJ%2FSBjqjAfg0kFUdLIy9frOOR8D0D9ICH15kbOS3YmTej9GPnPCDCH2FTCXocoFRD6TWTtJ7JZUmdownRJeaEvx6GqCY96twJFxp0WSTZo1Ggg1%2FMNJqnDFMacSPFLexhd4MmRV7gK8ZIDATjx965r8n4SIMX147iwCl8bCZ5BmKYcnmMncBH0jPVLy5e2eJJZluZR%2FuF8ZgwVoq4JFXEyzbQBdIt8cgNUI%3D&Expires=1783109260", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH63ZKRSFTB5&Signature=qfN5FbLNfArcF95V32aMxL1op1c%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEkaCXVzLWVhc3QtMSJGMEQCIFNlKvqOEvCLHDd7P9%2Fk4II4xIWnJnlCzZvyZmcYsVITAiAz2%2Fm54BxG1zf%2F7%2BSMkCtpQth4ipTTSSxHfoSoA7hb%2BCqQBAgREAAaDDUxMjM4MzkyNjE5OSIMJ%2FhDiin1PZT1x%2F6lKu0Dqzq78zWHg91Jhqqd1ub5tN3l13fjA4Fq0K3vARCUHijrbIbHMxH%2FGmINDPg5w%2FbY0RdbK0Ht0VBRZfPc%2FUU3z1YVpJq3df%2F9nJo2LeHqj2kAKHn%2FYuPH0tRAFdjKkYvdAyOJkTygV1H4SzX1e2HyvVL1akYut4gVTxMHtCOpz08oep1u0aA1HgkL1hvTYln0VMaZkMWjN5svv%2F8P4bEjC95%2FOjYGgJxjAFVGCw8D3JCmq57Ggs7vH53iFiJ4D5u3TbRFyM1MJWapKtqRymWa0ZF4OYODxViExKJAnzm9JwDwPYgQpHooIObFisfdfYTgRW8PNJwgkH0JZolj9PnJXn7oVcRys1O68tkWufr5kyHaB57zYtTHmSfRDpzo2QUg9snoawmeGqPYZfwByrdJflQSXylUiHZ3chAcwLinPs%2BQJdc0E%2FZ7OWe%2FGHWvz1hgowelU0tL%2BxN%2Bl%2BL%2BS%2F922mmemoUwns%2BY1w%2FGR0a53CygZYumxbeCRDEc20tFNgLO2N9Xowh%2FsneYA9hCvjhOfuOak4c4yfbzFjvrXgDafStaFGuONI%2FoIBtsJpPiQGr1ZFjHRKtmvTm8PIta4OMNLEc1gccAFaJldVpIUcZgLt%2BHjf%2B5CbxId%2FrA0mbTdFmBONY8Q0yLuFc35v55ajDJvJ%2FSBjqjAfg0kFUdLIy9frOOR8D0D9ICH15kbOS3YmTej9GPnPCDCH2FTCXocoFRD6TWTtJ7JZUmdownRJeaEvx6GqCY96twJFxp0WSTZo1Ggg1%2FMNJqnDFMacSPFLexhd4MmRV7gK8ZIDATjx965r8n4SIMX147iwCl8bCZ5BmKYcnmMncBH0jPVLy5e2eJJZluZR%2FuF8ZgwVoq4JFXEyzbQBdIt8cgNUI%3D&Expires=1783109260"};
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
    // Start empty to test the upload flow
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
                else if (data.type === "processing_complete") {
                    console.log("Stems received globally from AWS Batch:", data.stems);
                    setStemUrls(data.stems);
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
        setStatusMessage("Mocking Cloud Upload...");
        setStemUrls(null);
        
        // ==========================================
        // MOCK: Fast-forward to completion
        // ==========================================
        setTimeout(() => {
            setStatusMessage("Mocking Batch GPU Processing...");
            setTimeout(() => {
                setStemUrls(MOCK_PAYLOAD);
                setIsSplitting(false);
                setStatusMessage("Complete!");
            }, 1000);
        }, 1000);
        
        return; // Skip actual AWS logic for now

        try {
            // ==========================================
            // 1. ACTUAL S3 PRESIGNED URL FETCH
            // ==========================================
            const fileType = stemFile.type || 'application/octet-stream';
            const res = await fetch(`${API_URL}?filename=${encodeURIComponent(stemFileName)}&filetype=${encodeURIComponent(fileType)}&connectionId=${encodeURIComponent(awsConnectionId)}&stemMode=${encodeURIComponent(splitMode)}`);
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
