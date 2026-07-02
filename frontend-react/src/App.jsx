import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH63Z6ZUOK5S&Signature=941R4hLzXfwX9r6Pv08S1dfi9fQ%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEDIaCXVzLWVhc3QtMSJHMEUCIQCipO8Vk4DHD%2FYO0GwYY%2BeZxKV5CuT4sEc1QnKNaKUK7wIgaq5ygm1oes6vCpgJpwUQZiLnAO1dUrfknKfXj2aDHUoqmQQI%2B%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDBtXfHLKoIQgxD5xeSrtA4KFVG5ffA4mAnguBL4bUk951icUHBZZl%2BiQirOxwTAKhM3k6HWauVu4cN8J8YJD6NrvjIvfiFiKAlOTqYvttpAPTcEl9RpPKv4WUEuiXM6Ufg95tvlo0VVKYyBy3Atiy8%2BNCRp%2FJHkbZLQApD7z6NpZGWf54EwWl5Jv35mP0LIlfvhVWcSkNPuYGYflcG211AZ0o7g65ECM%2FKLI6teAcMfO%2BSljQwb2cc%2BjHNUpNzJrz65Ryd9z8cXlCI7kHr%2F%2FTN4VWpTInaJ0D43NsGPjOySiCFGXTI9P1ve%2Bk431AmtwpobLD%2BO0WNCuzOe9f11bif88L48fB0taXGhAtpmvcdMq0R2WTYjN0mGxqp4sh1dF7AhXu5XHt2YCJY539DciE1uwp0AjJGEou90%2B2RqOKe%2BHnO1yKZqqafqtTi1Ms0CJY7xEeEGdN5DudH8w7x%2BjC7do%2BfQeXq66IIzjxFISv9fT8V796%2BkkibBJpQkaMt9XHMw3GeBIRQ2fObpomZ8imTtmjv0NESKSQUpkBd5gG8%2FXd6yspkPOx7AXWQWMVFYnVZQa4PEdV9RKvqdkccvuXuWi%2F6YV1qBb04VEjQzXaLkrL2wXzc5AY0m0RHXdb34tq78WBXv4P0WBqLPLCBAwa9ViZjPJIJJP%2F3S%2F4GEw182a0gY6ogGY1Dj25hQlhTRTK8AkVIf8IdKenEOKsxung9Q2M0o8eZbpXV%2BDcYrqR3UMeUFUs4gbwfOV0lr%2Bb4iJg%2BBDu5v2NYHG47fdqxJ%2FF8dwysDKxwFvRgg5Nf2flnXAp%2FkD3Bu05ByFqB0gQs1Y%2F1u7RNIgROB%2BVsw0tRrMXsIiTsokXPhznBfojOBf4MkjhLJ%2F6bc2oOWumAbJf0lzgUkvsP%2FMvLI%3D&Expires=1783029550", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH63Z6ZUOK5S&Signature=twnv3SrdEnQ%2Bt1Syw96kTOR9blc%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEDIaCXVzLWVhc3QtMSJHMEUCIQCipO8Vk4DHD%2FYO0GwYY%2BeZxKV5CuT4sEc1QnKNaKUK7wIgaq5ygm1oes6vCpgJpwUQZiLnAO1dUrfknKfXj2aDHUoqmQQI%2B%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDBtXfHLKoIQgxD5xeSrtA4KFVG5ffA4mAnguBL4bUk951icUHBZZl%2BiQirOxwTAKhM3k6HWauVu4cN8J8YJD6NrvjIvfiFiKAlOTqYvttpAPTcEl9RpPKv4WUEuiXM6Ufg95tvlo0VVKYyBy3Atiy8%2BNCRp%2FJHkbZLQApD7z6NpZGWf54EwWl5Jv35mP0LIlfvhVWcSkNPuYGYflcG211AZ0o7g65ECM%2FKLI6teAcMfO%2BSljQwb2cc%2BjHNUpNzJrz65Ryd9z8cXlCI7kHr%2F%2FTN4VWpTInaJ0D43NsGPjOySiCFGXTI9P1ve%2Bk431AmtwpobLD%2BO0WNCuzOe9f11bif88L48fB0taXGhAtpmvcdMq0R2WTYjN0mGxqp4sh1dF7AhXu5XHt2YCJY539DciE1uwp0AjJGEou90%2B2RqOKe%2BHnO1yKZqqafqtTi1Ms0CJY7xEeEGdN5DudH8w7x%2BjC7do%2BfQeXq66IIzjxFISv9fT8V796%2BkkibBJpQkaMt9XHMw3GeBIRQ2fObpomZ8imTtmjv0NESKSQUpkBd5gG8%2FXd6yspkPOx7AXWQWMVFYnVZQa4PEdV9RKvqdkccvuXuWi%2F6YV1qBb04VEjQzXaLkrL2wXzc5AY0m0RHXdb34tq78WBXv4P0WBqLPLCBAwa9ViZjPJIJJP%2F3S%2F4GEw182a0gY6ogGY1Dj25hQlhTRTK8AkVIf8IdKenEOKsxung9Q2M0o8eZbpXV%2BDcYrqR3UMeUFUs4gbwfOV0lr%2Bb4iJg%2BBDu5v2NYHG47fdqxJ%2FF8dwysDKxwFvRgg5Nf2flnXAp%2FkD3Bu05ByFqB0gQs1Y%2F1u7RNIgROB%2BVsw0tRrMXsIiTsokXPhznBfojOBf4MkjhLJ%2F6bc2oOWumAbJf0lzgUkvsP%2FMvLI%3D&Expires=1783029550", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH63Z6ZUOK5S&Signature=Suv7V94mBO6waKe8j%2FCC%2FgnvaJw%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEDIaCXVzLWVhc3QtMSJHMEUCIQCipO8Vk4DHD%2FYO0GwYY%2BeZxKV5CuT4sEc1QnKNaKUK7wIgaq5ygm1oes6vCpgJpwUQZiLnAO1dUrfknKfXj2aDHUoqmQQI%2B%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDBtXfHLKoIQgxD5xeSrtA4KFVG5ffA4mAnguBL4bUk951icUHBZZl%2BiQirOxwTAKhM3k6HWauVu4cN8J8YJD6NrvjIvfiFiKAlOTqYvttpAPTcEl9RpPKv4WUEuiXM6Ufg95tvlo0VVKYyBy3Atiy8%2BNCRp%2FJHkbZLQApD7z6NpZGWf54EwWl5Jv35mP0LIlfvhVWcSkNPuYGYflcG211AZ0o7g65ECM%2FKLI6teAcMfO%2BSljQwb2cc%2BjHNUpNzJrz65Ryd9z8cXlCI7kHr%2F%2FTN4VWpTInaJ0D43NsGPjOySiCFGXTI9P1ve%2Bk431AmtwpobLD%2BO0WNCuzOe9f11bif88L48fB0taXGhAtpmvcdMq0R2WTYjN0mGxqp4sh1dF7AhXu5XHt2YCJY539DciE1uwp0AjJGEou90%2B2RqOKe%2BHnO1yKZqqafqtTi1Ms0CJY7xEeEGdN5DudH8w7x%2BjC7do%2BfQeXq66IIzjxFISv9fT8V796%2BkkibBJpQkaMt9XHMw3GeBIRQ2fObpomZ8imTtmjv0NESKSQUpkBd5gG8%2FXd6yspkPOx7AXWQWMVFYnVZQa4PEdV9RKvqdkccvuXuWi%2F6YV1qBb04VEjQzXaLkrL2wXzc5AY0m0RHXdb34tq78WBXv4P0WBqLPLCBAwa9ViZjPJIJJP%2F3S%2F4GEw182a0gY6ogGY1Dj25hQlhTRTK8AkVIf8IdKenEOKsxung9Q2M0o8eZbpXV%2BDcYrqR3UMeUFUs4gbwfOV0lr%2Bb4iJg%2BBDu5v2NYHG47fdqxJ%2FF8dwysDKxwFvRgg5Nf2flnXAp%2FkD3Bu05ByFqB0gQs1Y%2F1u7RNIgROB%2BVsw0tRrMXsIiTsokXPhznBfojOBf4MkjhLJ%2F6bc2oOWumAbJf0lzgUkvsP%2FMvLI%3D&Expires=1783029550", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH63Z6ZUOK5S&Signature=hEvtekwwaxOBxnifYSau0vBYnLQ%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEDIaCXVzLWVhc3QtMSJHMEUCIQCipO8Vk4DHD%2FYO0GwYY%2BeZxKV5CuT4sEc1QnKNaKUK7wIgaq5ygm1oes6vCpgJpwUQZiLnAO1dUrfknKfXj2aDHUoqmQQI%2B%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDBtXfHLKoIQgxD5xeSrtA4KFVG5ffA4mAnguBL4bUk951icUHBZZl%2BiQirOxwTAKhM3k6HWauVu4cN8J8YJD6NrvjIvfiFiKAlOTqYvttpAPTcEl9RpPKv4WUEuiXM6Ufg95tvlo0VVKYyBy3Atiy8%2BNCRp%2FJHkbZLQApD7z6NpZGWf54EwWl5Jv35mP0LIlfvhVWcSkNPuYGYflcG211AZ0o7g65ECM%2FKLI6teAcMfO%2BSljQwb2cc%2BjHNUpNzJrz65Ryd9z8cXlCI7kHr%2F%2FTN4VWpTInaJ0D43NsGPjOySiCFGXTI9P1ve%2Bk431AmtwpobLD%2BO0WNCuzOe9f11bif88L48fB0taXGhAtpmvcdMq0R2WTYjN0mGxqp4sh1dF7AhXu5XHt2YCJY539DciE1uwp0AjJGEou90%2B2RqOKe%2BHnO1yKZqqafqtTi1Ms0CJY7xEeEGdN5DudH8w7x%2BjC7do%2BfQeXq66IIzjxFISv9fT8V796%2BkkibBJpQkaMt9XHMw3GeBIRQ2fObpomZ8imTtmjv0NESKSQUpkBd5gG8%2FXd6yspkPOx7AXWQWMVFYnVZQa4PEdV9RKvqdkccvuXuWi%2F6YV1qBb04VEjQzXaLkrL2wXzc5AY0m0RHXdb34tq78WBXv4P0WBqLPLCBAwa9ViZjPJIJJP%2F3S%2F4GEw182a0gY6ogGY1Dj25hQlhTRTK8AkVIf8IdKenEOKsxung9Q2M0o8eZbpXV%2BDcYrqR3UMeUFUs4gbwfOV0lr%2Bb4iJg%2BBDu5v2NYHG47fdqxJ%2FF8dwysDKxwFvRgg5Nf2flnXAp%2FkD3Bu05ByFqB0gQs1Y%2F1u7RNIgROB%2BVsw0tRrMXsIiTsokXPhznBfojOBf4MkjhLJ%2F6bc2oOWumAbJf0lzgUkvsP%2FMvLI%3D&Expires=1783029550"};
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
