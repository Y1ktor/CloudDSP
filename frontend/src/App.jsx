/** Route and dialog composition; workspace state remains mounted across tabs. */
import React, { useEffect, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import ArchitecturePage from './components/ArchitecturePage';
import K8Page from './components/K8Page';
import CostPage from './components/CostPage';
import ScoreToMidiPage from './components/ScoreToMidi/ScoreToMidiPage';
import AppNavBar from './components/AppNavBar';
import DemoLibrary from './components/DemoLibrary';
import WelcomeTutorial from './components/WelcomeTutorial';
import StemSplitter from './components/StemSplitter/StemSplitter';
import PreviousJobs from './components/StemSplitter/PreviousJobs';
import { useStudioApp } from './app/useStudioApp';
import { historyKindForPath } from './app/jobHistory';
import { useScoreJobHistory } from './app/useScoreJobHistory';
import { useMidiSheetJobSession } from './components/ScoreToMidi/useMidiSheetJobSession';
import { useScoreJobSession } from './components/ScoreToMidi/useScoreJobSession';
import { JOB_API_URL, WEBSOCKET_URL } from './app/config';
import { profile } from '@platform/profile';
import './assets/css/styles.css';

export default function App() {
    return <BrowserRouter><StudioApp /></BrowserRouter>;
}

function StudioApp() {
    const app = useStudioApp();
    // Account changes destroy score files, pending requests, history rows, and
    // instruments. Neither account can see the previous account's workspace.
    return <StudioPages key={app.authProps.session?.subject || app.authProps.session?.username || 'signed-out'} app={app} />;
}

export function StudioPages({ app }) {
    const {
        stemProps, authProps, authenticatedFetch, authLoading, activeJobId, activeDemoId,
        demoCatalog, isDemoLibraryOpen, setIsDemoLibraryOpen, openDemoJob,
        isAuthDialogOpen, isPreviousJobsOpen, setIsPreviousJobsOpen,
        previousJobs, isPreviousJobsLoading, previousJobsError, deletingJobId,
        selectPreviousJob, fetchPreviousJobs, deletePreviousJob,
    } = app;
    const { pathname } = useLocation();
    const isScoreStudio = historyKindForPath(pathname) === 'score';
    const scoreFetch = profile.id === 'local' && authProps.session ? authenticatedFetch : null;
    const scoreSession = useScoreJobSession(scoreFetch);
    const [direction, setDirection] = useState('sheet-to-midi');
    const sheetSession = useMidiSheetJobSession(scoreFetch);
    const sheetHistory = useScoreJobHistory(scoreFetch, 'sheet');
    const scoreOnlyHistory = useScoreJobHistory(scoreFetch);
    const scoreHistory = direction === 'midi-to-sheet' ? sheetHistory : scoreOnlyHistory;
    const activeConversionSession = direction === 'midi-to-sheet' ? sheetSession : scoreSession;
    const { setIsOpen: setScoreHistoryOpen } = scoreHistory;
    const historyOpen = isScoreStudio ? scoreHistory.isOpen : isPreviousJobsOpen;
    const onOpenHistory = isScoreStudio ? scoreHistory.open : authProps.onOpenHistory;

    useEffect(() => {
        setIsPreviousJobsOpen(false);
        setScoreHistoryOpen(false);
    }, [pathname, setIsPreviousJobsOpen, setScoreHistoryOpen]);

    return (
        <div className="studio-app-shell" style={{ minHeight: '100vh' }}>
            <AppNavBar authProps={{ ...authProps, onOpenHistory }} />
            <WelcomeTutorial
                enabled={!authLoading && !isAuthDialogOpen && !isDemoLibraryOpen && !historyOpen}
            />
            <DemoLibrary
                isOpen={isDemoLibraryOpen}
                jobs={demoCatalog.jobs}
                activeDemoId={activeDemoId}
                onSelect={openDemoJob}
                onClose={() => setIsDemoLibraryOpen(false)}
            />
            <PreviousJobs
                key={isScoreStudio ? direction : 'stem-history'}
                jobKind={isScoreStudio ? (direction === 'midi-to-sheet' ? 'sheet' : 'score') : 'stem'}
                isOpen={historyOpen}
                onClose={() => isScoreStudio ? setScoreHistoryOpen(false) : setIsPreviousJobsOpen(false)}
                jobs={isScoreStudio ? scoreHistory.jobs : previousJobs}
                activeJobId={isScoreStudio ? activeConversionSession.scoreUploadState?.jobId : activeJobId}
                isLoading={isScoreStudio ? scoreHistory.isLoading : isPreviousJobsLoading}
                error={isScoreStudio ? scoreHistory.error : previousJobsError}
                onSelect={isScoreStudio ? (job) => { activeConversionSession.openSavedJob(job); setScoreHistoryOpen(false); } : selectPreviousJob}
                onRefresh={isScoreStudio ? scoreHistory.refresh : fetchPreviousJobs}
                onDelete={isScoreStudio ? null : deletePreviousJob}
                deletingJobId={isScoreStudio ? null : deletingJobId}
                selectionDisabled={isScoreStudio && activeConversionSession.isUploading}
            />
            {!authLoading && (!JOB_API_URL || (profile.requiresWebSocket && !WEBSOCKET_URL)) && (
                <div style={{ margin: '-24px 20px 20px', color: '#8b5a00', fontSize: '13px' }}>
                    {profile.messages.configurationWarning}
                </div>
            )}
            <Routes>
                <Route path="/" element={<div style={{ display: 'flex', justifyContent: 'center' }}><StemSplitter {...stemProps} /></div>} />
                {/* Remount on account changes so a previously staged file is not shown to the next user. */}
                <Route
                    path="/score-to-midi"
                    element={
                        <ScoreToMidiPage
                            key={authProps.session?.subject || authProps.session?.username || 'signed-out'}
                            authenticated={!authLoading && Boolean(authProps.session)}
                            authLoading={authLoading}
                            authProvider={profile.authKind === 'oidc' ? 'Keycloak' : 'your account'}
                            authenticatedFetch={scoreFetch}
                            scoreSession={scoreSession}
                            sheetSession={sheetSession}
                            direction={direction}
                            setDirection={setDirection}
                        />
                    }
                />
                <Route path="/architecture" element={<ArchitecturePage />} />
                <Route path="/k8" element={<K8Page />} />
                <Route path="/cost" element={<CostPage />} />
                <Route path="/stems" element={<Navigate to="/" replace />} />
            </Routes>
        </div>
    );
}
