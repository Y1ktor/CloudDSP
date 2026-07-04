import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH63Y6F2EXMW&Signature=U2PNp9NzsDkQ9jJbULYSGslrwDA%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJIMEYCIQDnOoMGys3vhHdXCiySlVtDTZyGDFAULuI4ZSQF3HywcAIhAOzifWcUhoCUsRO2q%2FWzqIuOBWzjqCMGtlVkXCOHTy%2FqKpAECBsQABoMNTEyMzgzOTI2MTk5Igzwp7PvPxb%2B0mjav4oq7QN0SWO1UGUAMsvKktceUTPRO7og1MVwXExva1OSEF6jeZBJV51PXqxu6wK7ISkJLpSUOFfGVJG6d2JlLIjK1COYTFIF6NQlGQHStLmt3qa0n91u%2Bh8keM40N9oV6B7n2Im2A6zqqdAJPYV6PHvz0r54NhjZdsvkx7MuI%2FAsYP%2FctHNuKQZV08IUBGZBz89Dt1dVDUWSsA7j52GU8Vf7bR61yGhljlwGrfqEzHJbV6DrMoSUBQ9Fdc5sYmK7W1jxs4g2BW4nZ7nEgQXboQJnx%2B7K5OwLER3643WbKI5PBCqlwpZl2yZnUbs9cfS6OOvWLtOZnTwVzkmkzqhm%2BKGi0KaB3yaeMAssX1f70ZFsSRZpBeNdUZuF4i9WyOLu%2BWKX8W6nDHTpgP%2FL75Ag9V42pD2UHQP%2F96A3KK6%2FBFSAcYDaR77JSUmldXhg%2Fb4E2nQKzLQ4ynoiCj9xA64ZokAKXsD3i0N93LrNQrKr4qu%2FN4hyYQyePTcEmPtgsng1zdmPd7FJO1RueWb%2FfcBNyxIdq39V7837%2F%2B4YFaUY%2FzdhIpg6FNrI%2BFi20O3PpkmKC88TEowsjqAcTPVoV%2BAVuGnE533LKNKwvVSLmsMCuAC1UQherZfG%2FfKrPvlpkMKUdUNkdAo5CPxoxMACNcB%2Frg%2FgMJ3UodIGOqEBSXOBGVUHDswPZoYDFitOAz%2Befu0Z8kd6ofOICYWV%2Fv4EMcD7CINYnBX2jUbE8QuMmBa%2B6r0%2FV14njwXz16WF29OiV8n45Qqg3OqOMLwco%2FiKpQn4UU%2FKuPe29MYlWg3FoPYTKdJT08KE6dimwUdt5oGOjuSZRENrE%2BDvjca9q%2BnBvVog7CwJbIPYUQ4uGXaB4dPa2JtuySgrZQ6xPomgA00%3D&Expires=1783145056", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH63Y6F2EXMW&Signature=VSLN37QNyVGCj8rpec26WJ43Y20%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJIMEYCIQDnOoMGys3vhHdXCiySlVtDTZyGDFAULuI4ZSQF3HywcAIhAOzifWcUhoCUsRO2q%2FWzqIuOBWzjqCMGtlVkXCOHTy%2FqKpAECBsQABoMNTEyMzgzOTI2MTk5Igzwp7PvPxb%2B0mjav4oq7QN0SWO1UGUAMsvKktceUTPRO7og1MVwXExva1OSEF6jeZBJV51PXqxu6wK7ISkJLpSUOFfGVJG6d2JlLIjK1COYTFIF6NQlGQHStLmt3qa0n91u%2Bh8keM40N9oV6B7n2Im2A6zqqdAJPYV6PHvz0r54NhjZdsvkx7MuI%2FAsYP%2FctHNuKQZV08IUBGZBz89Dt1dVDUWSsA7j52GU8Vf7bR61yGhljlwGrfqEzHJbV6DrMoSUBQ9Fdc5sYmK7W1jxs4g2BW4nZ7nEgQXboQJnx%2B7K5OwLER3643WbKI5PBCqlwpZl2yZnUbs9cfS6OOvWLtOZnTwVzkmkzqhm%2BKGi0KaB3yaeMAssX1f70ZFsSRZpBeNdUZuF4i9WyOLu%2BWKX8W6nDHTpgP%2FL75Ag9V42pD2UHQP%2F96A3KK6%2FBFSAcYDaR77JSUmldXhg%2Fb4E2nQKzLQ4ynoiCj9xA64ZokAKXsD3i0N93LrNQrKr4qu%2FN4hyYQyePTcEmPtgsng1zdmPd7FJO1RueWb%2FfcBNyxIdq39V7837%2F%2B4YFaUY%2FzdhIpg6FNrI%2BFi20O3PpkmKC88TEowsjqAcTPVoV%2BAVuGnE533LKNKwvVSLmsMCuAC1UQherZfG%2FfKrPvlpkMKUdUNkdAo5CPxoxMACNcB%2Frg%2FgMJ3UodIGOqEBSXOBGVUHDswPZoYDFitOAz%2Befu0Z8kd6ofOICYWV%2Fv4EMcD7CINYnBX2jUbE8QuMmBa%2B6r0%2FV14njwXz16WF29OiV8n45Qqg3OqOMLwco%2FiKpQn4UU%2FKuPe29MYlWg3FoPYTKdJT08KE6dimwUdt5oGOjuSZRENrE%2BDvjca9q%2BnBvVog7CwJbIPYUQ4uGXaB4dPa2JtuySgrZQ6xPomgA00%3D&Expires=1783145056", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH63Y6F2EXMW&Signature=qCHg%2BbO1oy3KufLzGoX%2BfXrfxPQ%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJIMEYCIQDnOoMGys3vhHdXCiySlVtDTZyGDFAULuI4ZSQF3HywcAIhAOzifWcUhoCUsRO2q%2FWzqIuOBWzjqCMGtlVkXCOHTy%2FqKpAECBsQABoMNTEyMzgzOTI2MTk5Igzwp7PvPxb%2B0mjav4oq7QN0SWO1UGUAMsvKktceUTPRO7og1MVwXExva1OSEF6jeZBJV51PXqxu6wK7ISkJLpSUOFfGVJG6d2JlLIjK1COYTFIF6NQlGQHStLmt3qa0n91u%2Bh8keM40N9oV6B7n2Im2A6zqqdAJPYV6PHvz0r54NhjZdsvkx7MuI%2FAsYP%2FctHNuKQZV08IUBGZBz89Dt1dVDUWSsA7j52GU8Vf7bR61yGhljlwGrfqEzHJbV6DrMoSUBQ9Fdc5sYmK7W1jxs4g2BW4nZ7nEgQXboQJnx%2B7K5OwLER3643WbKI5PBCqlwpZl2yZnUbs9cfS6OOvWLtOZnTwVzkmkzqhm%2BKGi0KaB3yaeMAssX1f70ZFsSRZpBeNdUZuF4i9WyOLu%2BWKX8W6nDHTpgP%2FL75Ag9V42pD2UHQP%2F96A3KK6%2FBFSAcYDaR77JSUmldXhg%2Fb4E2nQKzLQ4ynoiCj9xA64ZokAKXsD3i0N93LrNQrKr4qu%2FN4hyYQyePTcEmPtgsng1zdmPd7FJO1RueWb%2FfcBNyxIdq39V7837%2F%2B4YFaUY%2FzdhIpg6FNrI%2BFi20O3PpkmKC88TEowsjqAcTPVoV%2BAVuGnE533LKNKwvVSLmsMCuAC1UQherZfG%2FfKrPvlpkMKUdUNkdAo5CPxoxMACNcB%2Frg%2FgMJ3UodIGOqEBSXOBGVUHDswPZoYDFitOAz%2Befu0Z8kd6ofOICYWV%2Fv4EMcD7CINYnBX2jUbE8QuMmBa%2B6r0%2FV14njwXz16WF29OiV8n45Qqg3OqOMLwco%2FiKpQn4UU%2FKuPe29MYlWg3FoPYTKdJT08KE6dimwUdt5oGOjuSZRENrE%2BDvjca9q%2BnBvVog7CwJbIPYUQ4uGXaB4dPa2JtuySgrZQ6xPomgA00%3D&Expires=1783145056", "piano": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/piano.wav?AWSAccessKeyId=ASIAXOTDWH63Y6F2EXMW&Signature=t6W6iS%2FgRuqTbomMCulr%2Be7ZFvI%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJIMEYCIQDnOoMGys3vhHdXCiySlVtDTZyGDFAULuI4ZSQF3HywcAIhAOzifWcUhoCUsRO2q%2FWzqIuOBWzjqCMGtlVkXCOHTy%2FqKpAECBsQABoMNTEyMzgzOTI2MTk5Igzwp7PvPxb%2B0mjav4oq7QN0SWO1UGUAMsvKktceUTPRO7og1MVwXExva1OSEF6jeZBJV51PXqxu6wK7ISkJLpSUOFfGVJG6d2JlLIjK1COYTFIF6NQlGQHStLmt3qa0n91u%2Bh8keM40N9oV6B7n2Im2A6zqqdAJPYV6PHvz0r54NhjZdsvkx7MuI%2FAsYP%2FctHNuKQZV08IUBGZBz89Dt1dVDUWSsA7j52GU8Vf7bR61yGhljlwGrfqEzHJbV6DrMoSUBQ9Fdc5sYmK7W1jxs4g2BW4nZ7nEgQXboQJnx%2B7K5OwLER3643WbKI5PBCqlwpZl2yZnUbs9cfS6OOvWLtOZnTwVzkmkzqhm%2BKGi0KaB3yaeMAssX1f70ZFsSRZpBeNdUZuF4i9WyOLu%2BWKX8W6nDHTpgP%2FL75Ag9V42pD2UHQP%2F96A3KK6%2FBFSAcYDaR77JSUmldXhg%2Fb4E2nQKzLQ4ynoiCj9xA64ZokAKXsD3i0N93LrNQrKr4qu%2FN4hyYQyePTcEmPtgsng1zdmPd7FJO1RueWb%2FfcBNyxIdq39V7837%2F%2B4YFaUY%2FzdhIpg6FNrI%2BFi20O3PpkmKC88TEowsjqAcTPVoV%2BAVuGnE533LKNKwvVSLmsMCuAC1UQherZfG%2FfKrPvlpkMKUdUNkdAo5CPxoxMACNcB%2Frg%2FgMJ3UodIGOqEBSXOBGVUHDswPZoYDFitOAz%2Befu0Z8kd6ofOICYWV%2Fv4EMcD7CINYnBX2jUbE8QuMmBa%2B6r0%2FV14njwXz16WF29OiV8n45Qqg3OqOMLwco%2FiKpQn4UU%2FKuPe29MYlWg3FoPYTKdJT08KE6dimwUdt5oGOjuSZRENrE%2BDvjca9q%2BnBvVog7CwJbIPYUQ4uGXaB4dPa2JtuySgrZQ6xPomgA00%3D&Expires=1783145056", "guitar": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/guitar.wav?AWSAccessKeyId=ASIAXOTDWH63Y6F2EXMW&Signature=%2BBRhWcDxGHecVofD%2FWK7ozIWJtA%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJIMEYCIQDnOoMGys3vhHdXCiySlVtDTZyGDFAULuI4ZSQF3HywcAIhAOzifWcUhoCUsRO2q%2FWzqIuOBWzjqCMGtlVkXCOHTy%2FqKpAECBsQABoMNTEyMzgzOTI2MTk5Igzwp7PvPxb%2B0mjav4oq7QN0SWO1UGUAMsvKktceUTPRO7og1MVwXExva1OSEF6jeZBJV51PXqxu6wK7ISkJLpSUOFfGVJG6d2JlLIjK1COYTFIF6NQlGQHStLmt3qa0n91u%2Bh8keM40N9oV6B7n2Im2A6zqqdAJPYV6PHvz0r54NhjZdsvkx7MuI%2FAsYP%2FctHNuKQZV08IUBGZBz89Dt1dVDUWSsA7j52GU8Vf7bR61yGhljlwGrfqEzHJbV6DrMoSUBQ9Fdc5sYmK7W1jxs4g2BW4nZ7nEgQXboQJnx%2B7K5OwLER3643WbKI5PBCqlwpZl2yZnUbs9cfS6OOvWLtOZnTwVzkmkzqhm%2BKGi0KaB3yaeMAssX1f70ZFsSRZpBeNdUZuF4i9WyOLu%2BWKX8W6nDHTpgP%2FL75Ag9V42pD2UHQP%2F96A3KK6%2FBFSAcYDaR77JSUmldXhg%2Fb4E2nQKzLQ4ynoiCj9xA64ZokAKXsD3i0N93LrNQrKr4qu%2FN4hyYQyePTcEmPtgsng1zdmPd7FJO1RueWb%2FfcBNyxIdq39V7837%2F%2B4YFaUY%2FzdhIpg6FNrI%2BFi20O3PpkmKC88TEowsjqAcTPVoV%2BAVuGnE533LKNKwvVSLmsMCuAC1UQherZfG%2FfKrPvlpkMKUdUNkdAo5CPxoxMACNcB%2Frg%2FgMJ3UodIGOqEBSXOBGVUHDswPZoYDFitOAz%2Befu0Z8kd6ofOICYWV%2Fv4EMcD7CINYnBX2jUbE8QuMmBa%2B6r0%2FV14njwXz16WF29OiV8n45Qqg3OqOMLwco%2FiKpQn4UU%2FKuPe29MYlWg3FoPYTKdJT08KE6dimwUdt5oGOjuSZRENrE%2BDvjca9q%2BnBvVog7CwJbIPYUQ4uGXaB4dPa2JtuySgrZQ6xPomgA00%3D&Expires=1783145056", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH63Y6F2EXMW&Signature=0HQH3ty2dvwLOtWWjE2dYti0j9U%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEFMaCXVzLWVhc3QtMSJIMEYCIQDnOoMGys3vhHdXCiySlVtDTZyGDFAULuI4ZSQF3HywcAIhAOzifWcUhoCUsRO2q%2FWzqIuOBWzjqCMGtlVkXCOHTy%2FqKpAECBsQABoMNTEyMzgzOTI2MTk5Igzwp7PvPxb%2B0mjav4oq7QN0SWO1UGUAMsvKktceUTPRO7og1MVwXExva1OSEF6jeZBJV51PXqxu6wK7ISkJLpSUOFfGVJG6d2JlLIjK1COYTFIF6NQlGQHStLmt3qa0n91u%2Bh8keM40N9oV6B7n2Im2A6zqqdAJPYV6PHvz0r54NhjZdsvkx7MuI%2FAsYP%2FctHNuKQZV08IUBGZBz89Dt1dVDUWSsA7j52GU8Vf7bR61yGhljlwGrfqEzHJbV6DrMoSUBQ9Fdc5sYmK7W1jxs4g2BW4nZ7nEgQXboQJnx%2B7K5OwLER3643WbKI5PBCqlwpZl2yZnUbs9cfS6OOvWLtOZnTwVzkmkzqhm%2BKGi0KaB3yaeMAssX1f70ZFsSRZpBeNdUZuF4i9WyOLu%2BWKX8W6nDHTpgP%2FL75Ag9V42pD2UHQP%2F96A3KK6%2FBFSAcYDaR77JSUmldXhg%2Fb4E2nQKzLQ4ynoiCj9xA64ZokAKXsD3i0N93LrNQrKr4qu%2FN4hyYQyePTcEmPtgsng1zdmPd7FJO1RueWb%2FfcBNyxIdq39V7837%2F%2B4YFaUY%2FzdhIpg6FNrI%2BFi20O3PpkmKC88TEowsjqAcTPVoV%2BAVuGnE533LKNKwvVSLmsMCuAC1UQherZfG%2FfKrPvlpkMKUdUNkdAo5CPxoxMACNcB%2Frg%2FgMJ3UodIGOqEBSXOBGVUHDswPZoYDFitOAz%2Befu0Z8kd6ofOICYWV%2Fv4EMcD7CINYnBX2jUbE8QuMmBa%2B6r0%2FV14njwXz16WF29OiV8n45Qqg3OqOMLwco%2FiKpQn4UU%2FKuPe29MYlWg3FoPYTKdJT08KE6dimwUdt5oGOjuSZRENrE%2BDvjca9q%2BnBvVog7CwJbIPYUQ4uGXaB4dPa2JtuySgrZQ6xPomgA00%3D&Expires=1783145056"};
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
