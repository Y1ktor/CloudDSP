import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH633VBWNZ6N&Signature=8WdF6d%2B3I3RivEbbjhFUj97AzH0%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJGMEQCIDPiawkjSE1v9WE2c%2BNHkmtjnIXXnRXvDaw5%2FBZa%2B9JUAiB87pGRgpx8PbQFylpb1fgNWsA4cnaqzmhX6w2DZOA6eSqQBAgcEAAaDDUxMjM4MzkyNjE5OSIMorsvN3GhieWYyAP3Ku0Dq9Y2kyIFlj4WG6b%2FRQpzMXlo%2BXWedeMtRf%2BjguMYh7AYJvjrxOzJa9%2BriYoQiTdmOozX%2FpzLxG6Np%2BeGHWiBtQa%2FisyA8JwVeI2%2FB6ksixF%2F7LgBdUPSW5rFyPetnDvj%2FLMnyLRYnWueq2eIfcJWUpGJe21zTiekAP6GdrAB%2FYZO2ztzFE962d6jsYnhuF%2Fk6sCBZouzW1gtls4MdXBACwQuVi6AauAk8ScwWODq8iK0ZIp%2FJvcTt02wqQos6l%2Fqx%2BIl6e7hpTJPFgiXUvLTiC5QCMWEOVFtUEb%2F6XQ6ktjo0kqux9zYW8WyGkNZlMvKXeMo3bC1ZjeT1lIGY78u9M3IqQd3l4XS9C9zhaQb9Avinowe6axpRhUenxwTWzxiXUJdHN1RdkR4PPdekHgLg14nSEBN8V3ElAxNPQMYysKZOprMwRimiRsly5FCIGykrajMQAKFd%2Fia7D3BtHP0834r%2BcYkCWUskmqR5w%2FndJQUGxisE%2FPl8smBYlCUNMdt9NXjGeJSwoW7wD06kvOGIuIYTdunUJJk1j6UJt8bwZMaRe%2FmGZ7zCPKMyFkYcnxngjiEftDw2rx3tTQealKwEd8N7vFy%2BMj8Hb6Vqzb%2F0KRgJRrc%2BNzbdoSsXjztXv39K0W08C3aXvN5OK%2BY5DCoitrSBjqjAciJY9uann4GjXbxq%2BdwiuZly8vMh5R9abtjbQsx5Itc95Hle7S1DlBPBiQNeMj4d0p2G3dFiS2rKFgPI03bSjTiVpUGlm1wSUTI4Q18Nle%2Fe9%2FMHvUoyXN49XsPc2ow9dTmGYHvlPAf5Lj18eq4QBDnjj53M95VOpCHhoZ7OBltZIBz3QtHzDfDVRNTJoEkBqkcQfg3uX4PYBIMwcjoNthWmfM%3D&Expires=1784069483", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH633VBWNZ6N&Signature=XWJS%2Bf7D2%2BLEMepSj%2B2kWKmdFZ0%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJGMEQCIDPiawkjSE1v9WE2c%2BNHkmtjnIXXnRXvDaw5%2FBZa%2B9JUAiB87pGRgpx8PbQFylpb1fgNWsA4cnaqzmhX6w2DZOA6eSqQBAgcEAAaDDUxMjM4MzkyNjE5OSIMorsvN3GhieWYyAP3Ku0Dq9Y2kyIFlj4WG6b%2FRQpzMXlo%2BXWedeMtRf%2BjguMYh7AYJvjrxOzJa9%2BriYoQiTdmOozX%2FpzLxG6Np%2BeGHWiBtQa%2FisyA8JwVeI2%2FB6ksixF%2F7LgBdUPSW5rFyPetnDvj%2FLMnyLRYnWueq2eIfcJWUpGJe21zTiekAP6GdrAB%2FYZO2ztzFE962d6jsYnhuF%2Fk6sCBZouzW1gtls4MdXBACwQuVi6AauAk8ScwWODq8iK0ZIp%2FJvcTt02wqQos6l%2Fqx%2BIl6e7hpTJPFgiXUvLTiC5QCMWEOVFtUEb%2F6XQ6ktjo0kqux9zYW8WyGkNZlMvKXeMo3bC1ZjeT1lIGY78u9M3IqQd3l4XS9C9zhaQb9Avinowe6axpRhUenxwTWzxiXUJdHN1RdkR4PPdekHgLg14nSEBN8V3ElAxNPQMYysKZOprMwRimiRsly5FCIGykrajMQAKFd%2Fia7D3BtHP0834r%2BcYkCWUskmqR5w%2FndJQUGxisE%2FPl8smBYlCUNMdt9NXjGeJSwoW7wD06kvOGIuIYTdunUJJk1j6UJt8bwZMaRe%2FmGZ7zCPKMyFkYcnxngjiEftDw2rx3tTQealKwEd8N7vFy%2BMj8Hb6Vqzb%2F0KRgJRrc%2BNzbdoSsXjztXv39K0W08C3aXvN5OK%2BY5DCoitrSBjqjAciJY9uann4GjXbxq%2BdwiuZly8vMh5R9abtjbQsx5Itc95Hle7S1DlBPBiQNeMj4d0p2G3dFiS2rKFgPI03bSjTiVpUGlm1wSUTI4Q18Nle%2Fe9%2FMHvUoyXN49XsPc2ow9dTmGYHvlPAf5Lj18eq4QBDnjj53M95VOpCHhoZ7OBltZIBz3QtHzDfDVRNTJoEkBqkcQfg3uX4PYBIMwcjoNthWmfM%3D&Expires=1784069483", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH633VBWNZ6N&Signature=KE6y6H65%2BsYh6mEfwaDAq8DGUew%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJGMEQCIDPiawkjSE1v9WE2c%2BNHkmtjnIXXnRXvDaw5%2FBZa%2B9JUAiB87pGRgpx8PbQFylpb1fgNWsA4cnaqzmhX6w2DZOA6eSqQBAgcEAAaDDUxMjM4MzkyNjE5OSIMorsvN3GhieWYyAP3Ku0Dq9Y2kyIFlj4WG6b%2FRQpzMXlo%2BXWedeMtRf%2BjguMYh7AYJvjrxOzJa9%2BriYoQiTdmOozX%2FpzLxG6Np%2BeGHWiBtQa%2FisyA8JwVeI2%2FB6ksixF%2F7LgBdUPSW5rFyPetnDvj%2FLMnyLRYnWueq2eIfcJWUpGJe21zTiekAP6GdrAB%2FYZO2ztzFE962d6jsYnhuF%2Fk6sCBZouzW1gtls4MdXBACwQuVi6AauAk8ScwWODq8iK0ZIp%2FJvcTt02wqQos6l%2Fqx%2BIl6e7hpTJPFgiXUvLTiC5QCMWEOVFtUEb%2F6XQ6ktjo0kqux9zYW8WyGkNZlMvKXeMo3bC1ZjeT1lIGY78u9M3IqQd3l4XS9C9zhaQb9Avinowe6axpRhUenxwTWzxiXUJdHN1RdkR4PPdekHgLg14nSEBN8V3ElAxNPQMYysKZOprMwRimiRsly5FCIGykrajMQAKFd%2Fia7D3BtHP0834r%2BcYkCWUskmqR5w%2FndJQUGxisE%2FPl8smBYlCUNMdt9NXjGeJSwoW7wD06kvOGIuIYTdunUJJk1j6UJt8bwZMaRe%2FmGZ7zCPKMyFkYcnxngjiEftDw2rx3tTQealKwEd8N7vFy%2BMj8Hb6Vqzb%2F0KRgJRrc%2BNzbdoSsXjztXv39K0W08C3aXvN5OK%2BY5DCoitrSBjqjAciJY9uann4GjXbxq%2BdwiuZly8vMh5R9abtjbQsx5Itc95Hle7S1DlBPBiQNeMj4d0p2G3dFiS2rKFgPI03bSjTiVpUGlm1wSUTI4Q18Nle%2Fe9%2FMHvUoyXN49XsPc2ow9dTmGYHvlPAf5Lj18eq4QBDnjj53M95VOpCHhoZ7OBltZIBz3QtHzDfDVRNTJoEkBqkcQfg3uX4PYBIMwcjoNthWmfM%3D&Expires=1784069483", "piano": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/piano.wav?AWSAccessKeyId=ASIAXOTDWH633VBWNZ6N&Signature=KM4qAoD6eq0RFj5xAEgOjawcgLg%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJGMEQCIDPiawkjSE1v9WE2c%2BNHkmtjnIXXnRXvDaw5%2FBZa%2B9JUAiB87pGRgpx8PbQFylpb1fgNWsA4cnaqzmhX6w2DZOA6eSqQBAgcEAAaDDUxMjM4MzkyNjE5OSIMorsvN3GhieWYyAP3Ku0Dq9Y2kyIFlj4WG6b%2FRQpzMXlo%2BXWedeMtRf%2BjguMYh7AYJvjrxOzJa9%2BriYoQiTdmOozX%2FpzLxG6Np%2BeGHWiBtQa%2FisyA8JwVeI2%2FB6ksixF%2F7LgBdUPSW5rFyPetnDvj%2FLMnyLRYnWueq2eIfcJWUpGJe21zTiekAP6GdrAB%2FYZO2ztzFE962d6jsYnhuF%2Fk6sCBZouzW1gtls4MdXBACwQuVi6AauAk8ScwWODq8iK0ZIp%2FJvcTt02wqQos6l%2Fqx%2BIl6e7hpTJPFgiXUvLTiC5QCMWEOVFtUEb%2F6XQ6ktjo0kqux9zYW8WyGkNZlMvKXeMo3bC1ZjeT1lIGY78u9M3IqQd3l4XS9C9zhaQb9Avinowe6axpRhUenxwTWzxiXUJdHN1RdkR4PPdekHgLg14nSEBN8V3ElAxNPQMYysKZOprMwRimiRsly5FCIGykrajMQAKFd%2Fia7D3BtHP0834r%2BcYkCWUskmqR5w%2FndJQUGxisE%2FPl8smBYlCUNMdt9NXjGeJSwoW7wD06kvOGIuIYTdunUJJk1j6UJt8bwZMaRe%2FmGZ7zCPKMyFkYcnxngjiEftDw2rx3tTQealKwEd8N7vFy%2BMj8Hb6Vqzb%2F0KRgJRrc%2BNzbdoSsXjztXv39K0W08C3aXvN5OK%2BY5DCoitrSBjqjAciJY9uann4GjXbxq%2BdwiuZly8vMh5R9abtjbQsx5Itc95Hle7S1DlBPBiQNeMj4d0p2G3dFiS2rKFgPI03bSjTiVpUGlm1wSUTI4Q18Nle%2Fe9%2FMHvUoyXN49XsPc2ow9dTmGYHvlPAf5Lj18eq4QBDnjj53M95VOpCHhoZ7OBltZIBz3QtHzDfDVRNTJoEkBqkcQfg3uX4PYBIMwcjoNthWmfM%3D&Expires=1784069483", "guitar": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/guitar.wav?AWSAccessKeyId=ASIAXOTDWH633VBWNZ6N&Signature=HXwunUvaxfMfaGc3hDo6z4VSrco%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJGMEQCIDPiawkjSE1v9WE2c%2BNHkmtjnIXXnRXvDaw5%2FBZa%2B9JUAiB87pGRgpx8PbQFylpb1fgNWsA4cnaqzmhX6w2DZOA6eSqQBAgcEAAaDDUxMjM4MzkyNjE5OSIMorsvN3GhieWYyAP3Ku0Dq9Y2kyIFlj4WG6b%2FRQpzMXlo%2BXWedeMtRf%2BjguMYh7AYJvjrxOzJa9%2BriYoQiTdmOozX%2FpzLxG6Np%2BeGHWiBtQa%2FisyA8JwVeI2%2FB6ksixF%2F7LgBdUPSW5rFyPetnDvj%2FLMnyLRYnWueq2eIfcJWUpGJe21zTiekAP6GdrAB%2FYZO2ztzFE962d6jsYnhuF%2Fk6sCBZouzW1gtls4MdXBACwQuVi6AauAk8ScwWODq8iK0ZIp%2FJvcTt02wqQos6l%2Fqx%2BIl6e7hpTJPFgiXUvLTiC5QCMWEOVFtUEb%2F6XQ6ktjo0kqux9zYW8WyGkNZlMvKXeMo3bC1ZjeT1lIGY78u9M3IqQd3l4XS9C9zhaQb9Avinowe6axpRhUenxwTWzxiXUJdHN1RdkR4PPdekHgLg14nSEBN8V3ElAxNPQMYysKZOprMwRimiRsly5FCIGykrajMQAKFd%2Fia7D3BtHP0834r%2BcYkCWUskmqR5w%2FndJQUGxisE%2FPl8smBYlCUNMdt9NXjGeJSwoW7wD06kvOGIuIYTdunUJJk1j6UJt8bwZMaRe%2FmGZ7zCPKMyFkYcnxngjiEftDw2rx3tTQealKwEd8N7vFy%2BMj8Hb6Vqzb%2F0KRgJRrc%2BNzbdoSsXjztXv39K0W08C3aXvN5OK%2BY5DCoitrSBjqjAciJY9uann4GjXbxq%2BdwiuZly8vMh5R9abtjbQsx5Itc95Hle7S1DlBPBiQNeMj4d0p2G3dFiS2rKFgPI03bSjTiVpUGlm1wSUTI4Q18Nle%2Fe9%2FMHvUoyXN49XsPc2ow9dTmGYHvlPAf5Lj18eq4QBDnjj53M95VOpCHhoZ7OBltZIBz3QtHzDfDVRNTJoEkBqkcQfg3uX4PYBIMwcjoNthWmfM%3D&Expires=1784069483", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH633VBWNZ6N&Signature=LEJrt5xv%2FENb9sdYjTHptDTuvp0%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJGMEQCIDPiawkjSE1v9WE2c%2BNHkmtjnIXXnRXvDaw5%2FBZa%2B9JUAiB87pGRgpx8PbQFylpb1fgNWsA4cnaqzmhX6w2DZOA6eSqQBAgcEAAaDDUxMjM4MzkyNjE5OSIMorsvN3GhieWYyAP3Ku0Dq9Y2kyIFlj4WG6b%2FRQpzMXlo%2BXWedeMtRf%2BjguMYh7AYJvjrxOzJa9%2BriYoQiTdmOozX%2FpzLxG6Np%2BeGHWiBtQa%2FisyA8JwVeI2%2FB6ksixF%2F7LgBdUPSW5rFyPetnDvj%2FLMnyLRYnWueq2eIfcJWUpGJe21zTiekAP6GdrAB%2FYZO2ztzFE962d6jsYnhuF%2Fk6sCBZouzW1gtls4MdXBACwQuVi6AauAk8ScwWODq8iK0ZIp%2FJvcTt02wqQos6l%2Fqx%2BIl6e7hpTJPFgiXUvLTiC5QCMWEOVFtUEb%2F6XQ6ktjo0kqux9zYW8WyGkNZlMvKXeMo3bC1ZjeT1lIGY78u9M3IqQd3l4XS9C9zhaQb9Avinowe6axpRhUenxwTWzxiXUJdHN1RdkR4PPdekHgLg14nSEBN8V3ElAxNPQMYysKZOprMwRimiRsly5FCIGykrajMQAKFd%2Fia7D3BtHP0834r%2BcYkCWUskmqR5w%2FndJQUGxisE%2FPl8smBYlCUNMdt9NXjGeJSwoW7wD06kvOGIuIYTdunUJJk1j6UJt8bwZMaRe%2FmGZ7zCPKMyFkYcnxngjiEftDw2rx3tTQealKwEd8N7vFy%2BMj8Hb6Vqzb%2F0KRgJRrc%2BNzbdoSsXjztXv39K0W08C3aXvN5OK%2BY5DCoitrSBjqjAciJY9uann4GjXbxq%2BdwiuZly8vMh5R9abtjbQsx5Itc95Hle7S1DlBPBiQNeMj4d0p2G3dFiS2rKFgPI03bSjTiVpUGlm1wSUTI4Q18Nle%2Fe9%2FMHvUoyXN49XsPc2ow9dTmGYHvlPAf5Lj18eq4QBDnjj53M95VOpCHhoZ7OBltZIBz3QtHzDfDVRNTJoEkBqkcQfg3uX4PYBIMwcjoNthWmfM%3D&Expires=1784069483"};
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
