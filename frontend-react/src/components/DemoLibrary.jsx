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
    padding: '24px', background: 'rgba(5, 9, 13, 0.76)', backdropFilter: 'blur(6px)',
};
const dialogStyle = {
    boxSizing: 'border-box', width: 'min(100%, 620px)', maxHeight: 'min(720px, 90vh)',
    display: 'flex', flexDirection: 'column', overflow: 'hidden', border: '1px solid #3c4855',
    borderRadius: '10px', background: '#182028', color: '#edf4fa', boxShadow: '0 24px 70px rgba(0,0,0,0.6)',
};
const headerStyle = { position: 'relative', padding: '25px 58px 20px 25px', borderBottom: '1px solid #34414d' };
const eyebrowStyle = { color: '#83c991', fontSize: '11px', fontWeight: '800', letterSpacing: '0.12em', textTransform: 'uppercase' };
const titleStyle = { margin: '6px 0 7px', fontSize: '23px' };
const descriptionStyle = { margin: 0, maxWidth: '500px', color: '#aebdca', fontSize: '13px', lineHeight: 1.55 };
const closeButtonStyle = { position: 'absolute', top: '16px', right: '19px', border: 0, background: 'transparent', color: '#b5c0ca', cursor: 'pointer', fontSize: '26px' };
const listStyle = { display: 'grid', gap: '9px', padding: '17px', overflowY: 'auto' };
const jobButtonStyle = {
    width: '100%', minHeight: '70px', display: 'flex', alignItems: 'center', gap: '16px',
    padding: '13px 15px', border: '1px solid #3b4855', borderRadius: '7px',
    background: '#202a34', color: '#edf4fa', textAlign: 'left', cursor: 'pointer',
};
const activeJobButtonStyle = { borderColor: '#6aa778', background: '#22382a', boxShadow: 'inset 3px 0 #6fbd80' };
const jobTextStyle = { minWidth: 0, flex: 1, display: 'grid', gap: '5px' };
const jobTitleStyle = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '14px' };
const jobDescriptionStyle = { color: '#aab8c5', fontSize: '12px', lineHeight: 1.4 };
const openLabelStyle = { padding: '5px 10px', border: '1px solid #596979', borderRadius: '5px', color: '#cbd7e1', fontSize: '11px', fontWeight: '800' };
const activeOpenLabelStyle = { borderColor: '#669d72', color: '#bce7c5' };
const footerStyle = { padding: '13px 20px', borderTop: '1px solid #34414d', color: '#96a6b4', fontSize: '12px', lineHeight: 1.45 };
