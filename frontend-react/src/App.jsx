import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH63Z5RFSDG4&Signature=vjB7nv9Wrhb2eQ5bMF8yv%2BheALY%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEAoaCXVzLWVhc3QtMSJHMEUCIQDhxdh7WPoku46I%2Bf4M%2BgQwIr8w28ICluOVcFoBECOCkwIgPj1pprcp4%2F3X4NXb4Pm%2Fd9DKZtaJTqX2CokHnfl8io8qmQQI0%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDCxnB1i6Dzs2xHf2XSrtA0rgHJQcZWH1%2FhtxeRBZ1LU1wBRRqh37NYpoNTnOcrF0C%2Fy3lnNKvhQatGF4RtKZ7qJdUGX5XczrbAb%2FHMJDyIMoOTLYH8l7th2JRwRlx4foQjNryxc7ZEZzuFlfIBxEkFzo1Jl%2Bs9n3g4HmROSo9p7kBLVZLnKt3yL2zhw%2FNeTWbbU75P9qDLTQgV40jhr9jx3o8H1EIUgudoRpOwClGHhRkVww64%2FES1dtp8TT4SgXWjw6FF%2BNo6gtBp%2F6pQuqnikDI9ymZdxcEdVJQgV6oBmjMQ%2Frui%2Bz5V7pTH8G5dEFHctK4lgm135PY2RJte9OaHJEnVgTCNjQg%2ByBhX1TF5r6h69Bv45ND2gJMOBIZoeOPymyQMmXS5v3Bjx6sY1tqWSmVdQRuQQwY%2Flha5Vr%2B0kEJ2l4%2FHp7acgmq78%2B6CwEVzxWIxi2tWJxDFT4etACDcwESpGsKw9PmWQzAPeQFks%2FTnIs6Bz1ak33w5f5nsZ9A5sDfo5tWdtjofENWUsiPd8vp%2FNlpCKmJGPDPD2TC86Uf%2BWxJFTKFo7DOCrErLB8y%2FW5SzvQhVh4ZjbbcmOhRaFcDXwo%2F0fNO91cYYF0R1iQw9jNtQKkab5Sw3IaVydkN921CM51vOyx8vkFTr4vO1EsqOcXhC5ZmuOygy0w6t2R0gY6ogEGfUIdCb3aUuFlK4T3PWJt9CEodoRBUpyZS6XGzvI%2BXeLuluGKTl%2BTh061Xzjmn1PA2VTaEXf1ti4Yj56wATByJYqcmwd41R%2BtNWlp3sqXH1aySE5oNkcsIZ%2Bv5LMDOUmbqgkdOozMt9YsDikxHLT0NgDj6pBgH06%2F9yIWWNLphieVOGyunW%2B%2FLSQ7VjOnwKZJj5Qrv%2F8V0XiqBe3lNjkmKac%3D&Expires=1782873341", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH63Z5RFSDG4&Signature=TVxxyl934QaVBFZ9KkItbbk%2BCNg%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEAoaCXVzLWVhc3QtMSJHMEUCIQDhxdh7WPoku46I%2Bf4M%2BgQwIr8w28ICluOVcFoBECOCkwIgPj1pprcp4%2F3X4NXb4Pm%2Fd9DKZtaJTqX2CokHnfl8io8qmQQI0%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDCxnB1i6Dzs2xHf2XSrtA0rgHJQcZWH1%2FhtxeRBZ1LU1wBRRqh37NYpoNTnOcrF0C%2Fy3lnNKvhQatGF4RtKZ7qJdUGX5XczrbAb%2FHMJDyIMoOTLYH8l7th2JRwRlx4foQjNryxc7ZEZzuFlfIBxEkFzo1Jl%2Bs9n3g4HmROSo9p7kBLVZLnKt3yL2zhw%2FNeTWbbU75P9qDLTQgV40jhr9jx3o8H1EIUgudoRpOwClGHhRkVww64%2FES1dtp8TT4SgXWjw6FF%2BNo6gtBp%2F6pQuqnikDI9ymZdxcEdVJQgV6oBmjMQ%2Frui%2Bz5V7pTH8G5dEFHctK4lgm135PY2RJte9OaHJEnVgTCNjQg%2ByBhX1TF5r6h69Bv45ND2gJMOBIZoeOPymyQMmXS5v3Bjx6sY1tqWSmVdQRuQQwY%2Flha5Vr%2B0kEJ2l4%2FHp7acgmq78%2B6CwEVzxWIxi2tWJxDFT4etACDcwESpGsKw9PmWQzAPeQFks%2FTnIs6Bz1ak33w5f5nsZ9A5sDfo5tWdtjofENWUsiPd8vp%2FNlpCKmJGPDPD2TC86Uf%2BWxJFTKFo7DOCrErLB8y%2FW5SzvQhVh4ZjbbcmOhRaFcDXwo%2F0fNO91cYYF0R1iQw9jNtQKkab5Sw3IaVydkN921CM51vOyx8vkFTr4vO1EsqOcXhC5ZmuOygy0w6t2R0gY6ogEGfUIdCb3aUuFlK4T3PWJt9CEodoRBUpyZS6XGzvI%2BXeLuluGKTl%2BTh061Xzjmn1PA2VTaEXf1ti4Yj56wATByJYqcmwd41R%2BtNWlp3sqXH1aySE5oNkcsIZ%2Bv5LMDOUmbqgkdOozMt9YsDikxHLT0NgDj6pBgH06%2F9yIWWNLphieVOGyunW%2B%2FLSQ7VjOnwKZJj5Qrv%2F8V0XiqBe3lNjkmKac%3D&Expires=1782873341", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH63Z5RFSDG4&Signature=zp%2BXZ8m1yomRKoq7aP60BbHy%2BlA%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEAoaCXVzLWVhc3QtMSJHMEUCIQDhxdh7WPoku46I%2Bf4M%2BgQwIr8w28ICluOVcFoBECOCkwIgPj1pprcp4%2F3X4NXb4Pm%2Fd9DKZtaJTqX2CokHnfl8io8qmQQI0%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDCxnB1i6Dzs2xHf2XSrtA0rgHJQcZWH1%2FhtxeRBZ1LU1wBRRqh37NYpoNTnOcrF0C%2Fy3lnNKvhQatGF4RtKZ7qJdUGX5XczrbAb%2FHMJDyIMoOTLYH8l7th2JRwRlx4foQjNryxc7ZEZzuFlfIBxEkFzo1Jl%2Bs9n3g4HmROSo9p7kBLVZLnKt3yL2zhw%2FNeTWbbU75P9qDLTQgV40jhr9jx3o8H1EIUgudoRpOwClGHhRkVww64%2FES1dtp8TT4SgXWjw6FF%2BNo6gtBp%2F6pQuqnikDI9ymZdxcEdVJQgV6oBmjMQ%2Frui%2Bz5V7pTH8G5dEFHctK4lgm135PY2RJte9OaHJEnVgTCNjQg%2ByBhX1TF5r6h69Bv45ND2gJMOBIZoeOPymyQMmXS5v3Bjx6sY1tqWSmVdQRuQQwY%2Flha5Vr%2B0kEJ2l4%2FHp7acgmq78%2B6CwEVzxWIxi2tWJxDFT4etACDcwESpGsKw9PmWQzAPeQFks%2FTnIs6Bz1ak33w5f5nsZ9A5sDfo5tWdtjofENWUsiPd8vp%2FNlpCKmJGPDPD2TC86Uf%2BWxJFTKFo7DOCrErLB8y%2FW5SzvQhVh4ZjbbcmOhRaFcDXwo%2F0fNO91cYYF0R1iQw9jNtQKkab5Sw3IaVydkN921CM51vOyx8vkFTr4vO1EsqOcXhC5ZmuOygy0w6t2R0gY6ogEGfUIdCb3aUuFlK4T3PWJt9CEodoRBUpyZS6XGzvI%2BXeLuluGKTl%2BTh061Xzjmn1PA2VTaEXf1ti4Yj56wATByJYqcmwd41R%2BtNWlp3sqXH1aySE5oNkcsIZ%2Bv5LMDOUmbqgkdOozMt9YsDikxHLT0NgDj6pBgH06%2F9yIWWNLphieVOGyunW%2B%2FLSQ7VjOnwKZJj5Qrv%2F8V0XiqBe3lNjkmKac%3D&Expires=1782873341", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH63Z5RFSDG4&Signature=%2FaBlIt2v82q%2BUVbbEdXVKA%2BjuLo%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEAoaCXVzLWVhc3QtMSJHMEUCIQDhxdh7WPoku46I%2Bf4M%2BgQwIr8w28ICluOVcFoBECOCkwIgPj1pprcp4%2F3X4NXb4Pm%2Fd9DKZtaJTqX2CokHnfl8io8qmQQI0%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDCxnB1i6Dzs2xHf2XSrtA0rgHJQcZWH1%2FhtxeRBZ1LU1wBRRqh37NYpoNTnOcrF0C%2Fy3lnNKvhQatGF4RtKZ7qJdUGX5XczrbAb%2FHMJDyIMoOTLYH8l7th2JRwRlx4foQjNryxc7ZEZzuFlfIBxEkFzo1Jl%2Bs9n3g4HmROSo9p7kBLVZLnKt3yL2zhw%2FNeTWbbU75P9qDLTQgV40jhr9jx3o8H1EIUgudoRpOwClGHhRkVww64%2FES1dtp8TT4SgXWjw6FF%2BNo6gtBp%2F6pQuqnikDI9ymZdxcEdVJQgV6oBmjMQ%2Frui%2Bz5V7pTH8G5dEFHctK4lgm135PY2RJte9OaHJEnVgTCNjQg%2ByBhX1TF5r6h69Bv45ND2gJMOBIZoeOPymyQMmXS5v3Bjx6sY1tqWSmVdQRuQQwY%2Flha5Vr%2B0kEJ2l4%2FHp7acgmq78%2B6CwEVzxWIxi2tWJxDFT4etACDcwESpGsKw9PmWQzAPeQFks%2FTnIs6Bz1ak33w5f5nsZ9A5sDfo5tWdtjofENWUsiPd8vp%2FNlpCKmJGPDPD2TC86Uf%2BWxJFTKFo7DOCrErLB8y%2FW5SzvQhVh4ZjbbcmOhRaFcDXwo%2F0fNO91cYYF0R1iQw9jNtQKkab5Sw3IaVydkN921CM51vOyx8vkFTr4vO1EsqOcXhC5ZmuOygy0w6t2R0gY6ogEGfUIdCb3aUuFlK4T3PWJt9CEodoRBUpyZS6XGzvI%2BXeLuluGKTl%2BTh061Xzjmn1PA2VTaEXf1ti4Yj56wATByJYqcmwd41R%2BtNWlp3sqXH1aySE5oNkcsIZ%2Bv5LMDOUmbqgkdOozMt9YsDikxHLT0NgDj6pBgH06%2F9yIWWNLphieVOGyunW%2B%2FLSQ7VjOnwKZJj5Qrv%2F8V0XiqBe3lNjkmKac%3D&Expires=1782873341"};
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
    // Set to MOCK_PAYLOAD for immediate UI testing. Change back to null for production!
    const [stemUrls, setStemUrls] = useState(MOCK_PAYLOAD);
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
        setStatusMessage("Generating secure S3 upload link...");
        setStemUrls(null);
        
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
