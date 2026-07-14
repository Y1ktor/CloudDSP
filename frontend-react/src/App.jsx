import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH6342ZW4PS5&Signature=v1hSWrSMpoIowW4jcg7u93LjPAE%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEIaCXVzLWVhc3QtMSJHMEUCIQD480Wxiaub4vtRUMXy3%2F%2FtFaJ%2B%2BdT%2FDWU9GVX8dhvp9QIgEO3qrcLnjokm7%2F4WbYHAKaal7X4FjJ1Sqsze4IlPrEgqkAQIChAAGgw1MTIzODM5MjYxOTkiDP7EfurmS%2FnPdqCDCirtAwtrqJW5c%2FWjLKp4S28CITbNPtG8uK3cuFowIzjrP7k7%2BIeODXp35REsYOhPYg8W5igLzHD3ACe5%2FGKb2O1Xxb0tY7DDfkv85e01K9rj0qPQV48lFpUJpW9MrLOaRQXWcjcal7OY4UEFpLnP%2F5NcMehvVlc48g2xMgnOrCu8Svjzra6NSakShu2wya2scPDI1zGIS%2FOhFi8BlHl15w9pFuZwEyF5ZtNdj97LWKSZQmgkQtr0Ta4c2SUAIlMgy1F0A7E6apfcp3iRQV%2FB6dEMTo4eH6I%2B3NxjJHdVDEMoomP4gIE7vyZmLQaEIQIWhtai%2B0PapKIX%2B%2BVbW68dHDGweTHM6EAa%2BS3NVnF8PDtEX2JTWBA52BPVdCfQxs2ivYB3mvfd8PbqxeeleKqasZFcOHlz0sh4D0yCFMmgeiiiIItD6jOEBDVdWrOFweO0p0t2RrcefNIVYFPFkn5BmJaN7QOWE%2Bi76%2F6EikKuWvnrkPzdsNODsbCPlRPfKRAU%2BzPo5ADc5xhZmAZcRsxWEdW%2BksGso7oo6CM01XeJhUbbxdWVauUA8tJQu5roKjqtzJj2gWrc3SjQjOJ7K3whUrknztxKrQV4vx7o4c%2B35xOfr27SPkAEG%2BSHMXZQ%2FLxrKzLkXw%2FOAewnGM%2B3aOLFHX8whZvW0gY6ogH5v6bD6aII%2F1TNwLA%2FK%2BnPfxf9J02YN7JoBj1edvX8%2BaD%2FljT4QEZ3JG9a%2BFN4zzfNKUpVU2QjXgem9kvEoJ7OcF6mnqBXGZ5H4z%2FEYZoP377EU6j%2Flprwuz4MDkQEgRdtcjOaJI1YiXqOMhXhl8el3tBDoJPrfc%2BKZ%2BHgJJGLAvNDzekX3oBBl3U0x87hGb1KSLH13Dowc3ZR69Wm73UHjss%3D&Expires=1784006088", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH6342ZW4PS5&Signature=%2F7N1l9a5K2w9HRnk88mW1jCPhis%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEIaCXVzLWVhc3QtMSJHMEUCIQD480Wxiaub4vtRUMXy3%2F%2FtFaJ%2B%2BdT%2FDWU9GVX8dhvp9QIgEO3qrcLnjokm7%2F4WbYHAKaal7X4FjJ1Sqsze4IlPrEgqkAQIChAAGgw1MTIzODM5MjYxOTkiDP7EfurmS%2FnPdqCDCirtAwtrqJW5c%2FWjLKp4S28CITbNPtG8uK3cuFowIzjrP7k7%2BIeODXp35REsYOhPYg8W5igLzHD3ACe5%2FGKb2O1Xxb0tY7DDfkv85e01K9rj0qPQV48lFpUJpW9MrLOaRQXWcjcal7OY4UEFpLnP%2F5NcMehvVlc48g2xMgnOrCu8Svjzra6NSakShu2wya2scPDI1zGIS%2FOhFi8BlHl15w9pFuZwEyF5ZtNdj97LWKSZQmgkQtr0Ta4c2SUAIlMgy1F0A7E6apfcp3iRQV%2FB6dEMTo4eH6I%2B3NxjJHdVDEMoomP4gIE7vyZmLQaEIQIWhtai%2B0PapKIX%2B%2BVbW68dHDGweTHM6EAa%2BS3NVnF8PDtEX2JTWBA52BPVdCfQxs2ivYB3mvfd8PbqxeeleKqasZFcOHlz0sh4D0yCFMmgeiiiIItD6jOEBDVdWrOFweO0p0t2RrcefNIVYFPFkn5BmJaN7QOWE%2Bi76%2F6EikKuWvnrkPzdsNODsbCPlRPfKRAU%2BzPo5ADc5xhZmAZcRsxWEdW%2BksGso7oo6CM01XeJhUbbxdWVauUA8tJQu5roKjqtzJj2gWrc3SjQjOJ7K3whUrknztxKrQV4vx7o4c%2B35xOfr27SPkAEG%2BSHMXZQ%2FLxrKzLkXw%2FOAewnGM%2B3aOLFHX8whZvW0gY6ogH5v6bD6aII%2F1TNwLA%2FK%2BnPfxf9J02YN7JoBj1edvX8%2BaD%2FljT4QEZ3JG9a%2BFN4zzfNKUpVU2QjXgem9kvEoJ7OcF6mnqBXGZ5H4z%2FEYZoP377EU6j%2Flprwuz4MDkQEgRdtcjOaJI1YiXqOMhXhl8el3tBDoJPrfc%2BKZ%2BHgJJGLAvNDzekX3oBBl3U0x87hGb1KSLH13Dowc3ZR69Wm73UHjss%3D&Expires=1784006088", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH6342ZW4PS5&Signature=lgiC0x8bq3ewKrXghFrugxjM71c%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEIaCXVzLWVhc3QtMSJHMEUCIQD480Wxiaub4vtRUMXy3%2F%2FtFaJ%2B%2BdT%2FDWU9GVX8dhvp9QIgEO3qrcLnjokm7%2F4WbYHAKaal7X4FjJ1Sqsze4IlPrEgqkAQIChAAGgw1MTIzODM5MjYxOTkiDP7EfurmS%2FnPdqCDCirtAwtrqJW5c%2FWjLKp4S28CITbNPtG8uK3cuFowIzjrP7k7%2BIeODXp35REsYOhPYg8W5igLzHD3ACe5%2FGKb2O1Xxb0tY7DDfkv85e01K9rj0qPQV48lFpUJpW9MrLOaRQXWcjcal7OY4UEFpLnP%2F5NcMehvVlc48g2xMgnOrCu8Svjzra6NSakShu2wya2scPDI1zGIS%2FOhFi8BlHl15w9pFuZwEyF5ZtNdj97LWKSZQmgkQtr0Ta4c2SUAIlMgy1F0A7E6apfcp3iRQV%2FB6dEMTo4eH6I%2B3NxjJHdVDEMoomP4gIE7vyZmLQaEIQIWhtai%2B0PapKIX%2B%2BVbW68dHDGweTHM6EAa%2BS3NVnF8PDtEX2JTWBA52BPVdCfQxs2ivYB3mvfd8PbqxeeleKqasZFcOHlz0sh4D0yCFMmgeiiiIItD6jOEBDVdWrOFweO0p0t2RrcefNIVYFPFkn5BmJaN7QOWE%2Bi76%2F6EikKuWvnrkPzdsNODsbCPlRPfKRAU%2BzPo5ADc5xhZmAZcRsxWEdW%2BksGso7oo6CM01XeJhUbbxdWVauUA8tJQu5roKjqtzJj2gWrc3SjQjOJ7K3whUrknztxKrQV4vx7o4c%2B35xOfr27SPkAEG%2BSHMXZQ%2FLxrKzLkXw%2FOAewnGM%2B3aOLFHX8whZvW0gY6ogH5v6bD6aII%2F1TNwLA%2FK%2BnPfxf9J02YN7JoBj1edvX8%2BaD%2FljT4QEZ3JG9a%2BFN4zzfNKUpVU2QjXgem9kvEoJ7OcF6mnqBXGZ5H4z%2FEYZoP377EU6j%2Flprwuz4MDkQEgRdtcjOaJI1YiXqOMhXhl8el3tBDoJPrfc%2BKZ%2BHgJJGLAvNDzekX3oBBl3U0x87hGb1KSLH13Dowc3ZR69Wm73UHjss%3D&Expires=1784006088", "piano": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/piano.wav?AWSAccessKeyId=ASIAXOTDWH6342ZW4PS5&Signature=eONTSz%2Fto6tpKYwAMN02hk3%2FWo4%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEIaCXVzLWVhc3QtMSJHMEUCIQD480Wxiaub4vtRUMXy3%2F%2FtFaJ%2B%2BdT%2FDWU9GVX8dhvp9QIgEO3qrcLnjokm7%2F4WbYHAKaal7X4FjJ1Sqsze4IlPrEgqkAQIChAAGgw1MTIzODM5MjYxOTkiDP7EfurmS%2FnPdqCDCirtAwtrqJW5c%2FWjLKp4S28CITbNPtG8uK3cuFowIzjrP7k7%2BIeODXp35REsYOhPYg8W5igLzHD3ACe5%2FGKb2O1Xxb0tY7DDfkv85e01K9rj0qPQV48lFpUJpW9MrLOaRQXWcjcal7OY4UEFpLnP%2F5NcMehvVlc48g2xMgnOrCu8Svjzra6NSakShu2wya2scPDI1zGIS%2FOhFi8BlHl15w9pFuZwEyF5ZtNdj97LWKSZQmgkQtr0Ta4c2SUAIlMgy1F0A7E6apfcp3iRQV%2FB6dEMTo4eH6I%2B3NxjJHdVDEMoomP4gIE7vyZmLQaEIQIWhtai%2B0PapKIX%2B%2BVbW68dHDGweTHM6EAa%2BS3NVnF8PDtEX2JTWBA52BPVdCfQxs2ivYB3mvfd8PbqxeeleKqasZFcOHlz0sh4D0yCFMmgeiiiIItD6jOEBDVdWrOFweO0p0t2RrcefNIVYFPFkn5BmJaN7QOWE%2Bi76%2F6EikKuWvnrkPzdsNODsbCPlRPfKRAU%2BzPo5ADc5xhZmAZcRsxWEdW%2BksGso7oo6CM01XeJhUbbxdWVauUA8tJQu5roKjqtzJj2gWrc3SjQjOJ7K3whUrknztxKrQV4vx7o4c%2B35xOfr27SPkAEG%2BSHMXZQ%2FLxrKzLkXw%2FOAewnGM%2B3aOLFHX8whZvW0gY6ogH5v6bD6aII%2F1TNwLA%2FK%2BnPfxf9J02YN7JoBj1edvX8%2BaD%2FljT4QEZ3JG9a%2BFN4zzfNKUpVU2QjXgem9kvEoJ7OcF6mnqBXGZ5H4z%2FEYZoP377EU6j%2Flprwuz4MDkQEgRdtcjOaJI1YiXqOMhXhl8el3tBDoJPrfc%2BKZ%2BHgJJGLAvNDzekX3oBBl3U0x87hGb1KSLH13Dowc3ZR69Wm73UHjss%3D&Expires=1784006088", "guitar": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/guitar.wav?AWSAccessKeyId=ASIAXOTDWH6342ZW4PS5&Signature=Ze8qiPalJ4u7mgXN8g0GrV0UXos%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEIaCXVzLWVhc3QtMSJHMEUCIQD480Wxiaub4vtRUMXy3%2F%2FtFaJ%2B%2BdT%2FDWU9GVX8dhvp9QIgEO3qrcLnjokm7%2F4WbYHAKaal7X4FjJ1Sqsze4IlPrEgqkAQIChAAGgw1MTIzODM5MjYxOTkiDP7EfurmS%2FnPdqCDCirtAwtrqJW5c%2FWjLKp4S28CITbNPtG8uK3cuFowIzjrP7k7%2BIeODXp35REsYOhPYg8W5igLzHD3ACe5%2FGKb2O1Xxb0tY7DDfkv85e01K9rj0qPQV48lFpUJpW9MrLOaRQXWcjcal7OY4UEFpLnP%2F5NcMehvVlc48g2xMgnOrCu8Svjzra6NSakShu2wya2scPDI1zGIS%2FOhFi8BlHl15w9pFuZwEyF5ZtNdj97LWKSZQmgkQtr0Ta4c2SUAIlMgy1F0A7E6apfcp3iRQV%2FB6dEMTo4eH6I%2B3NxjJHdVDEMoomP4gIE7vyZmLQaEIQIWhtai%2B0PapKIX%2B%2BVbW68dHDGweTHM6EAa%2BS3NVnF8PDtEX2JTWBA52BPVdCfQxs2ivYB3mvfd8PbqxeeleKqasZFcOHlz0sh4D0yCFMmgeiiiIItD6jOEBDVdWrOFweO0p0t2RrcefNIVYFPFkn5BmJaN7QOWE%2Bi76%2F6EikKuWvnrkPzdsNODsbCPlRPfKRAU%2BzPo5ADc5xhZmAZcRsxWEdW%2BksGso7oo6CM01XeJhUbbxdWVauUA8tJQu5roKjqtzJj2gWrc3SjQjOJ7K3whUrknztxKrQV4vx7o4c%2B35xOfr27SPkAEG%2BSHMXZQ%2FLxrKzLkXw%2FOAewnGM%2B3aOLFHX8whZvW0gY6ogH5v6bD6aII%2F1TNwLA%2FK%2BnPfxf9J02YN7JoBj1edvX8%2BaD%2FljT4QEZ3JG9a%2BFN4zzfNKUpVU2QjXgem9kvEoJ7OcF6mnqBXGZ5H4z%2FEYZoP377EU6j%2Flprwuz4MDkQEgRdtcjOaJI1YiXqOMhXhl8el3tBDoJPrfc%2BKZ%2BHgJJGLAvNDzekX3oBBl3U0x87hGb1KSLH13Dowc3ZR69Wm73UHjss%3D&Expires=1784006088", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH6342ZW4PS5&Signature=t3g6uuHVpdAiyZHkno55q2%2FuwWA%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEEIaCXVzLWVhc3QtMSJHMEUCIQD480Wxiaub4vtRUMXy3%2F%2FtFaJ%2B%2BdT%2FDWU9GVX8dhvp9QIgEO3qrcLnjokm7%2F4WbYHAKaal7X4FjJ1Sqsze4IlPrEgqkAQIChAAGgw1MTIzODM5MjYxOTkiDP7EfurmS%2FnPdqCDCirtAwtrqJW5c%2FWjLKp4S28CITbNPtG8uK3cuFowIzjrP7k7%2BIeODXp35REsYOhPYg8W5igLzHD3ACe5%2FGKb2O1Xxb0tY7DDfkv85e01K9rj0qPQV48lFpUJpW9MrLOaRQXWcjcal7OY4UEFpLnP%2F5NcMehvVlc48g2xMgnOrCu8Svjzra6NSakShu2wya2scPDI1zGIS%2FOhFi8BlHl15w9pFuZwEyF5ZtNdj97LWKSZQmgkQtr0Ta4c2SUAIlMgy1F0A7E6apfcp3iRQV%2FB6dEMTo4eH6I%2B3NxjJHdVDEMoomP4gIE7vyZmLQaEIQIWhtai%2B0PapKIX%2B%2BVbW68dHDGweTHM6EAa%2BS3NVnF8PDtEX2JTWBA52BPVdCfQxs2ivYB3mvfd8PbqxeeleKqasZFcOHlz0sh4D0yCFMmgeiiiIItD6jOEBDVdWrOFweO0p0t2RrcefNIVYFPFkn5BmJaN7QOWE%2Bi76%2F6EikKuWvnrkPzdsNODsbCPlRPfKRAU%2BzPo5ADc5xhZmAZcRsxWEdW%2BksGso7oo6CM01XeJhUbbxdWVauUA8tJQu5roKjqtzJj2gWrc3SjQjOJ7K3whUrknztxKrQV4vx7o4c%2B35xOfr27SPkAEG%2BSHMXZQ%2FLxrKzLkXw%2FOAewnGM%2B3aOLFHX8whZvW0gY6ogH5v6bD6aII%2F1TNwLA%2FK%2BnPfxf9J02YN7JoBj1edvX8%2BaD%2FljT4QEZ3JG9a%2BFN4zzfNKUpVU2QjXgem9kvEoJ7OcF6mnqBXGZ5H4z%2FEYZoP377EU6j%2Flprwuz4MDkQEgRdtcjOaJI1YiXqOMhXhl8el3tBDoJPrfc%2BKZ%2BHgJJGLAvNDzekX3oBBl3U0x87hGb1KSLH13Dowc3ZR69Wm73UHjss%3D&Expires=1784006088"};
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
