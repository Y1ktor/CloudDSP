import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import './WelcomeTutorial.css';

/*
 * When the final workstation screenshots are ready, place them in
 * `public/tutorial/` and set `imageSrc` on the first two slides. Until then,
 * these lightweight previews keep the layout complete without issuing a
 * request for a missing asset.
 */
const TUTORIAL_SLIDES = [
    {
        id: 'workspace',
        eyebrow: 'Your workspace',
        title: 'See the whole song take shape',
        description: 'The main canvas keeps the original recording, separated stems, and generated MIDI on one synchronized timeline.',
        details: [
            'Use the transport to play, pause, seek, and follow the arrangement.',
            'Mute, solo, and balance each stem without losing synchronization.',
            'Switch MIDI playback on when you want to hear the extracted notes.',
        ],
        imageSrc: null,
        imageAlt: 'CloudDSP multitrack workstation with audio and MIDI tracks',
        preview: 'workspace',
        placeholderLabel: 'Main canvas screenshot',
    },
    {
        id: 'editor',
        eyebrow: 'MIDI editor',
        title: 'Open the music inside a stem',
        description: 'Double-click a MIDI lane to open the focused piano-roll editor and work with individual notes.',
        details: [
            'Move, resize, add, or remove notes directly on the grid.',
            'Audition drum voices independently with subtrack mute and solo controls.',
            'Revert changes safely or download the result when the edit is ready.',
        ],
        imageSrc: null,
        imageAlt: 'CloudDSP popup MIDI editor with a piano roll',
        preview: 'editor',
        placeholderLabel: 'Popup editor screenshot',
    },
    {
        id: 'finish',
        eyebrow: 'Ready when you are',
        title: 'Turn your own tracks into a workspace',
        description: 'Explore the public examples now, then create an account when you are ready to process and save personal projects.',
        details: [
            'Upload audio or import a supported media link.',
            'Return to saved projects from your private job history.',
            'Keep stems, MIDI, tempo, and downloads together for 14 days.',
        ],
        preview: 'finish',
    },
];

function ArrowIcon({ direction }) {
    return (
        <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" fill="none">
            <path
                d={direction === 'left' ? 'M15 5 8 12l7 7' : 'm9 5 7 7-7 7'}
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
            />
        </svg>
    );
}

function WorkspacePreview({ label }) {
    return (
        <div className="welcome-tutorial__placeholder" aria-label={label} role="img">
            <div className="welcome-tutorial__mock-window">
                <div className="welcome-tutorial__mock-toolbar">
                    <span />
                    <span />
                    <span />
                    <b />
                </div>
                <div className="welcome-tutorial__mock-transport">
                    <i />
                    <i />
                    <em />
                    <strong />
                </div>
                <div className="welcome-tutorial__mock-timeline">
                    {['original', 'vocals', 'drums', 'midi'].map((track, index) => (
                        <div className={`welcome-tutorial__mock-track welcome-tutorial__mock-track--${track}`} key={track}>
                            <span>{index + 1}</span>
                            <i />
                        </div>
                    ))}
                </div>
            </div>
            <span className="welcome-tutorial__placeholder-caption">{label}</span>
        </div>
    );
}

function EditorPreview({ label }) {
    return (
        <div className="welcome-tutorial__placeholder" aria-label={label} role="img">
            <div className="welcome-tutorial__mock-editor">
                <div className="welcome-tutorial__mock-editor-title"><span /><b /></div>
                <div className="welcome-tutorial__mock-piano">
                    <div className="welcome-tutorial__mock-keys">
                        {Array.from({ length: 8 }, (_, index) => <i key={index} />)}
                    </div>
                    <div className="welcome-tutorial__mock-notes">
                        <i style={{ '--note-x': '9%', '--note-y': '18%', '--note-w': '21%' }} />
                        <i style={{ '--note-x': '35%', '--note-y': '34%', '--note-w': '15%' }} />
                        <i style={{ '--note-x': '54%', '--note-y': '50%', '--note-w': '25%' }} />
                        <i style={{ '--note-x': '70%', '--note-y': '68%', '--note-w': '18%' }} />
                    </div>
                </div>
            </div>
            <span className="welcome-tutorial__placeholder-caption">{label}</span>
        </div>
    );
}

function FinishPreview() {
    return (
        <div className="welcome-tutorial__finish-visual" aria-hidden="true">
            <div className="welcome-tutorial__finish-mark">
                <svg viewBox="0 0 64 64" width="58" height="58" fill="none">
                    <path d="M19 33.5 28.5 43 47 23" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
            </div>
            <div className="welcome-tutorial__finish-lines">
                <span />
                <span />
                <span />
            </div>
        </div>
    );
}

function TutorialMedia({ slide }) {
    if (slide.imageSrc) {
        return <img className="welcome-tutorial__image" src={slide.imageSrc} alt={slide.imageAlt} />;
    }
    if (slide.preview === 'workspace') return <WorkspacePreview label={slide.placeholderLabel} />;
    if (slide.preview === 'editor') return <EditorPreview label={slide.placeholderLabel} />;
    return <FinishPreview />;
}

