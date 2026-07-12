import React, { useState, useRef, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter';

const WEBSOCKET_URL = "wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev";
const API_URL = "https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url";

// ==========================================
// DEV MOCK PAYLOAD: Paste your presigned URLs here!
// ==========================================
const MOCK_PAYLOAD = {"vocals": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/vocals.wav?AWSAccessKeyId=ASIAXOTDWH636GXADLV7&Signature=VVx8qptBPrQMbonmrxokFzY68O0%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEA0aCXVzLWVhc3QtMSJIMEYCIQDV2SkLWnNhZPuLA1%2FHAN%2FQiwgSNF2tYQZ7Q4MgMcPWaAIhAKqMEK5%2BIvmVXuvwyqJ8V23ZQQ0GMgbZvnPhWADtF7YmKpkECNX%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5IgxIXXJUEBOOcmAvixwq7QPo8mkDDKLC%2BDu952cNwkghTLtEkwGRaiuGpaOqXykGXA0FGvzUa%2FcvyqkEJjsa8BZ5siHGH3HIwqWhJPu64zCSydYNZoqnnKbyKnD5SD1a8piFk0dCMWEJ1%2Fiq0YYSZ%2B6VZEXwkfcphP6B%2Ba7LGrnpvbuU0xyyrQyh3UJufYfalNqgQjWr%2FIiOJDA3%2Fn6rt6TNUZfEbp6m7fdh%2FZJkg%2FVKi%2B2poW5DDGf9JpY8N05pVw9jYSeibqI9lEemiYhqs%2FNSsBbenlVj659aLV2X6p7SlQzZjDxC7cu44UIb3nqb%2Fi6SSBGQv7aNrHLs8SPjRF8fGU8iWFeBt2EsaPy3h6d%2FUTC73mOESdeYXoZFCM2lPvKH4EvJSZa1wM6eXDJcrGXQnuAub6uq2IlKeLPuDFaQd2MMqRDDyzV4e5NEF7AkRNn%2Fxr0DPJu2NFaG3U7PXhp7HSbnxAtTdmUsRDH6g7tQ8xA2R2qMTUN3U1Jax4iRwcFxbkPErSApJ92oe3cjCXlL91%2F1HNvrvgl9QcsBcFiqiBejIQJsLR4gBM7Maz0Z87hHScs2K0AUvPBVuc49iknXm5gIJB09d%2BXwUKz5WwZC2koX05miUg19yb0QzxTnPVmKklTwKnxa36HuDobSMcuKbQY3iXHB%2BoFw9gqYMPPIytIGOqEBV9WZeZv9bxpzWlxQ26o9AHoPVBLMjjnFPilsg4EVgX2vVi55mBywdrDxI5WT9otQm9P5XFxdM6BjCEeyJKnCZrlUSYngutx7VPEQnuEThl76pWnGMk519UXj%2BdnE0%2FmDQYcmGkbAi%2Bj9Dxv3%2BwjLTkAdxTSEvH8ZOAkCJlDKM%2FeYTM8rPlsEdRg1LqRj4qAhgXIB%2B3h0w5mYn2BLbrEtgMw%3D&Expires=1783815379", "drums": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/drums.wav?AWSAccessKeyId=ASIAXOTDWH636GXADLV7&Signature=SwxmWMeO6GWKNX5QYhjCPjBpB3s%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEA0aCXVzLWVhc3QtMSJIMEYCIQDV2SkLWnNhZPuLA1%2FHAN%2FQiwgSNF2tYQZ7Q4MgMcPWaAIhAKqMEK5%2BIvmVXuvwyqJ8V23ZQQ0GMgbZvnPhWADtF7YmKpkECNX%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5IgxIXXJUEBOOcmAvixwq7QPo8mkDDKLC%2BDu952cNwkghTLtEkwGRaiuGpaOqXykGXA0FGvzUa%2FcvyqkEJjsa8BZ5siHGH3HIwqWhJPu64zCSydYNZoqnnKbyKnD5SD1a8piFk0dCMWEJ1%2Fiq0YYSZ%2B6VZEXwkfcphP6B%2Ba7LGrnpvbuU0xyyrQyh3UJufYfalNqgQjWr%2FIiOJDA3%2Fn6rt6TNUZfEbp6m7fdh%2FZJkg%2FVKi%2B2poW5DDGf9JpY8N05pVw9jYSeibqI9lEemiYhqs%2FNSsBbenlVj659aLV2X6p7SlQzZjDxC7cu44UIb3nqb%2Fi6SSBGQv7aNrHLs8SPjRF8fGU8iWFeBt2EsaPy3h6d%2FUTC73mOESdeYXoZFCM2lPvKH4EvJSZa1wM6eXDJcrGXQnuAub6uq2IlKeLPuDFaQd2MMqRDDyzV4e5NEF7AkRNn%2Fxr0DPJu2NFaG3U7PXhp7HSbnxAtTdmUsRDH6g7tQ8xA2R2qMTUN3U1Jax4iRwcFxbkPErSApJ92oe3cjCXlL91%2F1HNvrvgl9QcsBcFiqiBejIQJsLR4gBM7Maz0Z87hHScs2K0AUvPBVuc49iknXm5gIJB09d%2BXwUKz5WwZC2koX05miUg19yb0QzxTnPVmKklTwKnxa36HuDobSMcuKbQY3iXHB%2BoFw9gqYMPPIytIGOqEBV9WZeZv9bxpzWlxQ26o9AHoPVBLMjjnFPilsg4EVgX2vVi55mBywdrDxI5WT9otQm9P5XFxdM6BjCEeyJKnCZrlUSYngutx7VPEQnuEThl76pWnGMk519UXj%2BdnE0%2FmDQYcmGkbAi%2Bj9Dxv3%2BwjLTkAdxTSEvH8ZOAkCJlDKM%2FeYTM8rPlsEdRg1LqRj4qAhgXIB%2B3h0w5mYn2BLbrEtgMw%3D&Expires=1783815379", "bass": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/bass.wav?AWSAccessKeyId=ASIAXOTDWH636GXADLV7&Signature=4T3zR71FKtVd52iMPYokgynxIa8%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEA0aCXVzLWVhc3QtMSJIMEYCIQDV2SkLWnNhZPuLA1%2FHAN%2FQiwgSNF2tYQZ7Q4MgMcPWaAIhAKqMEK5%2BIvmVXuvwyqJ8V23ZQQ0GMgbZvnPhWADtF7YmKpkECNX%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5IgxIXXJUEBOOcmAvixwq7QPo8mkDDKLC%2BDu952cNwkghTLtEkwGRaiuGpaOqXykGXA0FGvzUa%2FcvyqkEJjsa8BZ5siHGH3HIwqWhJPu64zCSydYNZoqnnKbyKnD5SD1a8piFk0dCMWEJ1%2Fiq0YYSZ%2B6VZEXwkfcphP6B%2Ba7LGrnpvbuU0xyyrQyh3UJufYfalNqgQjWr%2FIiOJDA3%2Fn6rt6TNUZfEbp6m7fdh%2FZJkg%2FVKi%2B2poW5DDGf9JpY8N05pVw9jYSeibqI9lEemiYhqs%2FNSsBbenlVj659aLV2X6p7SlQzZjDxC7cu44UIb3nqb%2Fi6SSBGQv7aNrHLs8SPjRF8fGU8iWFeBt2EsaPy3h6d%2FUTC73mOESdeYXoZFCM2lPvKH4EvJSZa1wM6eXDJcrGXQnuAub6uq2IlKeLPuDFaQd2MMqRDDyzV4e5NEF7AkRNn%2Fxr0DPJu2NFaG3U7PXhp7HSbnxAtTdmUsRDH6g7tQ8xA2R2qMTUN3U1Jax4iRwcFxbkPErSApJ92oe3cjCXlL91%2F1HNvrvgl9QcsBcFiqiBejIQJsLR4gBM7Maz0Z87hHScs2K0AUvPBVuc49iknXm5gIJB09d%2BXwUKz5WwZC2koX05miUg19yb0QzxTnPVmKklTwKnxa36HuDobSMcuKbQY3iXHB%2BoFw9gqYMPPIytIGOqEBV9WZeZv9bxpzWlxQ26o9AHoPVBLMjjnFPilsg4EVgX2vVi55mBywdrDxI5WT9otQm9P5XFxdM6BjCEeyJKnCZrlUSYngutx7VPEQnuEThl76pWnGMk519UXj%2BdnE0%2FmDQYcmGkbAi%2Bj9Dxv3%2BwjLTkAdxTSEvH8ZOAkCJlDKM%2FeYTM8rPlsEdRg1LqRj4qAhgXIB%2B3h0w5mYn2BLbrEtgMw%3D&Expires=1783815379", "piano": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/piano.wav?AWSAccessKeyId=ASIAXOTDWH636GXADLV7&Signature=fLMS5TmSsSTW7Cno1ULfHTLm7kE%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEA0aCXVzLWVhc3QtMSJIMEYCIQDV2SkLWnNhZPuLA1%2FHAN%2FQiwgSNF2tYQZ7Q4MgMcPWaAIhAKqMEK5%2BIvmVXuvwyqJ8V23ZQQ0GMgbZvnPhWADtF7YmKpkECNX%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5IgxIXXJUEBOOcmAvixwq7QPo8mkDDKLC%2BDu952cNwkghTLtEkwGRaiuGpaOqXykGXA0FGvzUa%2FcvyqkEJjsa8BZ5siHGH3HIwqWhJPu64zCSydYNZoqnnKbyKnD5SD1a8piFk0dCMWEJ1%2Fiq0YYSZ%2B6VZEXwkfcphP6B%2Ba7LGrnpvbuU0xyyrQyh3UJufYfalNqgQjWr%2FIiOJDA3%2Fn6rt6TNUZfEbp6m7fdh%2FZJkg%2FVKi%2B2poW5DDGf9JpY8N05pVw9jYSeibqI9lEemiYhqs%2FNSsBbenlVj659aLV2X6p7SlQzZjDxC7cu44UIb3nqb%2Fi6SSBGQv7aNrHLs8SPjRF8fGU8iWFeBt2EsaPy3h6d%2FUTC73mOESdeYXoZFCM2lPvKH4EvJSZa1wM6eXDJcrGXQnuAub6uq2IlKeLPuDFaQd2MMqRDDyzV4e5NEF7AkRNn%2Fxr0DPJu2NFaG3U7PXhp7HSbnxAtTdmUsRDH6g7tQ8xA2R2qMTUN3U1Jax4iRwcFxbkPErSApJ92oe3cjCXlL91%2F1HNvrvgl9QcsBcFiqiBejIQJsLR4gBM7Maz0Z87hHScs2K0AUvPBVuc49iknXm5gIJB09d%2BXwUKz5WwZC2koX05miUg19yb0QzxTnPVmKklTwKnxa36HuDobSMcuKbQY3iXHB%2BoFw9gqYMPPIytIGOqEBV9WZeZv9bxpzWlxQ26o9AHoPVBLMjjnFPilsg4EVgX2vVi55mBywdrDxI5WT9otQm9P5XFxdM6BjCEeyJKnCZrlUSYngutx7VPEQnuEThl76pWnGMk519UXj%2BdnE0%2FmDQYcmGkbAi%2Bj9Dxv3%2BwjLTkAdxTSEvH8ZOAkCJlDKM%2FeYTM8rPlsEdRg1LqRj4qAhgXIB%2B3h0w5mYn2BLbrEtgMw%3D&Expires=1783815379", "guitar": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/guitar.wav?AWSAccessKeyId=ASIAXOTDWH636GXADLV7&Signature=%2FpOpUM38VZAvJW0zGGF2MQvdoy4%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEA0aCXVzLWVhc3QtMSJIMEYCIQDV2SkLWnNhZPuLA1%2FHAN%2FQiwgSNF2tYQZ7Q4MgMcPWaAIhAKqMEK5%2BIvmVXuvwyqJ8V23ZQQ0GMgbZvnPhWADtF7YmKpkECNX%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5IgxIXXJUEBOOcmAvixwq7QPo8mkDDKLC%2BDu952cNwkghTLtEkwGRaiuGpaOqXykGXA0FGvzUa%2FcvyqkEJjsa8BZ5siHGH3HIwqWhJPu64zCSydYNZoqnnKbyKnD5SD1a8piFk0dCMWEJ1%2Fiq0YYSZ%2B6VZEXwkfcphP6B%2Ba7LGrnpvbuU0xyyrQyh3UJufYfalNqgQjWr%2FIiOJDA3%2Fn6rt6TNUZfEbp6m7fdh%2FZJkg%2FVKi%2B2poW5DDGf9JpY8N05pVw9jYSeibqI9lEemiYhqs%2FNSsBbenlVj659aLV2X6p7SlQzZjDxC7cu44UIb3nqb%2Fi6SSBGQv7aNrHLs8SPjRF8fGU8iWFeBt2EsaPy3h6d%2FUTC73mOESdeYXoZFCM2lPvKH4EvJSZa1wM6eXDJcrGXQnuAub6uq2IlKeLPuDFaQd2MMqRDDyzV4e5NEF7AkRNn%2Fxr0DPJu2NFaG3U7PXhp7HSbnxAtTdmUsRDH6g7tQ8xA2R2qMTUN3U1Jax4iRwcFxbkPErSApJ92oe3cjCXlL91%2F1HNvrvgl9QcsBcFiqiBejIQJsLR4gBM7Maz0Z87hHScs2K0AUvPBVuc49iknXm5gIJB09d%2BXwUKz5WwZC2koX05miUg19yb0QzxTnPVmKklTwKnxa36HuDobSMcuKbQY3iXHB%2BoFw9gqYMPPIytIGOqEBV9WZeZv9bxpzWlxQ26o9AHoPVBLMjjnFPilsg4EVgX2vVi55mBywdrDxI5WT9otQm9P5XFxdM6BjCEeyJKnCZrlUSYngutx7VPEQnuEThl76pWnGMk519UXj%2BdnE0%2FmDQYcmGkbAi%2Bj9Dxv3%2BwjLTkAdxTSEvH8ZOAkCJlDKM%2FeYTM8rPlsEdRg1LqRj4qAhgXIB%2B3h0w5mYn2BLbrEtgMw%3D&Expires=1783815379", "other": "https://clouddsp-processed-audio-512383926199-us-east-1.s3.amazonaws.com/stems/2370ff2b-dbaa-4fe4-9782-a8408101d2a0-Yosemite/other.wav?AWSAccessKeyId=ASIAXOTDWH636GXADLV7&Signature=s%2FF%2FakIPaDDwixbE8xy05IJUj3E%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEA0aCXVzLWVhc3QtMSJIMEYCIQDV2SkLWnNhZPuLA1%2FHAN%2FQiwgSNF2tYQZ7Q4MgMcPWaAIhAKqMEK5%2BIvmVXuvwyqJ8V23ZQQ0GMgbZvnPhWADtF7YmKpkECNX%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNTEyMzgzOTI2MTk5IgxIXXJUEBOOcmAvixwq7QPo8mkDDKLC%2BDu952cNwkghTLtEkwGRaiuGpaOqXykGXA0FGvzUa%2FcvyqkEJjsa8BZ5siHGH3HIwqWhJPu64zCSydYNZoqnnKbyKnD5SD1a8piFk0dCMWEJ1%2Fiq0YYSZ%2B6VZEXwkfcphP6B%2Ba7LGrnpvbuU0xyyrQyh3UJufYfalNqgQjWr%2FIiOJDA3%2Fn6rt6TNUZfEbp6m7fdh%2FZJkg%2FVKi%2B2poW5DDGf9JpY8N05pVw9jYSeibqI9lEemiYhqs%2FNSsBbenlVj659aLV2X6p7SlQzZjDxC7cu44UIb3nqb%2Fi6SSBGQv7aNrHLs8SPjRF8fGU8iWFeBt2EsaPy3h6d%2FUTC73mOESdeYXoZFCM2lPvKH4EvJSZa1wM6eXDJcrGXQnuAub6uq2IlKeLPuDFaQd2MMqRDDyzV4e5NEF7AkRNn%2Fxr0DPJu2NFaG3U7PXhp7HSbnxAtTdmUsRDH6g7tQ8xA2R2qMTUN3U1Jax4iRwcFxbkPErSApJ92oe3cjCXlL91%2F1HNvrvgl9QcsBcFiqiBejIQJsLR4gBM7Maz0Z87hHScs2K0AUvPBVuc49iknXm5gIJB09d%2BXwUKz5WwZC2koX05miUg19yb0QzxTnPVmKklTwKnxa36HuDobSMcuKbQY3iXHB%2BoFw9gqYMPPIytIGOqEBV9WZeZv9bxpzWlxQ26o9AHoPVBLMjjnFPilsg4EVgX2vVi55mBywdrDxI5WT9otQm9P5XFxdM6BjCEeyJKnCZrlUSYngutx7VPEQnuEThl76pWnGMk519UXj%2BdnE0%2FmDQYcmGkbAi%2Bj9Dxv3%2BwjLTkAdxTSEvH8ZOAkCJlDKM%2FeYTM8rPlsEdRg1LqRj4qAhgXIB%2B3h0w5mYn2BLbrEtgMw%3D&Expires=1783815379"};
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
