import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH63Z6XZ7DW3&Signature=fsbkzk1%2B57EHoNYFnogiWxcs4dc%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEIP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJIMEYCIQCcKoUInrgKEzYRqliO47gj6hTLV6Rpm5e5lrxu%2F6FVFQIhAMTIVkS%2BAAI7WPtQ2%2FVB1a7AEi3VYi3oK4AwUVoxgXs3KpAECEsQABoMNTEyMzgzOTI2MTk5IgyK024noik6YcdZL7Qq7QNPdhwcBf5tazOc4GIh1wXmwIji24q3jqbhlmW4w7iz4g2Ilg3Ym7o9HyOHlA3S71TOAaZ1LgdKoeM6AcaHVFHgmQ9KpUtksO1GNol3Rchtb72Gr4OJtkYrclQcMdGtFGsuLhGkQsQbe4nummH9dQagNE3KRNqddvBOLPYOjuH%2F3DPXSUm4dZ965Dz1tdsfKnBAPsR0NgDMhI0tyamW85ic8dE5a0Z7Mu8yACd%2BkFPdW9LI6vXNPpZ1uAEdI4fNrLlnyU9fIvEIN%2BDGoErQdsbdho9hFjlt%2BI6Lka5Sjf3eMoONR5pX5cutLhfgmLT7Gm5zFFU6tqnwp2m7vjVOPPFX1znVR6gSBg1uE%2FK%2FCsGXo0DkxOyd77TaMSiS9UdeTbYeM9RJldM%2B%2BQm%2FA6cpqsj8xBW6kkU4suX2y2v%2Bju3BkA3c338YFF137uN1qE4OJs2LjWMRzDE%2FFR352p3GAaZScxF9ReIsWEoNL0TQHImX59XFDmzPMaykrg%2BzM0WpDONO4cRywhoa1PC97oUd1e6GCdnHGPur9SneuicGYGRXnVwaAralQY45ODSHQjlK5%2BfWGOmPywWPEAXpm7huLOAg7UyldmkLdLeSbsY4LtKfXN8qGuxhYjrvlJ7ZzLqlVNIopWmGXiNL9RvfJ%2B3dMPDA5NIGOqEB4BG%2FfJ92fADYdXVBC5JHMGdYt4aK9c0ntb2U%2Fj5MNRmfMibTk6rKalzqs2coY7NwChq5h4qjJAQFeP05%2FaopQt0OHvKTO7gCfHdmFQ0wTKwgzZeW98S5jHAOtbmcUrPFq2MlevO6O%2FpayGQ6XUWFMZCGmk9%2B8MTbEF4r0toC13%2FigZkMc7tC75TAGnScwimVryfBybxSculwqKfXJVNbHVg%3D&Expires=1784240308", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH63Z6XZ7DW3&Signature=v1SL4zwUW6yy%2Boe%2FTivm5sdO3BU%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEIP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJIMEYCIQCcKoUInrgKEzYRqliO47gj6hTLV6Rpm5e5lrxu%2F6FVFQIhAMTIVkS%2BAAI7WPtQ2%2FVB1a7AEi3VYi3oK4AwUVoxgXs3KpAECEsQABoMNTEyMzgzOTI2MTk5IgyK024noik6YcdZL7Qq7QNPdhwcBf5tazOc4GIh1wXmwIji24q3jqbhlmW4w7iz4g2Ilg3Ym7o9HyOHlA3S71TOAaZ1LgdKoeM6AcaHVFHgmQ9KpUtksO1GNol3Rchtb72Gr4OJtkYrclQcMdGtFGsuLhGkQsQbe4nummH9dQagNE3KRNqddvBOLPYOjuH%2F3DPXSUm4dZ965Dz1tdsfKnBAPsR0NgDMhI0tyamW85ic8dE5a0Z7Mu8yACd%2BkFPdW9LI6vXNPpZ1uAEdI4fNrLlnyU9fIvEIN%2BDGoErQdsbdho9hFjlt%2BI6Lka5Sjf3eMoONR5pX5cutLhfgmLT7Gm5zFFU6tqnwp2m7vjVOPPFX1znVR6gSBg1uE%2FK%2FCsGXo0DkxOyd77TaMSiS9UdeTbYeM9RJldM%2B%2BQm%2FA6cpqsj8xBW6kkU4suX2y2v%2Bju3BkA3c338YFF137uN1qE4OJs2LjWMRzDE%2FFR352p3GAaZScxF9ReIsWEoNL0TQHImX59XFDmzPMaykrg%2BzM0WpDONO4cRywhoa1PC97oUd1e6GCdnHGPur9SneuicGYGRXnVwaAralQY45ODSHQjlK5%2BfWGOmPywWPEAXpm7huLOAg7UyldmkLdLeSbsY4LtKfXN8qGuxhYjrvlJ7ZzLqlVNIopWmGXiNL9RvfJ%2B3dMPDA5NIGOqEB4BG%2FfJ92fADYdXVBC5JHMGdYt4aK9c0ntb2U%2Fj5MNRmfMibTk6rKalzqs2coY7NwChq5h4qjJAQFeP05%2FaopQt0OHvKTO7gCfHdmFQ0wTKwgzZeW98S5jHAOtbmcUrPFq2MlevO6O%2FpayGQ6XUWFMZCGmk9%2B8MTbEF4r0toC13%2FigZkMc7tC75TAGnScwimVryfBybxSculwqKfXJVNbHVg%3D&Expires=1784240308", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH63Z6XZ7DW3&Signature=0IjOnZ7igZMoc2lR4yW6WI0ExO0%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEIP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJIMEYCIQCcKoUInrgKEzYRqliO47gj6hTLV6Rpm5e5lrxu%2F6FVFQIhAMTIVkS%2BAAI7WPtQ2%2FVB1a7AEi3VYi3oK4AwUVoxgXs3KpAECEsQABoMNTEyMzgzOTI2MTk5IgyK024noik6YcdZL7Qq7QNPdhwcBf5tazOc4GIh1wXmwIji24q3jqbhlmW4w7iz4g2Ilg3Ym7o9HyOHlA3S71TOAaZ1LgdKoeM6AcaHVFHgmQ9KpUtksO1GNol3Rchtb72Gr4OJtkYrclQcMdGtFGsuLhGkQsQbe4nummH9dQagNE3KRNqddvBOLPYOjuH%2F3DPXSUm4dZ965Dz1tdsfKnBAPsR0NgDMhI0tyamW85ic8dE5a0Z7Mu8yACd%2BkFPdW9LI6vXNPpZ1uAEdI4fNrLlnyU9fIvEIN%2BDGoErQdsbdho9hFjlt%2BI6Lka5Sjf3eMoONR5pX5cutLhfgmLT7Gm5zFFU6tqnwp2m7vjVOPPFX1znVR6gSBg1uE%2FK%2FCsGXo0DkxOyd77TaMSiS9UdeTbYeM9RJldM%2B%2BQm%2FA6cpqsj8xBW6kkU4suX2y2v%2Bju3BkA3c338YFF137uN1qE4OJs2LjWMRzDE%2FFR352p3GAaZScxF9ReIsWEoNL0TQHImX59XFDmzPMaykrg%2BzM0WpDONO4cRywhoa1PC97oUd1e6GCdnHGPur9SneuicGYGRXnVwaAralQY45ODSHQjlK5%2BfWGOmPywWPEAXpm7huLOAg7UyldmkLdLeSbsY4LtKfXN8qGuxhYjrvlJ7ZzLqlVNIopWmGXiNL9RvfJ%2B3dMPDA5NIGOqEB4BG%2FfJ92fADYdXVBC5JHMGdYt4aK9c0ntb2U%2Fj5MNRmfMibTk6rKalzqs2coY7NwChq5h4qjJAQFeP05%2FaopQt0OHvKTO7gCfHdmFQ0wTKwgzZeW98S5jHAOtbmcUrPFq2MlevO6O%2FpayGQ6XUWFMZCGmk9%2B8MTbEF4r0toC13%2FigZkMc7tC75TAGnScwimVryfBybxSculwqKfXJVNbHVg%3D&Expires=1784240308", "piano": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/piano.wav?AWSAccessKeyId=ASIAXOTDWH63Z6XZ7DW3&Signature=5JCjzGmO9RQx%2BYoc033gK4ppM6M%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEIP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJIMEYCIQCcKoUInrgKEzYRqliO47gj6hTLV6Rpm5e5lrxu%2F6FVFQIhAMTIVkS%2BAAI7WPtQ2%2FVB1a7AEi3VYi3oK4AwUVoxgXs3KpAECEsQABoMNTEyMzgzOTI2MTk5IgyK024noik6YcdZL7Qq7QNPdhwcBf5tazOc4GIh1wXmwIji24q3jqbhlmW4w7iz4g2Ilg3Ym7o9HyOHlA3S71TOAaZ1LgdKoeM6AcaHVFHgmQ9KpUtksO1GNol3Rchtb72Gr4OJtkYrclQcMdGtFGsuLhGkQsQbe4nummH9dQagNE3KRNqddvBOLPYOjuH%2F3DPXSUm4dZ965Dz1tdsfKnBAPsR0NgDMhI0tyamW85ic8dE5a0Z7Mu8yACd%2BkFPdW9LI6vXNPpZ1uAEdI4fNrLlnyU9fIvEIN%2BDGoErQdsbdho9hFjlt%2BI6Lka5Sjf3eMoONR5pX5cutLhfgmLT7Gm5zFFU6tqnwp2m7vjVOPPFX1znVR6gSBg1uE%2FK%2FCsGXo0DkxOyd77TaMSiS9UdeTbYeM9RJldM%2B%2BQm%2FA6cpqsj8xBW6kkU4suX2y2v%2Bju3BkA3c338YFF137uN1qE4OJs2LjWMRzDE%2FFR352p3GAaZScxF9ReIsWEoNL0TQHImX59XFDmzPMaykrg%2BzM0WpDONO4cRywhoa1PC97oUd1e6GCdnHGPur9SneuicGYGRXnVwaAralQY45ODSHQjlK5%2BfWGOmPywWPEAXpm7huLOAg7UyldmkLdLeSbsY4LtKfXN8qGuxhYjrvlJ7ZzLqlVNIopWmGXiNL9RvfJ%2B3dMPDA5NIGOqEB4BG%2FfJ92fADYdXVBC5JHMGdYt4aK9c0ntb2U%2Fj5MNRmfMibTk6rKalzqs2coY7NwChq5h4qjJAQFeP05%2FaopQt0OHvKTO7gCfHdmFQ0wTKwgzZeW98S5jHAOtbmcUrPFq2MlevO6O%2FpayGQ6XUWFMZCGmk9%2B8MTbEF4r0toC13%2FigZkMc7tC75TAGnScwimVryfBybxSculwqKfXJVNbHVg%3D&Expires=1784240308", "guitar": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/guitar.wav?AWSAccessKeyId=ASIAXOTDWH63Z6XZ7DW3&Signature=TmSsLyk0D8ZIPR1TOik6U53%2Bfo8%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEIP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJIMEYCIQCcKoUInrgKEzYRqliO47gj6hTLV6Rpm5e5lrxu%2F6FVFQIhAMTIVkS%2BAAI7WPtQ2%2FVB1a7AEi3VYi3oK4AwUVoxgXs3KpAECEsQABoMNTEyMzgzOTI2MTk5IgyK024noik6YcdZL7Qq7QNPdhwcBf5tazOc4GIh1wXmwIji24q3jqbhlmW4w7iz4g2Ilg3Ym7o9HyOHlA3S71TOAaZ1LgdKoeM6AcaHVFHgmQ9KpUtksO1GNol3Rchtb72Gr4OJtkYrclQcMdGtFGsuLhGkQsQbe4nummH9dQagNE3KRNqddvBOLPYOjuH%2F3DPXSUm4dZ965Dz1tdsfKnBAPsR0NgDMhI0tyamW85ic8dE5a0Z7Mu8yACd%2BkFPdW9LI6vXNPpZ1uAEdI4fNrLlnyU9fIvEIN%2BDGoErQdsbdho9hFjlt%2BI6Lka5Sjf3eMoONR5pX5cutLhfgmLT7Gm5zFFU6tqnwp2m7vjVOPPFX1znVR6gSBg1uE%2FK%2FCsGXo0DkxOyd77TaMSiS9UdeTbYeM9RJldM%2B%2BQm%2FA6cpqsj8xBW6kkU4suX2y2v%2Bju3BkA3c338YFF137uN1qE4OJs2LjWMRzDE%2FFR352p3GAaZScxF9ReIsWEoNL0TQHImX59XFDmzPMaykrg%2BzM0WpDONO4cRywhoa1PC97oUd1e6GCdnHGPur9SneuicGYGRXnVwaAralQY45ODSHQjlK5%2BfWGOmPywWPEAXpm7huLOAg7UyldmkLdLeSbsY4LtKfXN8qGuxhYjrvlJ7ZzLqlVNIopWmGXiNL9RvfJ%2B3dMPDA5NIGOqEB4BG%2FfJ92fADYdXVBC5JHMGdYt4aK9c0ntb2U%2Fj5MNRmfMibTk6rKalzqs2coY7NwChq5h4qjJAQFeP05%2FaopQt0OHvKTO7gCfHdmFQ0wTKwgzZeW98S5jHAOtbmcUrPFq2MlevO6O%2FpayGQ6XUWFMZCGmk9%2B8MTbEF4r0toC13%2FigZkMc7tC75TAGnScwimVryfBybxSculwqKfXJVNbHVg%3D&Expires=1784240308", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH63Z6XZ7DW3&Signature=NyXH350MUVF2aiammcPRPn3%2FLOQ%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEIP%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJIMEYCIQCcKoUInrgKEzYRqliO47gj6hTLV6Rpm5e5lrxu%2F6FVFQIhAMTIVkS%2BAAI7WPtQ2%2FVB1a7AEi3VYi3oK4AwUVoxgXs3KpAECEsQABoMNTEyMzgzOTI2MTk5IgyK024noik6YcdZL7Qq7QNPdhwcBf5tazOc4GIh1wXmwIji24q3jqbhlmW4w7iz4g2Ilg3Ym7o9HyOHlA3S71TOAaZ1LgdKoeM6AcaHVFHgmQ9KpUtksO1GNol3Rchtb72Gr4OJtkYrclQcMdGtFGsuLhGkQsQbe4nummH9dQagNE3KRNqddvBOLPYOjuH%2F3DPXSUm4dZ965Dz1tdsfKnBAPsR0NgDMhI0tyamW85ic8dE5a0Z7Mu8yACd%2BkFPdW9LI6vXNPpZ1uAEdI4fNrLlnyU9fIvEIN%2BDGoErQdsbdho9hFjlt%2BI6Lka5Sjf3eMoONR5pX5cutLhfgmLT7Gm5zFFU6tqnwp2m7vjVOPPFX1znVR6gSBg1uE%2FK%2FCsGXo0DkxOyd77TaMSiS9UdeTbYeM9RJldM%2B%2BQm%2FA6cpqsj8xBW6kkU4suX2y2v%2Bju3BkA3c338YFF137uN1qE4OJs2LjWMRzDE%2FFR352p3GAaZScxF9ReIsWEoNL0TQHImX59XFDmzPMaykrg%2BzM0WpDONO4cRywhoa1PC97oUd1e6GCdnHGPur9SneuicGYGRXnVwaAralQY45ODSHQjlK5%2BfWGOmPywWPEAXpm7huLOAg7UyldmkLdLeSbsY4LtKfXN8qGuxhYjrvlJ7ZzLqlVNIopWmGXiNL9RvfJ%2B3dMPDA5NIGOqEB4BG%2FfJ92fADYdXVBC5JHMGdYt4aK9c0ntb2U%2Fj5MNRmfMibTk6rKalzqs2coY7NwChq5h4qjJAQFeP05%2FaopQt0OHvKTO7gCfHdmFQ0wTKwgzZeW98S5jHAOtbmcUrPFq2MlevO6O%2FpayGQ6XUWFMZCGmk9%2B8MTbEF4r0toC13%2FigZkMc7tC75TAGnScwimVryfBybxSculwqKfXJVNbHVg%3D&Expires=1784240308"};
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
                    setStatusMessage("Stems ready. Extracting MIDI...");
                }

                // Phase 4: MIDI Extraction Finished
                else if (data.type === "midi_processing_complete") {
                    console.log("MIDI received from Basic Pitch:", data);
                    setStatusMessage(`MIDI extraction complete for ${data.stem_name}.`);
                }
                
                // Phase 3.5: yt-dlp Extraction Updates
                else if (data.type === "status") {
                    setStatusMessage(data.message);
                }
                else if (data.type === "extraction_complete") {
                    console.log("Extracted audio details:", data);
                    setStatusMessage("Extraction Complete! Audio is ready.");
                    setIsSplitting(false);
                    // Optionally, store the downloadUrl in state if needed globally, 
                    // or just leave it for now since AWS Batch will start processing it automatically.
                    // socketRef.current.close(); // Wait for Batch processing_complete before closing
                }
                
                // Phase 5: Server Error
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

    const executeLinkExtraction = (url) => {
        if (!awsConnectionId) {
            setErrorMsg("Still establishing secure connection to AWS... please try again in a few seconds.");
            connectWebSocket();
            return;
        }
        
        setIsSplitting(true);
        setErrorMsg("");
        setStatusMessage("Sending link to CloudDSP...");
        setStemUrls(null);
        
        // Construct the payload mapping to the 'yt-dlp' route
        const payload = {
            action: "yt-dlp",
            url: url,
            stemMode: splitMode
        };
        
        socketRef.current.send(JSON.stringify(payload));
    };


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
        executeStemSplit, executeLinkExtraction, connectWebSocket, closeWebSocket
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