export default function WelcomeTutorial({ enabled = true, delayMs = 2400 }) {
    const location = useLocation();
    const dialogRef = useRef(null);
    const restoreFocusRef = useRef(null);
    // Dismiss only for this document. A page refresh deliberately offers the
    // tutorial again so its layout and final screenshots remain easy to review.
    const [isDismissed, setIsDismissed] = useState(false);
    const [isOpen, setIsOpen] = useState(false);
    const [activeSlide, setActiveSlide] = useState(0);
    const [direction, setDirection] = useState('forward');

    useEffect(() => {
        if (!enabled || isDismissed || location.pathname !== '/') {
            setIsOpen(false);
            return undefined;
        }

        const timer = window.setTimeout(() => setIsOpen(true), delayMs);
        return () => window.clearTimeout(timer);
    }, [delayMs, enabled, isDismissed, location.pathname]);

    useEffect(() => {
        if (!isOpen) return undefined;

        restoreFocusRef.current = document.activeElement;
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        window.requestAnimationFrame(() => dialogRef.current?.focus());

        return () => {
            document.body.style.overflow = previousOverflow;
            if (restoreFocusRef.current instanceof HTMLElement) restoreFocusRef.current.focus();
        };
    }, [isOpen]);

    const complete = useCallback(() => {
        setIsDismissed(true);
        setIsOpen(false);
    }, []);

    const goToSlide = useCallback((nextIndex) => {
        const boundedIndex = Math.max(0, Math.min(TUTORIAL_SLIDES.length - 1, nextIndex));
        setDirection(boundedIndex < activeSlide ? 'backward' : 'forward');
        setActiveSlide(boundedIndex);
    }, [activeSlide]);

    const goForward = useCallback(() => {
        if (activeSlide === TUTORIAL_SLIDES.length - 1) {
            complete();
            return;
        }
        setDirection('forward');
        setActiveSlide((current) => current + 1);
    }, [activeSlide, complete]);

    const handleKeyDown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            complete();
            return;
        }
        if (event.key === 'ArrowLeft' && activeSlide > 0) {
            event.preventDefault();
            goToSlide(activeSlide - 1);
            return;
        }
        if (event.key === 'ArrowRight') {
            event.preventDefault();
            goForward();
            return;
        }
        if (event.key !== 'Tab') return;

        const focusable = dialogRef.current?.querySelectorAll('button:not(:disabled)');
        if (!focusable?.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    };

    if (!isOpen) return null;

    const slide = TUTORIAL_SLIDES[activeSlide];
    const isLastSlide = activeSlide === TUTORIAL_SLIDES.length - 1;

    return (
        <div className="welcome-tutorial__overlay" role="presentation">
            <section
                ref={dialogRef}
                className="welcome-tutorial"
                role="dialog"
                aria-modal="true"
                aria-labelledby="welcome-tutorial-title"
                aria-describedby="welcome-tutorial-description"
                tabIndex={-1}
                onKeyDown={handleKeyDown}
            >
                <button type="button" className="welcome-tutorial__skip" onClick={complete}>
                    Skip tutorial
                </button>

                <button
                    type="button"
                    className="welcome-tutorial__arrow welcome-tutorial__arrow--left"
                    onClick={() => goToSlide(activeSlide - 1)}
                    disabled={activeSlide === 0}
                    aria-label="Previous tutorial slide"
                >
                    <ArrowIcon direction="left" />
                </button>

                <div
                    key={`${slide.id}-${direction}`}
                    className={`welcome-tutorial__slide welcome-tutorial__slide--${direction}`}
                >
                    <div className="welcome-tutorial__media">
                        <TutorialMedia slide={slide} />
                    </div>

                    <div className="welcome-tutorial__copy" aria-live="polite">
                        <span className="welcome-tutorial__eyebrow">{slide.eyebrow}</span>
                        <h2 id="welcome-tutorial-title">{slide.title}</h2>
                        <p id="welcome-tutorial-description">{slide.description}</p>
                        <ul>
                            {slide.details.map((detail) => <li key={detail}>{detail}</li>)}
                        </ul>
                        {isLastSlide && (
                            <button type="button" className="welcome-tutorial__finish" onClick={complete}>
                                Start exploring
                            </button>
                        )}
                    </div>
                </div>

                <button
                    type="button"
                    className="welcome-tutorial__arrow welcome-tutorial__arrow--right"
                    onClick={goForward}
                    aria-label={isLastSlide ? 'Finish tutorial' : 'Next tutorial slide'}
                >
                    <ArrowIcon direction="right" />
                </button>

                <nav className="welcome-tutorial__dots" aria-label="Tutorial slides">
                    {TUTORIAL_SLIDES.map((item, index) => (
                        <button
                            key={item.id}
                            type="button"
                            className={index === activeSlide ? 'welcome-tutorial__dot welcome-tutorial__dot--active' : 'welcome-tutorial__dot'}
                            onClick={() => goToSlide(index)}
                            aria-label={`Go to slide ${index + 1}: ${item.title}`}
                            aria-current={index === activeSlide ? 'step' : undefined}
                        />
                    ))}
                </nav>
                <span className="welcome-tutorial__counter" aria-hidden="true">
                    {activeSlide + 1} / {TUTORIAL_SLIDES.length}
                </span>
            </section>
        </div>
    );
}
