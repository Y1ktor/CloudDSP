import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH63XZIR2OKJ&Signature=%2B%2BYJM%2BkZup9e%2BQvHNgDX9w2Z8NI%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECcaCXVzLWVhc3QtMSJIMEYCIQCk4Cqfh2uD6HMGaUA12clZsbAsXq59Pt4PHD51TnCKRAIhAKF0r2IyYfbzTJFAk7NF4z1PJrZkRcGHBoiar9aWqtEvKpkECPD%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5Igx1XbrOL6eFJe%2BM5D4q7QORW5wx6XgSKbA%2Fn9a3nEnYUY2gA7iZ0sKKw2rdTYU8da4bKWlHQMmTUqbGxl6N0RAU9U0l9i1vRJ1D3jLZhXE151YpmGE59gCaagPM2pG0gDy1MZjGqVTimvPFGjD9FA5Yi%2BogqAaoUdfwSWsCXyG%2BDu0o9DNGGclk9yLjiQYuNHtWLTZ5e6V3IbYQqZJXzwVD0CLGND6D9Oa32wvHOUwPB%2F29lV77D4FpWAZegN%2FNWHp8187sZH2lpY4rw1cdx8GUqTqoGXygNVAc7upHwB7aiVNBa%2F2O19EEZ4QVzWIoZIje1EKYJ9a8xOnfIxlFPc1ypgR6L2IbI%2FBXtRSuYLxKgXCSPqf3vStA6UrKz%2F8lU2mmD9GrQmfwUGE0fHEFF5tD8%2BL5bsjF%2Fo0hj0ILu5UDiCYtF89PnwV0xY6hTX14i17JB%2F6t8ltaoFwOT3mWimOfMrBJxAC26LvpqwPEX2CdhHt%2B4GKeC7Ol9lrV61GB9o1lLoryJZVNYNnqKIZySVG1y5V88dghR4jcTkQFOr2VZsEyakJb8HbjyXj7fHhwLkQWCR7Qv2ogG7k1mED1XG5oQq5cxlVad9fJdwn4qre8sqpkS2SYAvDjprwkEF%2FnIIp%2F86nlLuxl5JOz%2FzKpZWTpLRrGFE7J2y4u%2FxsBMKC60NIGOqEBo283wbypFhOSOvnfumjCHWnxJPSbnIBqc4TKqliyadBrXsbC0G2weSEnrlVUN9qKAgqBp0RU0BAH%2BBLaMmGSAvSIDjVN37wz2eztKFO0xyfuCXRFcaR1MVW7UpVOVgNmiYxsmdZQ68xrX3i1Xjtk2QThbNStzqeidauerhhsNgOemRqO9FwvFtaN0NPFYvIhYZrE2FkBMw2xyXKI72EByAU%3D&Expires=1783911779", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH63XZIR2OKJ&Signature=ocfizMFBLPvT%2BzQ1xXuHGWbRvSA%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECcaCXVzLWVhc3QtMSJIMEYCIQCk4Cqfh2uD6HMGaUA12clZsbAsXq59Pt4PHD51TnCKRAIhAKF0r2IyYfbzTJFAk7NF4z1PJrZkRcGHBoiar9aWqtEvKpkECPD%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5Igx1XbrOL6eFJe%2BM5D4q7QORW5wx6XgSKbA%2Fn9a3nEnYUY2gA7iZ0sKKw2rdTYU8da4bKWlHQMmTUqbGxl6N0RAU9U0l9i1vRJ1D3jLZhXE151YpmGE59gCaagPM2pG0gDy1MZjGqVTimvPFGjD9FA5Yi%2BogqAaoUdfwSWsCXyG%2BDu0o9DNGGclk9yLjiQYuNHtWLTZ5e6V3IbYQqZJXzwVD0CLGND6D9Oa32wvHOUwPB%2F29lV77D4FpWAZegN%2FNWHp8187sZH2lpY4rw1cdx8GUqTqoGXygNVAc7upHwB7aiVNBa%2F2O19EEZ4QVzWIoZIje1EKYJ9a8xOnfIxlFPc1ypgR6L2IbI%2FBXtRSuYLxKgXCSPqf3vStA6UrKz%2F8lU2mmD9GrQmfwUGE0fHEFF5tD8%2BL5bsjF%2Fo0hj0ILu5UDiCYtF89PnwV0xY6hTX14i17JB%2F6t8ltaoFwOT3mWimOfMrBJxAC26LvpqwPEX2CdhHt%2B4GKeC7Ol9lrV61GB9o1lLoryJZVNYNnqKIZySVG1y5V88dghR4jcTkQFOr2VZsEyakJb8HbjyXj7fHhwLkQWCR7Qv2ogG7k1mED1XG5oQq5cxlVad9fJdwn4qre8sqpkS2SYAvDjprwkEF%2FnIIp%2F86nlLuxl5JOz%2FzKpZWTpLRrGFE7J2y4u%2FxsBMKC60NIGOqEBo283wbypFhOSOvnfumjCHWnxJPSbnIBqc4TKqliyadBrXsbC0G2weSEnrlVUN9qKAgqBp0RU0BAH%2BBLaMmGSAvSIDjVN37wz2eztKFO0xyfuCXRFcaR1MVW7UpVOVgNmiYxsmdZQ68xrX3i1Xjtk2QThbNStzqeidauerhhsNgOemRqO9FwvFtaN0NPFYvIhYZrE2FkBMw2xyXKI72EByAU%3D&Expires=1783911779", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH63XZIR2OKJ&Signature=rL87LzHh8vmu2b9zzhc63m0PpR8%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECcaCXVzLWVhc3QtMSJIMEYCIQCk4Cqfh2uD6HMGaUA12clZsbAsXq59Pt4PHD51TnCKRAIhAKF0r2IyYfbzTJFAk7NF4z1PJrZkRcGHBoiar9aWqtEvKpkECPD%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5Igx1XbrOL6eFJe%2BM5D4q7QORW5wx6XgSKbA%2Fn9a3nEnYUY2gA7iZ0sKKw2rdTYU8da4bKWlHQMmTUqbGxl6N0RAU9U0l9i1vRJ1D3jLZhXE151YpmGE59gCaagPM2pG0gDy1MZjGqVTimvPFGjD9FA5Yi%2BogqAaoUdfwSWsCXyG%2BDu0o9DNGGclk9yLjiQYuNHtWLTZ5e6V3IbYQqZJXzwVD0CLGND6D9Oa32wvHOUwPB%2F29lV77D4FpWAZegN%2FNWHp8187sZH2lpY4rw1cdx8GUqTqoGXygNVAc7upHwB7aiVNBa%2F2O19EEZ4QVzWIoZIje1EKYJ9a8xOnfIxlFPc1ypgR6L2IbI%2FBXtRSuYLxKgXCSPqf3vStA6UrKz%2F8lU2mmD9GrQmfwUGE0fHEFF5tD8%2BL5bsjF%2Fo0hj0ILu5UDiCYtF89PnwV0xY6hTX14i17JB%2F6t8ltaoFwOT3mWimOfMrBJxAC26LvpqwPEX2CdhHt%2B4GKeC7Ol9lrV61GB9o1lLoryJZVNYNnqKIZySVG1y5V88dghR4jcTkQFOr2VZsEyakJb8HbjyXj7fHhwLkQWCR7Qv2ogG7k1mED1XG5oQq5cxlVad9fJdwn4qre8sqpkS2SYAvDjprwkEF%2FnIIp%2F86nlLuxl5JOz%2FzKpZWTpLRrGFE7J2y4u%2FxsBMKC60NIGOqEBo283wbypFhOSOvnfumjCHWnxJPSbnIBqc4TKqliyadBrXsbC0G2weSEnrlVUN9qKAgqBp0RU0BAH%2BBLaMmGSAvSIDjVN37wz2eztKFO0xyfuCXRFcaR1MVW7UpVOVgNmiYxsmdZQ68xrX3i1Xjtk2QThbNStzqeidauerhhsNgOemRqO9FwvFtaN0NPFYvIhYZrE2FkBMw2xyXKI72EByAU%3D&Expires=1783911779", "piano": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/piano.wav?AWSAccessKeyId=ASIAXOTDWH63XZIR2OKJ&Signature=%2FvtIuqAp2uFB3%2FS3f8ynUltGVEQ%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECcaCXVzLWVhc3QtMSJIMEYCIQCk4Cqfh2uD6HMGaUA12clZsbAsXq59Pt4PHD51TnCKRAIhAKF0r2IyYfbzTJFAk7NF4z1PJrZkRcGHBoiar9aWqtEvKpkECPD%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5Igx1XbrOL6eFJe%2BM5D4q7QORW5wx6XgSKbA%2Fn9a3nEnYUY2gA7iZ0sKKw2rdTYU8da4bKWlHQMmTUqbGxl6N0RAU9U0l9i1vRJ1D3jLZhXE151YpmGE59gCaagPM2pG0gDy1MZjGqVTimvPFGjD9FA5Yi%2BogqAaoUdfwSWsCXyG%2BDu0o9DNGGclk9yLjiQYuNHtWLTZ5e6V3IbYQqZJXzwVD0CLGND6D9Oa32wvHOUwPB%2F29lV77D4FpWAZegN%2FNWHp8187sZH2lpY4rw1cdx8GUqTqoGXygNVAc7upHwB7aiVNBa%2F2O19EEZ4QVzWIoZIje1EKYJ9a8xOnfIxlFPc1ypgR6L2IbI%2FBXtRSuYLxKgXCSPqf3vStA6UrKz%2F8lU2mmD9GrQmfwUGE0fHEFF5tD8%2BL5bsjF%2Fo0hj0ILu5UDiCYtF89PnwV0xY6hTX14i17JB%2F6t8ltaoFwOT3mWimOfMrBJxAC26LvpqwPEX2CdhHt%2B4GKeC7Ol9lrV61GB9o1lLoryJZVNYNnqKIZySVG1y5V88dghR4jcTkQFOr2VZsEyakJb8HbjyXj7fHhwLkQWCR7Qv2ogG7k1mED1XG5oQq5cxlVad9fJdwn4qre8sqpkS2SYAvDjprwkEF%2FnIIp%2F86nlLuxl5JOz%2FzKpZWTpLRrGFE7J2y4u%2FxsBMKC60NIGOqEBo283wbypFhOSOvnfumjCHWnxJPSbnIBqc4TKqliyadBrXsbC0G2weSEnrlVUN9qKAgqBp0RU0BAH%2BBLaMmGSAvSIDjVN37wz2eztKFO0xyfuCXRFcaR1MVW7UpVOVgNmiYxsmdZQ68xrX3i1Xjtk2QThbNStzqeidauerhhsNgOemRqO9FwvFtaN0NPFYvIhYZrE2FkBMw2xyXKI72EByAU%3D&Expires=1783911779", "guitar": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/guitar.wav?AWSAccessKeyId=ASIAXOTDWH63XZIR2OKJ&Signature=qa8%2FBbQcJpQuf6l%2BRv%2Fde4nWc2U%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECcaCXVzLWVhc3QtMSJIMEYCIQCk4Cqfh2uD6HMGaUA12clZsbAsXq59Pt4PHD51TnCKRAIhAKF0r2IyYfbzTJFAk7NF4z1PJrZkRcGHBoiar9aWqtEvKpkECPD%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5Igx1XbrOL6eFJe%2BM5D4q7QORW5wx6XgSKbA%2Fn9a3nEnYUY2gA7iZ0sKKw2rdTYU8da4bKWlHQMmTUqbGxl6N0RAU9U0l9i1vRJ1D3jLZhXE151YpmGE59gCaagPM2pG0gDy1MZjGqVTimvPFGjD9FA5Yi%2BogqAaoUdfwSWsCXyG%2BDu0o9DNGGclk9yLjiQYuNHtWLTZ5e6V3IbYQqZJXzwVD0CLGND6D9Oa32wvHOUwPB%2F29lV77D4FpWAZegN%2FNWHp8187sZH2lpY4rw1cdx8GUqTqoGXygNVAc7upHwB7aiVNBa%2F2O19EEZ4QVzWIoZIje1EKYJ9a8xOnfIxlFPc1ypgR6L2IbI%2FBXtRSuYLxKgXCSPqf3vStA6UrKz%2F8lU2mmD9GrQmfwUGE0fHEFF5tD8%2BL5bsjF%2Fo0hj0ILu5UDiCYtF89PnwV0xY6hTX14i17JB%2F6t8ltaoFwOT3mWimOfMrBJxAC26LvpqwPEX2CdhHt%2B4GKeC7Ol9lrV61GB9o1lLoryJZVNYNnqKIZySVG1y5V88dghR4jcTkQFOr2VZsEyakJb8HbjyXj7fHhwLkQWCR7Qv2ogG7k1mED1XG5oQq5cxlVad9fJdwn4qre8sqpkS2SYAvDjprwkEF%2FnIIp%2F86nlLuxl5JOz%2FzKpZWTpLRrGFE7J2y4u%2FxsBMKC60NIGOqEBo283wbypFhOSOvnfumjCHWnxJPSbnIBqc4TKqliyadBrXsbC0G2weSEnrlVUN9qKAgqBp0RU0BAH%2BBLaMmGSAvSIDjVN37wz2eztKFO0xyfuCXRFcaR1MVW7UpVOVgNmiYxsmdZQ68xrX3i1Xjtk2QThbNStzqeidauerhhsNgOemRqO9FwvFtaN0NPFYvIhYZrE2FkBMw2xyXKI72EByAU%3D&Expires=1783911779", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH63XZIR2OKJ&Signature=%2BtHPH6JQuFgZBtqoR8a5CM2La%2Bk%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECcaCXVzLWVhc3QtMSJIMEYCIQCk4Cqfh2uD6HMGaUA12clZsbAsXq59Pt4PHD51TnCKRAIhAKF0r2IyYfbzTJFAk7NF4z1PJrZkRcGHBoiar9aWqtEvKpkECPD%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5Igx1XbrOL6eFJe%2BM5D4q7QORW5wx6XgSKbA%2Fn9a3nEnYUY2gA7iZ0sKKw2rdTYU8da4bKWlHQMmTUqbGxl6N0RAU9U0l9i1vRJ1D3jLZhXE151YpmGE59gCaagPM2pG0gDy1MZjGqVTimvPFGjD9FA5Yi%2BogqAaoUdfwSWsCXyG%2BDu0o9DNGGclk9yLjiQYuNHtWLTZ5e6V3IbYQqZJXzwVD0CLGND6D9Oa32wvHOUwPB%2F29lV77D4FpWAZegN%2FNWHp8187sZH2lpY4rw1cdx8GUqTqoGXygNVAc7upHwB7aiVNBa%2F2O19EEZ4QVzWIoZIje1EKYJ9a8xOnfIxlFPc1ypgR6L2IbI%2FBXtRSuYLxKgXCSPqf3vStA6UrKz%2F8lU2mmD9GrQmfwUGE0fHEFF5tD8%2BL5bsjF%2Fo0hj0ILu5UDiCYtF89PnwV0xY6hTX14i17JB%2F6t8ltaoFwOT3mWimOfMrBJxAC26LvpqwPEX2CdhHt%2B4GKeC7Ol9lrV61GB9o1lLoryJZVNYNnqKIZySVG1y5V88dghR4jcTkQFOr2VZsEyakJb8HbjyXj7fHhwLkQWCR7Qv2ogG7k1mED1XG5oQq5cxlVad9fJdwn4qre8sqpkS2SYAvDjprwkEF%2FnIIp%2F86nlLuxl5JOz%2FzKpZWTpLRrGFE7J2y4u%2FxsBMKC60NIGOqEBo283wbypFhOSOvnfumjCHWnxJPSbnIBqc4TKqliyadBrXsbC0G2weSEnrlVUN9qKAgqBp0RU0BAH%2BBLaMmGSAvSIDjVN37wz2eztKFO0xyfuCXRFcaR1MVW7UpVOVgNmiYxsmdZQ68xrX3i1Xjtk2QThbNStzqeidauerhhsNgOemRqO9FwvFtaN0NPFYvIhYZrE2FkBMw2xyXKI72EByAU%3D&Expires=1783911779"};
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
        
        if (false) {
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
        } // close if (false)
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
