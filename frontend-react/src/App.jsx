import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH632PFOM52L&Signature=ggdgtERIf%2F%2BNVRq7uFrXL1aB%2FTY%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEJP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJHMEUCIQCSFE3VyufMro9%2Fgh0TGTBkoy38m5WYwufMSPZcEksJGgIgfsrFw5GRP2y7nKnYt33KgKXM2Br1XQLMEAfQKfA9fUIqkAQIXBAAGgw1MTIzODM5MjYxOTkiDL%2BU9psVQGMKQqG4%2BSrtAx6%2B7f7R2HkRNgVuvMaqNSxpxhnSmS66fcr8BxX%2F4Uu4ofytd9dbUbEHFzFDZJmtg40%2FPTVZmRuqPv%2B1%2BDYywYaZ4BYdK8V0Y4doVy%2FgI6lf%2BgKtScgrxZlFJK1cmotC9xb3bYu0IF%2FQVuMoIeLz66uWhgx6X2jmMaBfQw%2BszscvcHeXWvk4bgNfaWdlOORgpWu%2FAE3s09LSg75sOZi3XOk6xEAJFfUeHZLsYGhCVVch9L%2FPK9YgNQ%2BCFXzypwEgvYNLZDoy2tFER%2BQPtP4FOD6J4%2BgFsEaaIm45zhnXkO9EI%2FtvfhaKeVw4swL2JSWPzmsFvVkXdSb6K386WNWq%2FeRICQI%2FGnSE4vFY%2FuHuWFwtAO7JWCtJOFpwDR%2BwMLptBlIMunthrz6UHj8kK3D7h9Da7p93K83ZxuMEif6rBh7FmsVIaKC2TgBbJkcC%2BLEof%2BlZd12uL%2B7Y0Kp5m5zYNGMebfeptUn%2Bs8npyvBLqLqpXyrpo9u8pVlSnGAJJ%2BHvzBG6uK%2FrHZ4CRY%2BDwT2hjlgQJR4cVT6NUtdMU40yUw6ptLZK7Sx0obPGWBhTJuFpYySu0hJ2Q%2FrRTxwbbASthK12xP5vpYk7zXodwXzMUwwZj0tkOagAfP7YRQckmQsAmkK6YnygZL7L8INLMhww6%2B6v0gY6ogFtWBlPqvMtt5QoaDqPfLFnT0GDzoppaT6a1v17bJWc7h5j2upoDZ2qQSyexMOEJWdYMhm%2BVR4iwbm5C0b9%2Bws4lpvSejoPVnLtdABGOMCdDuBPTBEG6ZhrmsHMQPGeerVgvXpe2japmp97hp0bJy%2BoS%2BaGAnRnJbioLmHBVYCjRXqF40Z6mAhoJ72DDe%2FU%2F183Ex7Btgcj8Y1yaLa3nLJQY6s%3D&Expires=1783377838", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH632PFOM52L&Signature=ohmV6ci14Rr5XZrmBEwyct4V7ik%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEJP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJHMEUCIQCSFE3VyufMro9%2Fgh0TGTBkoy38m5WYwufMSPZcEksJGgIgfsrFw5GRP2y7nKnYt33KgKXM2Br1XQLMEAfQKfA9fUIqkAQIXBAAGgw1MTIzODM5MjYxOTkiDL%2BU9psVQGMKQqG4%2BSrtAx6%2B7f7R2HkRNgVuvMaqNSxpxhnSmS66fcr8BxX%2F4Uu4ofytd9dbUbEHFzFDZJmtg40%2FPTVZmRuqPv%2B1%2BDYywYaZ4BYdK8V0Y4doVy%2FgI6lf%2BgKtScgrxZlFJK1cmotC9xb3bYu0IF%2FQVuMoIeLz66uWhgx6X2jmMaBfQw%2BszscvcHeXWvk4bgNfaWdlOORgpWu%2FAE3s09LSg75sOZi3XOk6xEAJFfUeHZLsYGhCVVch9L%2FPK9YgNQ%2BCFXzypwEgvYNLZDoy2tFER%2BQPtP4FOD6J4%2BgFsEaaIm45zhnXkO9EI%2FtvfhaKeVw4swL2JSWPzmsFvVkXdSb6K386WNWq%2FeRICQI%2FGnSE4vFY%2FuHuWFwtAO7JWCtJOFpwDR%2BwMLptBlIMunthrz6UHj8kK3D7h9Da7p93K83ZxuMEif6rBh7FmsVIaKC2TgBbJkcC%2BLEof%2BlZd12uL%2B7Y0Kp5m5zYNGMebfeptUn%2Bs8npyvBLqLqpXyrpo9u8pVlSnGAJJ%2BHvzBG6uK%2FrHZ4CRY%2BDwT2hjlgQJR4cVT6NUtdMU40yUw6ptLZK7Sx0obPGWBhTJuFpYySu0hJ2Q%2FrRTxwbbASthK12xP5vpYk7zXodwXzMUwwZj0tkOagAfP7YRQckmQsAmkK6YnygZL7L8INLMhww6%2B6v0gY6ogFtWBlPqvMtt5QoaDqPfLFnT0GDzoppaT6a1v17bJWc7h5j2upoDZ2qQSyexMOEJWdYMhm%2BVR4iwbm5C0b9%2Bws4lpvSejoPVnLtdABGOMCdDuBPTBEG6ZhrmsHMQPGeerVgvXpe2japmp97hp0bJy%2BoS%2BaGAnRnJbioLmHBVYCjRXqF40Z6mAhoJ72DDe%2FU%2F183Ex7Btgcj8Y1yaLa3nLJQY6s%3D&Expires=1783377838", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH632PFOM52L&Signature=qqaADvLsegOf7eqgpKyuri3%2Bpo4%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEJP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJHMEUCIQCSFE3VyufMro9%2Fgh0TGTBkoy38m5WYwufMSPZcEksJGgIgfsrFw5GRP2y7nKnYt33KgKXM2Br1XQLMEAfQKfA9fUIqkAQIXBAAGgw1MTIzODM5MjYxOTkiDL%2BU9psVQGMKQqG4%2BSrtAx6%2B7f7R2HkRNgVuvMaqNSxpxhnSmS66fcr8BxX%2F4Uu4ofytd9dbUbEHFzFDZJmtg40%2FPTVZmRuqPv%2B1%2BDYywYaZ4BYdK8V0Y4doVy%2FgI6lf%2BgKtScgrxZlFJK1cmotC9xb3bYu0IF%2FQVuMoIeLz66uWhgx6X2jmMaBfQw%2BszscvcHeXWvk4bgNfaWdlOORgpWu%2FAE3s09LSg75sOZi3XOk6xEAJFfUeHZLsYGhCVVch9L%2FPK9YgNQ%2BCFXzypwEgvYNLZDoy2tFER%2BQPtP4FOD6J4%2BgFsEaaIm45zhnXkO9EI%2FtvfhaKeVw4swL2JSWPzmsFvVkXdSb6K386WNWq%2FeRICQI%2FGnSE4vFY%2FuHuWFwtAO7JWCtJOFpwDR%2BwMLptBlIMunthrz6UHj8kK3D7h9Da7p93K83ZxuMEif6rBh7FmsVIaKC2TgBbJkcC%2BLEof%2BlZd12uL%2B7Y0Kp5m5zYNGMebfeptUn%2Bs8npyvBLqLqpXyrpo9u8pVlSnGAJJ%2BHvzBG6uK%2FrHZ4CRY%2BDwT2hjlgQJR4cVT6NUtdMU40yUw6ptLZK7Sx0obPGWBhTJuFpYySu0hJ2Q%2FrRTxwbbASthK12xP5vpYk7zXodwXzMUwwZj0tkOagAfP7YRQckmQsAmkK6YnygZL7L8INLMhww6%2B6v0gY6ogFtWBlPqvMtt5QoaDqPfLFnT0GDzoppaT6a1v17bJWc7h5j2upoDZ2qQSyexMOEJWdYMhm%2BVR4iwbm5C0b9%2Bws4lpvSejoPVnLtdABGOMCdDuBPTBEG6ZhrmsHMQPGeerVgvXpe2japmp97hp0bJy%2BoS%2BaGAnRnJbioLmHBVYCjRXqF40Z6mAhoJ72DDe%2FU%2F183Ex7Btgcj8Y1yaLa3nLJQY6s%3D&Expires=1783377838", "piano": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/piano.wav?AWSAccessKeyId=ASIAXOTDWH632PFOM52L&Signature=pL3utNO5QbxU0CsC4R9sVT7FF04%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEJP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJHMEUCIQCSFE3VyufMro9%2Fgh0TGTBkoy38m5WYwufMSPZcEksJGgIgfsrFw5GRP2y7nKnYt33KgKXM2Br1XQLMEAfQKfA9fUIqkAQIXBAAGgw1MTIzODM5MjYxOTkiDL%2BU9psVQGMKQqG4%2BSrtAx6%2B7f7R2HkRNgVuvMaqNSxpxhnSmS66fcr8BxX%2F4Uu4ofytd9dbUbEHFzFDZJmtg40%2FPTVZmRuqPv%2B1%2BDYywYaZ4BYdK8V0Y4doVy%2FgI6lf%2BgKtScgrxZlFJK1cmotC9xb3bYu0IF%2FQVuMoIeLz66uWhgx6X2jmMaBfQw%2BszscvcHeXWvk4bgNfaWdlOORgpWu%2FAE3s09LSg75sOZi3XOk6xEAJFfUeHZLsYGhCVVch9L%2FPK9YgNQ%2BCFXzypwEgvYNLZDoy2tFER%2BQPtP4FOD6J4%2BgFsEaaIm45zhnXkO9EI%2FtvfhaKeVw4swL2JSWPzmsFvVkXdSb6K386WNWq%2FeRICQI%2FGnSE4vFY%2FuHuWFwtAO7JWCtJOFpwDR%2BwMLptBlIMunthrz6UHj8kK3D7h9Da7p93K83ZxuMEif6rBh7FmsVIaKC2TgBbJkcC%2BLEof%2BlZd12uL%2B7Y0Kp5m5zYNGMebfeptUn%2Bs8npyvBLqLqpXyrpo9u8pVlSnGAJJ%2BHvzBG6uK%2FrHZ4CRY%2BDwT2hjlgQJR4cVT6NUtdMU40yUw6ptLZK7Sx0obPGWBhTJuFpYySu0hJ2Q%2FrRTxwbbASthK12xP5vpYk7zXodwXzMUwwZj0tkOagAfP7YRQckmQsAmkK6YnygZL7L8INLMhww6%2B6v0gY6ogFtWBlPqvMtt5QoaDqPfLFnT0GDzoppaT6a1v17bJWc7h5j2upoDZ2qQSyexMOEJWdYMhm%2BVR4iwbm5C0b9%2Bws4lpvSejoPVnLtdABGOMCdDuBPTBEG6ZhrmsHMQPGeerVgvXpe2japmp97hp0bJy%2BoS%2BaGAnRnJbioLmHBVYCjRXqF40Z6mAhoJ72DDe%2FU%2F183Ex7Btgcj8Y1yaLa3nLJQY6s%3D&Expires=1783377838", "guitar": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/guitar.wav?AWSAccessKeyId=ASIAXOTDWH632PFOM52L&Signature=uu0p5NwI7%2BSxEoWFLEVMMMG230I%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEJP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJHMEUCIQCSFE3VyufMro9%2Fgh0TGTBkoy38m5WYwufMSPZcEksJGgIgfsrFw5GRP2y7nKnYt33KgKXM2Br1XQLMEAfQKfA9fUIqkAQIXBAAGgw1MTIzODM5MjYxOTkiDL%2BU9psVQGMKQqG4%2BSrtAx6%2B7f7R2HkRNgVuvMaqNSxpxhnSmS66fcr8BxX%2F4Uu4ofytd9dbUbEHFzFDZJmtg40%2FPTVZmRuqPv%2B1%2BDYywYaZ4BYdK8V0Y4doVy%2FgI6lf%2BgKtScgrxZlFJK1cmotC9xb3bYu0IF%2FQVuMoIeLz66uWhgx6X2jmMaBfQw%2BszscvcHeXWvk4bgNfaWdlOORgpWu%2FAE3s09LSg75sOZi3XOk6xEAJFfUeHZLsYGhCVVch9L%2FPK9YgNQ%2BCFXzypwEgvYNLZDoy2tFER%2BQPtP4FOD6J4%2BgFsEaaIm45zhnXkO9EI%2FtvfhaKeVw4swL2JSWPzmsFvVkXdSb6K386WNWq%2FeRICQI%2FGnSE4vFY%2FuHuWFwtAO7JWCtJOFpwDR%2BwMLptBlIMunthrz6UHj8kK3D7h9Da7p93K83ZxuMEif6rBh7FmsVIaKC2TgBbJkcC%2BLEof%2BlZd12uL%2B7Y0Kp5m5zYNGMebfeptUn%2Bs8npyvBLqLqpXyrpo9u8pVlSnGAJJ%2BHvzBG6uK%2FrHZ4CRY%2BDwT2hjlgQJR4cVT6NUtdMU40yUw6ptLZK7Sx0obPGWBhTJuFpYySu0hJ2Q%2FrRTxwbbASthK12xP5vpYk7zXodwXzMUwwZj0tkOagAfP7YRQckmQsAmkK6YnygZL7L8INLMhww6%2B6v0gY6ogFtWBlPqvMtt5QoaDqPfLFnT0GDzoppaT6a1v17bJWc7h5j2upoDZ2qQSyexMOEJWdYMhm%2BVR4iwbm5C0b9%2Bws4lpvSejoPVnLtdABGOMCdDuBPTBEG6ZhrmsHMQPGeerVgvXpe2japmp97hp0bJy%2BoS%2BaGAnRnJbioLmHBVYCjRXqF40Z6mAhoJ72DDe%2FU%2F183Ex7Btgcj8Y1yaLa3nLJQY6s%3D&Expires=1783377838", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH632PFOM52L&Signature=KmoLZvyLJ2sGojNyKUu7F4K81V8%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEJP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJHMEUCIQCSFE3VyufMro9%2Fgh0TGTBkoy38m5WYwufMSPZcEksJGgIgfsrFw5GRP2y7nKnYt33KgKXM2Br1XQLMEAfQKfA9fUIqkAQIXBAAGgw1MTIzODM5MjYxOTkiDL%2BU9psVQGMKQqG4%2BSrtAx6%2B7f7R2HkRNgVuvMaqNSxpxhnSmS66fcr8BxX%2F4Uu4ofytd9dbUbEHFzFDZJmtg40%2FPTVZmRuqPv%2B1%2BDYywYaZ4BYdK8V0Y4doVy%2FgI6lf%2BgKtScgrxZlFJK1cmotC9xb3bYu0IF%2FQVuMoIeLz66uWhgx6X2jmMaBfQw%2BszscvcHeXWvk4bgNfaWdlOORgpWu%2FAE3s09LSg75sOZi3XOk6xEAJFfUeHZLsYGhCVVch9L%2FPK9YgNQ%2BCFXzypwEgvYNLZDoy2tFER%2BQPtP4FOD6J4%2BgFsEaaIm45zhnXkO9EI%2FtvfhaKeVw4swL2JSWPzmsFvVkXdSb6K386WNWq%2FeRICQI%2FGnSE4vFY%2FuHuWFwtAO7JWCtJOFpwDR%2BwMLptBlIMunthrz6UHj8kK3D7h9Da7p93K83ZxuMEif6rBh7FmsVIaKC2TgBbJkcC%2BLEof%2BlZd12uL%2B7Y0Kp5m5zYNGMebfeptUn%2Bs8npyvBLqLqpXyrpo9u8pVlSnGAJJ%2BHvzBG6uK%2FrHZ4CRY%2BDwT2hjlgQJR4cVT6NUtdMU40yUw6ptLZK7Sx0obPGWBhTJuFpYySu0hJ2Q%2FrRTxwbbASthK12xP5vpYk7zXodwXzMUwwZj0tkOagAfP7YRQckmQsAmkK6YnygZL7L8INLMhww6%2B6v0gY6ogFtWBlPqvMtt5QoaDqPfLFnT0GDzoppaT6a1v17bJWc7h5j2upoDZ2qQSyexMOEJWdYMhm%2BVR4iwbm5C0b9%2Bws4lpvSejoPVnLtdABGOMCdDuBPTBEG6ZhrmsHMQPGeerVgvXpe2japmp97hp0bJy%2BoS%2BaGAnRnJbioLmHBVYCjRXqF40Z6mAhoJ72DDe%2FU%2F183Ex7Btgcj8Y1yaLa3nLJQY6s%3D&Expires=1783377838"};
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
