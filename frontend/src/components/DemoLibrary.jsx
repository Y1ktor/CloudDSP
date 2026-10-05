/** Show the public example picker and let visitors open a demo in the workspace. */
import React from 'react';

export default function DemoLibrary({ isOpen, jobs, activeDemoId, onSelect, onClose }) {
    if (!isOpen) return null;

    return (
        <div
            role="presentation"
            onMouseDown={onClose}
            style={overlayStyle}
        >
            <section
                role="dialog"
                aria-modal="true"
                aria-labelledby="demo-library-title"
                onMouseDown={(event) => event.stopPropagation()}
                style={dialogStyle}
            >
                <header style={headerStyle}>
                    <div>
                        <div style={eyebrowStyle}>No account required</div>
                        <h2 id="demo-library-title" style={titleStyle}>Try a CloudDSP example</h2>
                        <p style={descriptionStyle}>
                            Explore separated audio and edit generated MIDI. Your changes stay in this browser and never alter the public demo files.
                        </p>
                    </div>
                    <button type="button" onClick={onClose} aria-label="Close examples" style={closeButtonStyle}>×</button>
                </header>

                <div style={listStyle}>
                    {jobs.map((job) => {
                        const isActive = job.id === activeDemoId;
                        return (
                            <button
                                key={job.id}
                                type="button"
                                onClick={() => onSelect(job)}
                                aria-pressed={isActive}
                                style={{ ...jobButtonStyle, ...(isActive ? activeJobButtonStyle : {}) }}
                            >
                                <span style={jobTextStyle}>
                                    <strong style={jobTitleStyle}>{job.title}</strong>
                                    <span style={jobDescriptionStyle}>{job.description || `${job.stemMode} audio and MIDI example`}</span>
                                </span>
                                <span style={{ ...openLabelStyle, ...(isActive ? activeOpenLabelStyle : {}) }}>
                                    {isActive ? 'Open' : 'Load'}
                                </span>
                            </button>
                        );
                    })}
                </div>

                <footer style={footerStyle}>
                    Sign in when you are ready to upload, process, save, or delete your own projects.
                </footer>
            </section>
        </div>
    );
}

const overlayStyle = {
    position: 'fixed', inset: 0, zIndex: 1900, display: 'grid', placeItems: 'center',
    padding: '24px', background: 'rgba(37, 52, 70, 0.28)', backdropFilter: 'blur(6px)',
};
const dialogStyle = {
    boxSizing: 'border-box', width: 'min(100%, 620px)', maxHeight: 'min(720px, 90vh)',
    display: 'flex', flexDirection: 'column', overflow: 'hidden', border: '1px solid var(--studio-border)',
    borderRadius: '10px', background: 'var(--studio-surface)', color: 'var(--studio-text)', boxShadow: '0 24px 70px rgba(44, 62, 80, 0.22)',
};
const headerStyle = { position: 'relative', padding: '25px 58px 20px 25px', borderBottom: '1px solid var(--studio-border)' };
const eyebrowStyle = { color: 'var(--studio-midi)', fontSize: '11px', fontWeight: '800', letterSpacing: '0.12em', textTransform: 'uppercase' };
const titleStyle = { margin: '6px 0 7px', fontSize: '23px' };
const descriptionStyle = { margin: 0, maxWidth: '500px', color: 'var(--studio-text-secondary)', fontSize: '13px', lineHeight: 1.55 };
const closeButtonStyle = { position: 'absolute', top: '16px', right: '19px', border: 0, background: 'transparent', color: 'var(--studio-text-muted)', cursor: 'pointer', fontSize: '26px' };
const listStyle = { display: 'grid', gap: '9px', padding: '17px', overflowY: 'auto' };
const jobButtonStyle = {
    width: '100%', minHeight: '70px', display: 'flex', alignItems: 'center', gap: '16px',
    padding: '13px 15px', border: '1px solid var(--studio-border)', borderRadius: '7px',
    background: 'var(--studio-surface-muted)', color: 'var(--studio-text)', textAlign: 'left', cursor: 'pointer',
};
const activeJobButtonStyle = { borderColor: '#9dcfb1', background: 'var(--studio-midi-soft)', boxShadow: 'inset 3px 0 var(--studio-midi)' };
const jobTextStyle = { minWidth: 0, flex: 1, display: 'grid', gap: '5px' };
const jobTitleStyle = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '14px' };
const jobDescriptionStyle = { color: 'var(--studio-text-muted)', fontSize: '12px', lineHeight: 1.4 };
const openLabelStyle = { padding: '5px 10px', border: '1px solid var(--studio-border-strong)', borderRadius: '5px', color: 'var(--studio-text-secondary)', fontSize: '11px', fontWeight: '800' };
const activeOpenLabelStyle = { borderColor: '#9dcfb1', color: '#217248' };
const footerStyle = { padding: '13px 20px', borderTop: '1px solid var(--studio-border)', color: 'var(--studio-text-muted)', fontSize: '12px', lineHeight: 1.45 };
