import React, { useEffect, useRef, useState } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import EqPage from './EqPage';
import StemSplitter from './components/StemSplitter/StemSplitter';

const WEBSOCKET_URL = import.meta.env.VITE_WEBSOCKET_URL ||
    'wss://grreq325rk.execute-api.us-east-1.amazonaws.com/dev';
const UPLOAD_URL_API = import.meta.env.VITE_UPLOAD_URL_API ||
    'https://6ec8xwsshl.execute-api.us-east-1.amazonaws.com/upload-url';

function NavBar() {
    const location = useLocation();
    const navStyle = {
        background: '#222', padding: '15px 20px', display: 'flex', gap: '20px',
        borderBottom: '1px solid #444', marginBottom: '40px', alignItems: 'center',
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)'
    };
    const getLinkStyle = (path) => ({
        color: location.pathname === path ? '#fff' : '#aaa',
        textDecoration: 'none', fontWeight: 'bold', padding: '8px 16px',
        borderRadius: '4px', background: location.pathname === path ? '#4CAF50' : 'transparent',
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
    const [stemFile, setStemFile] = useState(null);
    const [stemFileName, setStemFileName] = useState('No file loaded');
    const [splitMode, setSplitMode] = useState('6-stems');
    const [isSplitting, setIsSplitting] = useState(false);
    const [statusMessage, setStatusMessage] = useState('');
    const [stemUrls, setStemUrls] = useState(null);
    const [midiUrls, setMidiUrls] = useState(null);
    const [errorMsg, setErrorMsg] = useState('');
    const [awsConnectionId, setAwsConnectionId] = useState(null);

    const socketRef = useRef(null);
    const pendingMidiStemsRef = useRef(new Set());
    const receivedMidiStemsRef = useRef(new Set());

    useEffect(() => () => socketRef.current?.close(), []);

    const connectWebSocket = React.useCallback(() => {
        const socket = socketRef.current;
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
            return;
        }

        console.log('Opening CloudDSP WebSocket connection.');
        const nextSocket = new WebSocket(WEBSOCKET_URL);
        socketRef.current = nextSocket;

        nextSocket.onopen = () => {
            console.log('CloudDSP WebSocket connected.');
            nextSocket.send(JSON.stringify({ action: 'echo' }));
        };

        nextSocket.onerror = (error) => {
            console.error('WebSocket error:', error);
            setErrorMsg('Failed to connect to the CloudDSP WebSocket server.');
        };

        nextSocket.onclose = () => {
            console.log('CloudDSP WebSocket closed.');
            setAwsConnectionId(null);
        };

        nextSocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.type === 'connected') {
                    setAwsConnectionId(data.connectionId);
                    console.log('Received CloudDSP WebSocket connection ID.');
                    return;
                }

                // CloudStemSplit emits processing_complete/stems. The Batch EventBridge
                // notifier uses stems_ready/urls, so support both during the migration.
                if (data.type === 'processing_complete' || data.type === 'stems_ready') {
                    const urls = data.stems || data.urls;
                    if (!urls || Object.keys(urls).length === 0) {
                        throw new Error('The backend completed without any stem URLs.');
                    }

                    const stemNames = Object.keys(urls);
                    pendingMidiStemsRef.current = new Set(stemNames);
                    receivedMidiStemsRef.current.forEach((stemName) => {
                        pendingMidiStemsRef.current.delete(stemName);
                    });
                    const remaining = pendingMidiStemsRef.current.size;
                    setStemUrls(urls);
                    setIsSplitting(false);
                    setStatusMessage(
                        remaining > 0
                            ? `Stems ready. Extracting MIDI for ${remaining} stem(s)...`
                            : 'Stems and MIDI extraction complete.'
                    );
                    console.log('Received presigned stem URLs:', stemNames);
                    return;
                }

                if (data.type === 'midi_processing_complete') {
                    if (!data.stem_name || !data.midi_url) {
                        throw new Error('The MIDI completion event is missing a stem name or MIDI URL.');
                    }

                    setMidiUrls((current) => ({
                        ...(current || {}),
                        [data.stem_name]: data.midi_url,
                    }));
                    receivedMidiStemsRef.current.add(data.stem_name);
                    pendingMidiStemsRef.current.delete(data.stem_name);
                    const remaining = pendingMidiStemsRef.current.size;
                    setStatusMessage(
                        remaining > 0
                            ? `MIDI ready for ${data.stem_name}. Waiting for ${remaining} stem(s)...`
                            : 'MIDI extraction complete.'
                    );
                    console.log(`Received presigned MIDI URL for ${data.stem_name}.`);
                    return;
                }

                if (data.type === 'status') {
                    setStatusMessage(data.message || 'Processing...');
                    return;
                }

                if (data.type === 'extraction_complete') {
                    setStatusMessage('Extraction complete. Waiting for stem processing...');
                    return;
                }

                if (data.type === 'error') {
                    setErrorMsg(data.message || 'An AWS backend error occurred.');
                    setIsSplitting(false);
                }
            } catch (error) {
                console.error('Failed to handle WebSocket message:', error);
                setErrorMsg(error.message || 'Received an invalid backend message.');
                setIsSplitting(false);
            }
        };
    }, []);

    const executeLinkExtraction = (url) => {
        if (!awsConnectionId || socketRef.current?.readyState !== WebSocket.OPEN) {
            setErrorMsg('Still establishing a secure connection to AWS. Please try again in a moment.');
            connectWebSocket();
            return;
        }

        setIsSplitting(true);
        setErrorMsg('');
        setStatusMessage('Sending link to CloudDSP...');
        setStemUrls(null);
        setMidiUrls(null);
        pendingMidiStemsRef.current.clear();
        receivedMidiStemsRef.current.clear();
        socketRef.current.send(JSON.stringify({ action: 'yt-dlp', url, stemMode: splitMode }));
    };

    const executeStemSplit = async () => {
        if (!stemFile) {
            setErrorMsg('Please select a file first.');
            return;
        }
        if (!awsConnectionId || socketRef.current?.readyState !== WebSocket.OPEN) {
            setErrorMsg('Still establishing a secure connection to AWS. Please try again in a moment.');
            connectWebSocket();
            return;
        }

        setIsSplitting(true);
        setErrorMsg('');
        setStemUrls(null);
        setMidiUrls(null);
        pendingMidiStemsRef.current.clear();
        receivedMidiStemsRef.current.clear();

        try {
            const fileType = stemFile.type || 'application/octet-stream';
            const query = new URLSearchParams({
                filename: stemFileName,
                filetype: fileType,
                connectionId: awsConnectionId,
                stemMode: splitMode,
            });

            setStatusMessage('Requesting a secure S3 upload URL...');
            const uploadUrlResponse = await fetch(`${UPLOAD_URL_API}?${query.toString()}`);
            if (!uploadUrlResponse.ok) {
                throw new Error(`Upload URL request failed (${uploadUrlResponse.status}).`);
            }
            const uploadDetails = await uploadUrlResponse.json();
            if (!uploadDetails.uploadUrl) {
                throw new Error(uploadDetails.error || 'The upload service did not return an upload URL.');
            }

            setStatusMessage('Uploading audio to S3...');
            const uploadResponse = await fetch(uploadDetails.uploadUrl, {
                method: 'PUT',
                body: stemFile,
                headers: {
                    'Content-Type': fileType,
                    'x-amz-meta-connection-id': awsConnectionId,
                    'x-amz-meta-stem-mode': splitMode,
                },
            });
            if (!uploadResponse.ok) {
                throw new Error(`S3 upload failed (${uploadResponse.status}). Check the upload bucket CORS policy.`);
            }

            // The successful PUT creates the S3 event. EventBridge submits the
            // Demucs job; its WebSocket messages drive the remaining UI state.
            setStatusMessage('Upload complete. Waiting for AWS Batch stem processing...');
            console.log('Audio uploaded to S3; waiting for backend stem and MIDI events.');
        } catch (error) {
            console.error('Audio upload failed:', error);
            setErrorMsg(error.message || 'Failed to upload audio to CloudDSP.');
            setIsSplitting(false);
        }
    };

    const stemProps = {
        file: stemFile, setFile: setStemFile,
        fileName: stemFileName, setFileName: setStemFileName,
        splitMode, setSplitMode,
        isSplitting, statusMessage, stemUrls, midiUrls, errorMsg,
        setErrorMsg, setStemUrls, setMidiUrls,
        executeStemSplit, executeLinkExtraction, connectWebSocket,
    };

    return (
        <BrowserRouter>
            <div style={{ minHeight: '100vh' }}>
                <NavBar />
                <Routes>
                    <Route path="/" element={<EqPage />} />
                    <Route path="/stems" element={<div style={{ display: 'flex', justifyContent: 'center' }}><StemSplitter {...stemProps} /></div>} />
                </Routes>
            </div>
        </BrowserRouter>
    );
}
