/** Render one score page at a time without embedding a cross-origin frame. */
import React, { useEffect, useRef, useState } from 'react';
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?worker&url';

// Vite emits a same-origin worker. The existing CSP keeps frame-src/object-src
// disabled; neither an external viewer nor an unsafe-eval exception is needed.
GlobalWorkerOptions.workerSrc = workerUrl;

export default function SheetPdfPreview({ url }) {
    const [document, setDocument] = useState(null);
    const [pageNumber, setPageNumber] = useState(1);
    const [attempt, setAttempt] = useState(0);
    const [error, setError] = useState('');
    const [rendering, setRendering] = useState(true);
    const [width, setWidth] = useState(0);
    const containerRef = useRef(null);
    const canvasRef = useRef(null);

    useEffect(() => {
        let current = true;
        const controller = new AbortController();
        let loadingTask;
        setDocument(null);
        setError('');
        setRendering(true);
        setPageNumber(1);
        const load = async () => {
            try {
                // Signed private artifacts must not enter the browser HTTP
                // cache. Only the current sheet's bytes live in this viewer.
                const response = await fetch(url, { signal: controller.signal, cache: 'no-store' });
                if (!response.ok) throw new Error('PDF unavailable');
                const blob = await response.blob();
                if (blob.size > 50 * 1024 * 1024) throw new Error('PDF too large');
                const data = new Uint8Array(await blob.arrayBuffer());
                if (!current) return;
                loadingTask = getDocument({ data, isEvalSupported: false, disableFontFace: true,
                    useSystemFonts: false, enableXfa: false });
                const pdf = await loadingTask.promise;
                if (current) setDocument(pdf);
            } catch {
                if (current) {
                    setError('The score preview could not be loaded. Retry or open the PDF above.');
                    setRendering(false);
                }
            }
        };
        void load();
        return () => {
            current = false;
            controller.abort();
            // Also terminates PDF.js's worker and releases its page/font data
            // when selecting another job, switching studios, or signing out.
            void loadingTask?.destroy().catch(() => {});
        };
    }, [url, attempt]);

    useEffect(() => {
        const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
        observer.observe(containerRef.current);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (!document || !width) return undefined;
        let current = true;
        let task;
        setRendering(true);
        setError('');
        const render = async () => {
            try {
                const page = await document.getPage(pageNumber);
                if (!current) return;
                const viewport = page.getViewport({ scale: 1 });
                const cssWidth = Math.min(width, 900);
                const scale = cssWidth / viewport.width;
                const cssHeight = viewport.height * scale;
                // Bound the single canvas to four million pixels, including
                // Retina displays or unusually tall pages. Other pages stay
                // unrendered, keeping the MIDI editor's memory available.
                const density = Math.min(window.devicePixelRatio || 1, 2,
                    Math.sqrt(4_000_000 / (cssWidth * cssHeight)));
                const canvas = canvasRef.current;
                canvas.width = Math.floor(cssWidth * density);
                canvas.height = Math.floor(cssHeight * density);
                canvas.style.width = `${cssWidth}px`;
                canvas.style.height = `${cssHeight}px`;
                task = page.render({ canvasContext: canvas.getContext('2d'),
                    viewport: page.getViewport({ scale: scale * density }) });
                await task.promise;
                if (current) setRendering(false);
            } catch {
                if (current) {
                    setError('This page could not be displayed. Retry or open the PDF above.');
                    setRendering(false);
                }
            }
        };
        void render();
        return () => { current = false; task?.cancel(); };
    }, [document, pageNumber, width]);

    return <div className="score-midi-sheet-viewer" aria-busy={rendering}>
        <div className="score-midi-sheet-toolbar" aria-label="Score preview pages">
            <strong>Score preview</strong>
            <div className="score-midi-sheet-pagination">
                <button className="score-midi-secondary-button" type="button" disabled={!document || pageNumber === 1}
                    onClick={() => setPageNumber((page) => page - 1)} aria-label="Previous score page">← Previous</button>
                <span aria-live="polite">{document ? `Page ${pageNumber} of ${document.numPages}` : 'Loading PDF…'}</span>
                <button className="score-midi-secondary-button" type="button" disabled={!document || pageNumber === document.numPages}
                    onClick={() => setPageNumber((page) => page + 1)} aria-label="Next score page">Next →</button>
            </div>
        </div>
        {error ? <p className="score-midi-error" role="alert">{error}{' '}
            <button className="score-midi-secondary-button" type="button" onClick={() => setAttempt((value) => value + 1)}>Retry preview</button>
        </p> : rendering && <p className="score-midi-sheet-loading" role="status">Rendering score preview…</p>}
        <div className="score-midi-sheet-canvas" ref={containerRef}>
            <canvas ref={canvasRef} hidden={!document || rendering || Boolean(error)} role="img"
                aria-label={`Rendered score, page ${pageNumber}${document ? ` of ${document.numPages}` : ''}`} />
        </div>
    </div>;
}
