import React from 'react';
import JSZip from 'jszip';

function triggerBlobDownload(blob, filename) {
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

async function fetchArtifactBlob(artifact) {
    if (artifact.file instanceof Blob) return artifact.file;

    const response = await fetch(artifact.url);
    if (!response.ok) {
        throw new Error(`${artifact.filename} could not be downloaded (HTTP ${response.status}).`);
    }
    return response.blob();
}

async function fetchArtifactSize(artifact) {
    if (artifact.file instanceof Blob) return artifact.file.size;

    // A one-byte range response gives the immutable object's total byte count
    // without pre-downloading the potentially large audio artifact. S3 CORS
    // exposes Content-Range for the configured frontend origins.
    const response = await fetch(artifact.url, { headers: { Range: 'bytes=0-0' } });
    const contentRange = response.headers.get('Content-Range');
    const contentLength = response.headers.get('Content-Length');
    response.body?.cancel();

    const totalMatch = contentRange?.match(/\/(\d+)$/);
    if (totalMatch) return Number(totalMatch[1]);
    if (response.ok && contentLength && Number.isFinite(Number(contentLength))) {
        return Number(contentLength);
    }
    throw new Error(`Could not determine the size of ${artifact.filename}.`);
}

function formatFileSize(size) {
    if (!Number.isFinite(size) || size < 0) return null;
    if (size < 1024) return `${size} B`;
    const units = ['KB', 'MB', 'GB'];
    const unitIndex = Math.min(Math.floor(Math.log(size) / Math.log(1024)) - 1, units.length - 1);
    const value = size / (1024 ** (unitIndex + 1));
    return `${value.toFixed(value >= 100 ? 0 : 1)}${units[unitIndex]}`;
}

/**
 * Lets the user select durable job artifacts and download either a ZIP that
 * preserves the DAW hierarchy or the selected artifacts as flat files.
 */
export default function DownloadPopup({
    rootFolderName,
    artifacts,
    selectedArtifactIds,
    setSelectedArtifactIds,
    onClose,
}) {
    const [isDownloading, setIsDownloading] = React.useState(false);
    const [downloadStatus, setDownloadStatus] = React.useState('');
    const [downloadError, setDownloadError] = React.useState('');
    const [artifactSizes, setArtifactSizes] = React.useState({});
    const sizeCacheRef = React.useRef(new Map());

    const selectedArtifacts = artifacts.filter((artifact) => selectedArtifactIds.has(artifact.id));
    const originalArtifacts = artifacts.filter((artifact) => artifact.group === 'original');
    const stemArtifacts = artifacts.filter((artifact) => artifact.group === 'stems');
    const midiArtifacts = artifacts.filter((artifact) => artifact.group === 'midi');
    const unknownSelectedSizeCount = selectedArtifacts.filter((artifact) => !Number.isFinite(artifactSizes[artifact.id])).length;
    const selectedSize = selectedArtifacts.reduce(
        (total, artifact) => total + (Number.isFinite(artifactSizes[artifact.id]) ? artifactSizes[artifact.id] : 0),
        0,
    );

    React.useEffect(() => {
        let cancelled = false;
        const localSizes = {};
        const missingArtifacts = artifacts.filter((artifact) => {
            if (artifact.file instanceof Blob) {
                localSizes[artifact.id] = artifact.file.size;
                return false;
            }
            if (sizeCacheRef.current.has(artifact.id)) {
                localSizes[artifact.id] = sizeCacheRef.current.get(artifact.id);
                return false;
            }
            return true;
        });

        setArtifactSizes((previous) => ({ ...previous, ...localSizes }));
        if (missingArtifacts.length === 0) return undefined;

        Promise.all(missingArtifacts.map(async (artifact) => {
            try {
                const size = await fetchArtifactSize(artifact);
                sizeCacheRef.current.set(artifact.id, size);
                return [artifact.id, size];
            } catch (error) {
                console.warn(`[CloudDSP] Could not determine download size for '${artifact.filename}':`, error);
                return null;
            }
        })).then((resolvedSizes) => {
            if (cancelled) return;
            const updates = Object.fromEntries(resolvedSizes.filter(Boolean));
            if (Object.keys(updates).length > 0) {
                setArtifactSizes((previous) => ({ ...previous, ...updates }));
            }
        });

        return () => {
            cancelled = true;
        };
    }, [artifacts]);

    const toggleArtifact = (artifactId) => {
        if (isDownloading) return;
        setSelectedArtifactIds((previous) => {
            const next = new Set(previous);
            if (next.has(artifactId)) next.delete(artifactId);
            else next.add(artifactId);
            return next;
        });
    };

    const downloadAsFolder = async () => {
        if (selectedArtifacts.length === 0 || isDownloading) return;

        setIsDownloading(true);
        setDownloadError('');
        try {
            const zip = new JSZip();
            for (let index = 0; index < selectedArtifacts.length; index += 1) {
                const artifact = selectedArtifacts[index];
                setDownloadStatus(`Adding ${index + 1} of ${selectedArtifacts.length}: ${artifact.filename}`);
                zip.file(artifact.archivePath, await fetchArtifactBlob(artifact));
            }
            setDownloadStatus('Creating ZIP archive…');
            const archive = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 6 } });
            triggerBlobDownload(archive, `${rootFolderName}.zip`);
            setDownloadStatus('Download started.');
        } catch (error) {
            console.error('[CloudDSP] Artifact download failed:', error);
            setDownloadError(error.message || 'The selected files could not be downloaded. Refresh the job to obtain new links and try again.');
        } finally {
            setIsDownloading(false);
        }
    };

    const downloadFilesOnly = async () => {
        if (selectedArtifacts.length === 0 || isDownloading) return;

        setIsDownloading(true);
        setDownloadError('');
        try {
            for (let index = 0; index < selectedArtifacts.length; index += 1) {
                const artifact = selectedArtifacts[index];
                setDownloadStatus(`Downloading ${index + 1} of ${selectedArtifacts.length}: ${artifact.filename}`);
                triggerBlobDownload(await fetchArtifactBlob(artifact), artifact.filename);
            }
            setDownloadStatus('Downloads started. Your browser may ask permission to download multiple files.');
        } catch (error) {
            console.error('[CloudDSP] Artifact download failed:', error);
            setDownloadError(error.message || 'The selected files could not be downloaded. Refresh the job to obtain new links and try again.');
        } finally {
            setIsDownloading(false);
        }
    };

    const renderFile = (artifact, indent) => (
        <label
            key={artifact.id}
            style={{
                display: 'flex', alignItems: 'center', gap: '9px', minHeight: '34px',
                padding: `5px 10px 5px ${indent}px`, borderRadius: '5px', cursor: isDownloading ? 'default' : 'pointer',
                color: '#d8e0ea', borderTop: '1px solid rgba(148, 168, 187, 0.2)',
                background: selectedArtifactIds.has(artifact.id) ? '#294d6b' : 'transparent',
                boxShadow: selectedArtifactIds.has(artifact.id) ? 'inset 3px 0 #76b9ee' : 'none',
            }}
        >
            <span aria-hidden="true" style={{ color: '#aebccc', fontSize: '14px' }}>▤</span>
            <span style={{ flexGrow: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '13px' }}>
                {artifact.filename}
            </span>
            <input
                className="download-artifact-checkbox"
                type="checkbox"
                checked={selectedArtifactIds.has(artifact.id)}
                onChange={() => toggleArtifact(artifact.id)}
                disabled={isDownloading}
                aria-label={`Select ${artifact.filename}`}
            />
        </label>
    );

    const renderFolder = (label, children) => children.length > 0 && (
        <React.Fragment key={label}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minHeight: '33px', padding: '3px 10px 3px 34px', color: '#e8c878', fontSize: '13px', fontWeight: '700' }}>
                <span aria-hidden="true">▾</span>
                <span aria-hidden="true">▰</span>
                <span>{label}</span>
            </div>
            {children.map((artifact) => renderFile(artifact, 58))}
        </React.Fragment>
    );

    return (
        <div
            role="presentation"
            onMouseDown={() => !isDownloading && onClose()}
            style={{
                position: 'fixed', inset: 0, zIndex: 10000, display: 'flex', alignItems: 'center', justifyContent: 'center',
                padding: '20px', background: 'rgba(0, 0, 0, 0.7)',
            }}
        >
            <section
                role="dialog"
                aria-modal="true"
                aria-labelledby="download-artifacts-title"
                onMouseDown={(event) => event.stopPropagation()}
                style={{
                    width: 'min(640px, 100%)', maxHeight: 'min(680px, 90vh)', display: 'flex', flexDirection: 'column',
                    color: '#fff', background: '#20252b', border: '1px solid #4b5663', borderRadius: '9px',
                    boxShadow: '0 18px 60px rgba(0, 0, 0, 0.65)', overflow: 'hidden',
                }}
            >
                <style>{`
                    .download-artifact-checkbox {
                        appearance: none;
                        -webkit-appearance: none;
                        width: 17px;
                        height: 17px;
                        margin: 0;
                        border: 1px solid #8fa7bd;
                        border-radius: 50%;
                        background: #182029;
                        cursor: pointer;
                        display: grid;
                        place-content: center;
                        flex: 0 0 auto;
                    }
                    .download-artifact-checkbox::before {
                        content: '';
                        width: 8px;
                        height: 8px;
                        border-radius: 50%;
                        transform: scale(0);
                        transition: transform 120ms ease-out;
                        background: #dff0ff;
                    }
                    .download-artifact-checkbox:checked {
                        background: #4f94d4;
                        border-color: #79b5eb;
                    }
                    .download-artifact-checkbox:checked::before { transform: scale(1); }
                    .download-artifact-checkbox:focus-visible { outline: 2px solid #70b4ef; outline-offset: 2px; }
                    .download-artifact-checkbox:disabled { cursor: wait; opacity: 0.6; }
                `}</style>

                <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '17px 20px', borderBottom: '1px solid #3d4650' }}>
                    <div>
                        <h3 id="download-artifacts-title" style={{ margin: 0, fontSize: '17px' }}>Download project files</h3>
                        <p style={{ margin: '4px 0 0', color: '#aeb9c6', fontSize: '12px' }}>Select the original audio, stems, and MIDI files to include.</p>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={isDownloading}
                        aria-label="Close download dialog"
                        style={{ background: 'transparent', border: 'none', color: '#bac4d2', fontSize: '25px', lineHeight: 1, cursor: isDownloading ? 'wait' : 'pointer' }}
                    >×</button>
                </header>

                <div style={{ overflowY: 'auto', padding: '12px 10px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minHeight: '35px', padding: '3px 10px', color: '#f0ce77', fontSize: '14px', fontWeight: '700' }}>
                        <span aria-hidden="true">▾</span>
                        <span aria-hidden="true">▰</span>
                        <span>{rootFolderName}</span>
                    </div>
                    {originalArtifacts.map((artifact) => renderFile(artifact, 34))}
                    {renderFolder('stems', stemArtifacts)}
                    {renderFolder('midi', midiArtifacts)}
                </div>

                <footer style={{ padding: '14px 20px 16px', borderTop: '1px solid #3d4650', background: '#1b2026' }}>
                    {downloadError && <div role="alert" style={{ marginBottom: '10px', color: '#ffaaa5', fontSize: '12px' }}>{downloadError}</div>}
                    {downloadStatus && <div aria-live="polite" style={{ marginBottom: '10px', color: '#b9d9f5', fontSize: '12px' }}>{downloadStatus}</div>}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
                        <span style={{ color: '#aeb9c6', fontSize: '12px' }}>
                            {selectedArtifacts.length} file{selectedArtifacts.length === 1 ? '' : 's'} selected
                            {selectedArtifacts.length > 0 && (
                                unknownSelectedSizeCount > 0
                                    ? ` · calculating size${unknownSelectedSizeCount === 1 ? '' : 's'}…`
                                    : ` · ${formatFileSize(selectedSize)}`
                            )}
                        </span>
                        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                            <button
                                type="button"
                                onClick={downloadFilesOnly}
                                disabled={isDownloading || selectedArtifacts.length === 0}
                                style={{ padding: '8px 11px', color: '#dbe8f3', background: '#35404b', border: '1px solid #566574', borderRadius: '5px', cursor: isDownloading || selectedArtifacts.length === 0 ? 'not-allowed' : 'pointer', fontSize: '12px', fontWeight: '700', opacity: selectedArtifacts.length === 0 ? 0.55 : 1 }}
                            >Download files only</button>
                            <button
                                type="button"
                                onClick={downloadAsFolder}
                                disabled={isDownloading || selectedArtifacts.length === 0}
                                style={{ padding: '8px 11px', color: '#fff', background: '#347cbd', border: '1px solid #65a7df', borderRadius: '5px', cursor: isDownloading || selectedArtifacts.length === 0 ? 'not-allowed' : 'pointer', fontSize: '12px', fontWeight: '700', opacity: selectedArtifacts.length === 0 ? 0.55 : 1 }}
                            >Download as folder (.zip)</button>
                        </div>
                    </div>
                </footer>
            </section>
        </div>
    );
}
