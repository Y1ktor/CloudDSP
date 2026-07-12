import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH636337FQGW&Signature=aUTXKp%2F%2FVpGmnvVBLqLpo1iVqzM%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECIaCXVzLWVhc3QtMSJHMEUCIF1y44e48JKBkhSs2uKyp7SOB77W7aRmTbPLL3FPg4f4AiEAoJEn7jgEgQoWef8fpPeFcEOUA19BVOeim4lnDBAXb7MqmQQI6v%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDLVN9cmTfCGFm1NxIirtA7wS0pZ62vsbNpx1OO4oVoa%2F0A4gXgegOTMEU7yD6VHrPrYLmlpoh4X%2F5bvM0HAFwnaKgdwP0Dy7jw6qvxinLnsbIm1m3cdU%2B8xAxim8UW%2B6Gwsa03O3ePJ2IqkSdffgo7OkxekARE0GZqOPxDDAT69uvfoNj1HjLNjVje1hOp4ivU5yfNOqoJSl%2Ba4yfwizEjtCI8QlnCKbND3rme4c9cw%2FB1vssDzeysmrVXXedpHh0uweUDEoGJnKxAW5MtxyPwJXolsGbykSNmfobJSUArf3IJ1PVYN%2F8jO9%2BW2fTIUKOHQ424p3pi0gRZA771L2oK9%2BENzA26a99xiGix5PBpaoueAEK1P%2Bod%2FTxFoDRQzSstShRwx1eoHDvXGbFylYNokdaXZ%2F2%2F1wWB4IecBOciVLG0AxJPXYmMNzXMDHBt2QpYG0lzz%2FYb6HeETvnZ91Kk17Ko5Ao9ul1hQ6EEp0TpM6KtkPY01uCkc9PC%2B7ERxCW4z5fsyGT3%2F%2BkbqVKc7mZg9q%2F5gBvO%2BQIZbHTEGOXDO%2B%2BHr%2BkdGIobMNDYPOPeyzeWhX2f4l5WsC1QO%2FKZvlskA0xIIFwVGihe2Si6DEHTqEMmCBnPP4J5SoWPR32pWeKCFNjfZ46CJw8tUY7tjbMsT4CGgVWxfNS%2FMOIbIw4JPP0gY6ogEPe6xhS3GQ0QBgv8l3DKGf48AZMoZhRBA64F%2ByLhg7jyqyhhdbnystErZzSXneMGnrnIZVQz3Imbc2tp0CodNOC1cvnUzT4qhcwMxLHXNm%2FrnEilAlZB778H2PDSXisLEghsyBO%2By4miHHO%2F%2F8dXdXWasd6YZd8lWqbOB0cIyOi24h8IH43bqr4EsF8zWQk%2FOABDREl1I4YG6vRf%2BLD3DphjM%3D&Expires=1783890467", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH636337FQGW&Signature=2x4lBhPclSxF%2F7%2FAHHOJtJa2gr0%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECIaCXVzLWVhc3QtMSJHMEUCIF1y44e48JKBkhSs2uKyp7SOB77W7aRmTbPLL3FPg4f4AiEAoJEn7jgEgQoWef8fpPeFcEOUA19BVOeim4lnDBAXb7MqmQQI6v%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDLVN9cmTfCGFm1NxIirtA7wS0pZ62vsbNpx1OO4oVoa%2F0A4gXgegOTMEU7yD6VHrPrYLmlpoh4X%2F5bvM0HAFwnaKgdwP0Dy7jw6qvxinLnsbIm1m3cdU%2B8xAxim8UW%2B6Gwsa03O3ePJ2IqkSdffgo7OkxekARE0GZqOPxDDAT69uvfoNj1HjLNjVje1hOp4ivU5yfNOqoJSl%2Ba4yfwizEjtCI8QlnCKbND3rme4c9cw%2FB1vssDzeysmrVXXedpHh0uweUDEoGJnKxAW5MtxyPwJXolsGbykSNmfobJSUArf3IJ1PVYN%2F8jO9%2BW2fTIUKOHQ424p3pi0gRZA771L2oK9%2BENzA26a99xiGix5PBpaoueAEK1P%2Bod%2FTxFoDRQzSstShRwx1eoHDvXGbFylYNokdaXZ%2F2%2F1wWB4IecBOciVLG0AxJPXYmMNzXMDHBt2QpYG0lzz%2FYb6HeETvnZ91Kk17Ko5Ao9ul1hQ6EEp0TpM6KtkPY01uCkc9PC%2B7ERxCW4z5fsyGT3%2F%2BkbqVKc7mZg9q%2F5gBvO%2BQIZbHTEGOXDO%2B%2BHr%2BkdGIobMNDYPOPeyzeWhX2f4l5WsC1QO%2FKZvlskA0xIIFwVGihe2Si6DEHTqEMmCBnPP4J5SoWPR32pWeKCFNjfZ46CJw8tUY7tjbMsT4CGgVWxfNS%2FMOIbIw4JPP0gY6ogEPe6xhS3GQ0QBgv8l3DKGf48AZMoZhRBA64F%2ByLhg7jyqyhhdbnystErZzSXneMGnrnIZVQz3Imbc2tp0CodNOC1cvnUzT4qhcwMxLHXNm%2FrnEilAlZB778H2PDSXisLEghsyBO%2By4miHHO%2F%2F8dXdXWasd6YZd8lWqbOB0cIyOi24h8IH43bqr4EsF8zWQk%2FOABDREl1I4YG6vRf%2BLD3DphjM%3D&Expires=1783890467", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH636337FQGW&Signature=BfnqexqrcMK%2BSW%2FXQfn6ovtP05Q%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECIaCXVzLWVhc3QtMSJHMEUCIF1y44e48JKBkhSs2uKyp7SOB77W7aRmTbPLL3FPg4f4AiEAoJEn7jgEgQoWef8fpPeFcEOUA19BVOeim4lnDBAXb7MqmQQI6v%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDLVN9cmTfCGFm1NxIirtA7wS0pZ62vsbNpx1OO4oVoa%2F0A4gXgegOTMEU7yD6VHrPrYLmlpoh4X%2F5bvM0HAFwnaKgdwP0Dy7jw6qvxinLnsbIm1m3cdU%2B8xAxim8UW%2B6Gwsa03O3ePJ2IqkSdffgo7OkxekARE0GZqOPxDDAT69uvfoNj1HjLNjVje1hOp4ivU5yfNOqoJSl%2Ba4yfwizEjtCI8QlnCKbND3rme4c9cw%2FB1vssDzeysmrVXXedpHh0uweUDEoGJnKxAW5MtxyPwJXolsGbykSNmfobJSUArf3IJ1PVYN%2F8jO9%2BW2fTIUKOHQ424p3pi0gRZA771L2oK9%2BENzA26a99xiGix5PBpaoueAEK1P%2Bod%2FTxFoDRQzSstShRwx1eoHDvXGbFylYNokdaXZ%2F2%2F1wWB4IecBOciVLG0AxJPXYmMNzXMDHBt2QpYG0lzz%2FYb6HeETvnZ91Kk17Ko5Ao9ul1hQ6EEp0TpM6KtkPY01uCkc9PC%2B7ERxCW4z5fsyGT3%2F%2BkbqVKc7mZg9q%2F5gBvO%2BQIZbHTEGOXDO%2B%2BHr%2BkdGIobMNDYPOPeyzeWhX2f4l5WsC1QO%2FKZvlskA0xIIFwVGihe2Si6DEHTqEMmCBnPP4J5SoWPR32pWeKCFNjfZ46CJw8tUY7tjbMsT4CGgVWxfNS%2FMOIbIw4JPP0gY6ogEPe6xhS3GQ0QBgv8l3DKGf48AZMoZhRBA64F%2ByLhg7jyqyhhdbnystErZzSXneMGnrnIZVQz3Imbc2tp0CodNOC1cvnUzT4qhcwMxLHXNm%2FrnEilAlZB778H2PDSXisLEghsyBO%2By4miHHO%2F%2F8dXdXWasd6YZd8lWqbOB0cIyOi24h8IH43bqr4EsF8zWQk%2FOABDREl1I4YG6vRf%2BLD3DphjM%3D&Expires=1783890467", "piano": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/piano.wav?AWSAccessKeyId=ASIAXOTDWH636337FQGW&Signature=j9vizBrRLdxALh%2FQWJp3hxOQZPo%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECIaCXVzLWVhc3QtMSJHMEUCIF1y44e48JKBkhSs2uKyp7SOB77W7aRmTbPLL3FPg4f4AiEAoJEn7jgEgQoWef8fpPeFcEOUA19BVOeim4lnDBAXb7MqmQQI6v%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDLVN9cmTfCGFm1NxIirtA7wS0pZ62vsbNpx1OO4oVoa%2F0A4gXgegOTMEU7yD6VHrPrYLmlpoh4X%2F5bvM0HAFwnaKgdwP0Dy7jw6qvxinLnsbIm1m3cdU%2B8xAxim8UW%2B6Gwsa03O3ePJ2IqkSdffgo7OkxekARE0GZqOPxDDAT69uvfoNj1HjLNjVje1hOp4ivU5yfNOqoJSl%2Ba4yfwizEjtCI8QlnCKbND3rme4c9cw%2FB1vssDzeysmrVXXedpHh0uweUDEoGJnKxAW5MtxyPwJXolsGbykSNmfobJSUArf3IJ1PVYN%2F8jO9%2BW2fTIUKOHQ424p3pi0gRZA771L2oK9%2BENzA26a99xiGix5PBpaoueAEK1P%2Bod%2FTxFoDRQzSstShRwx1eoHDvXGbFylYNokdaXZ%2F2%2F1wWB4IecBOciVLG0AxJPXYmMNzXMDHBt2QpYG0lzz%2FYb6HeETvnZ91Kk17Ko5Ao9ul1hQ6EEp0TpM6KtkPY01uCkc9PC%2B7ERxCW4z5fsyGT3%2F%2BkbqVKc7mZg9q%2F5gBvO%2BQIZbHTEGOXDO%2B%2BHr%2BkdGIobMNDYPOPeyzeWhX2f4l5WsC1QO%2FKZvlskA0xIIFwVGihe2Si6DEHTqEMmCBnPP4J5SoWPR32pWeKCFNjfZ46CJw8tUY7tjbMsT4CGgVWxfNS%2FMOIbIw4JPP0gY6ogEPe6xhS3GQ0QBgv8l3DKGf48AZMoZhRBA64F%2ByLhg7jyqyhhdbnystErZzSXneMGnrnIZVQz3Imbc2tp0CodNOC1cvnUzT4qhcwMxLHXNm%2FrnEilAlZB778H2PDSXisLEghsyBO%2By4miHHO%2F%2F8dXdXWasd6YZd8lWqbOB0cIyOi24h8IH43bqr4EsF8zWQk%2FOABDREl1I4YG6vRf%2BLD3DphjM%3D&Expires=1783890467", "guitar": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/guitar.wav?AWSAccessKeyId=ASIAXOTDWH636337FQGW&Signature=ZxhtlNHM1WdVmUkHzY5wVYHca8s%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECIaCXVzLWVhc3QtMSJHMEUCIF1y44e48JKBkhSs2uKyp7SOB77W7aRmTbPLL3FPg4f4AiEAoJEn7jgEgQoWef8fpPeFcEOUA19BVOeim4lnDBAXb7MqmQQI6v%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDLVN9cmTfCGFm1NxIirtA7wS0pZ62vsbNpx1OO4oVoa%2F0A4gXgegOTMEU7yD6VHrPrYLmlpoh4X%2F5bvM0HAFwnaKgdwP0Dy7jw6qvxinLnsbIm1m3cdU%2B8xAxim8UW%2B6Gwsa03O3ePJ2IqkSdffgo7OkxekARE0GZqOPxDDAT69uvfoNj1HjLNjVje1hOp4ivU5yfNOqoJSl%2Ba4yfwizEjtCI8QlnCKbND3rme4c9cw%2FB1vssDzeysmrVXXedpHh0uweUDEoGJnKxAW5MtxyPwJXolsGbykSNmfobJSUArf3IJ1PVYN%2F8jO9%2BW2fTIUKOHQ424p3pi0gRZA771L2oK9%2BENzA26a99xiGix5PBpaoueAEK1P%2Bod%2FTxFoDRQzSstShRwx1eoHDvXGbFylYNokdaXZ%2F2%2F1wWB4IecBOciVLG0AxJPXYmMNzXMDHBt2QpYG0lzz%2FYb6HeETvnZ91Kk17Ko5Ao9ul1hQ6EEp0TpM6KtkPY01uCkc9PC%2B7ERxCW4z5fsyGT3%2F%2BkbqVKc7mZg9q%2F5gBvO%2BQIZbHTEGOXDO%2B%2BHr%2BkdGIobMNDYPOPeyzeWhX2f4l5WsC1QO%2FKZvlskA0xIIFwVGihe2Si6DEHTqEMmCBnPP4J5SoWPR32pWeKCFNjfZ46CJw8tUY7tjbMsT4CGgVWxfNS%2FMOIbIw4JPP0gY6ogEPe6xhS3GQ0QBgv8l3DKGf48AZMoZhRBA64F%2ByLhg7jyqyhhdbnystErZzSXneMGnrnIZVQz3Imbc2tp0CodNOC1cvnUzT4qhcwMxLHXNm%2FrnEilAlZB778H2PDSXisLEghsyBO%2By4miHHO%2F%2F8dXdXWasd6YZd8lWqbOB0cIyOi24h8IH43bqr4EsF8zWQk%2FOABDREl1I4YG6vRf%2BLD3DphjM%3D&Expires=1783890467", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH636337FQGW&Signature=HHfpdq66q6%2BeTIQgySN6Hl4iI9o%3D&x-amz-security-token=IQoJb3JpZ2luX2VjECIaCXVzLWVhc3QtMSJHMEUCIF1y44e48JKBkhSs2uKyp7SOB77W7aRmTbPLL3FPg4f4AiEAoJEn7jgEgQoWef8fpPeFcEOUA19BVOeim4lnDBAXb7MqmQQI6v%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTIzODM5MjYxOTkiDLVN9cmTfCGFm1NxIirtA7wS0pZ62vsbNpx1OO4oVoa%2F0A4gXgegOTMEU7yD6VHrPrYLmlpoh4X%2F5bvM0HAFwnaKgdwP0Dy7jw6qvxinLnsbIm1m3cdU%2B8xAxim8UW%2B6Gwsa03O3ePJ2IqkSdffgo7OkxekARE0GZqOPxDDAT69uvfoNj1HjLNjVje1hOp4ivU5yfNOqoJSl%2Ba4yfwizEjtCI8QlnCKbND3rme4c9cw%2FB1vssDzeysmrVXXedpHh0uweUDEoGJnKxAW5MtxyPwJXolsGbykSNmfobJSUArf3IJ1PVYN%2F8jO9%2BW2fTIUKOHQ424p3pi0gRZA771L2oK9%2BENzA26a99xiGix5PBpaoueAEK1P%2Bod%2FTxFoDRQzSstShRwx1eoHDvXGbFylYNokdaXZ%2F2%2F1wWB4IecBOciVLG0AxJPXYmMNzXMDHBt2QpYG0lzz%2FYb6HeETvnZ91Kk17Ko5Ao9ul1hQ6EEp0TpM6KtkPY01uCkc9PC%2B7ERxCW4z5fsyGT3%2F%2BkbqVKc7mZg9q%2F5gBvO%2BQIZbHTEGOXDO%2B%2BHr%2BkdGIobMNDYPOPeyzeWhX2f4l5WsC1QO%2FKZvlskA0xIIFwVGihe2Si6DEHTqEMmCBnPP4J5SoWPR32pWeKCFNjfZ46CJw8tUY7tjbMsT4CGgVWxfNS%2FMOIbIw4JPP0gY6ogEPe6xhS3GQ0QBgv8l3DKGf48AZMoZhRBA64F%2ByLhg7jyqyhhdbnystErZzSXneMGnrnIZVQz3Imbc2tp0CodNOC1cvnUzT4qhcwMxLHXNm%2FrnEilAlZB778H2PDSXisLEghsyBO%2By4miHHO%2F%2F8dXdXWasd6YZd8lWqbOB0cIyOi24h8IH43bqr4EsF8zWQk%2FOABDREl1I4YG6vRf%2BLD3DphjM%3D&Expires=1783890467"};
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
