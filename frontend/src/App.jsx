/** Route and dialog composition; workspace state remains mounted across tabs. */
import React from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import ArchitecturePage from './components/ArchitecturePage';
import K8Page from './components/K8Page';
import CostPage from './components/CostPage';
import AppNavBar from './components/AppNavBar';
import DemoLibrary from './components/DemoLibrary';
import WelcomeTutorial from './components/WelcomeTutorial';
import StemSplitter from './components/StemSplitter/StemSplitter';
import PreviousJobs from './components/StemSplitter/PreviousJobs';
import { useStudioApp } from './app/useStudioApp';
import { JOB_API_URL, WEBSOCKET_URL } from './app/config';
import { profile } from '@platform/profile';
import './assets/css/styles.css';

export default function App() {
    const {
        stemProps, authProps, authLoading, activeJobId, activeDemoId,
        demoCatalog, isDemoLibraryOpen, setIsDemoLibraryOpen, openDemoJob,
        isAuthDialogOpen, isPreviousJobsOpen, setIsPreviousJobsOpen,
        previousJobs, isPreviousJobsLoading, previousJobsError, deletingJobId,
        selectPreviousJob, fetchPreviousJobs, deletePreviousJob,
    } = useStudioApp();

    return (
        <BrowserRouter>
            <div className="studio-app-shell" style={{ minHeight: '100vh' }}>
                <AppNavBar authProps={authProps} />
                <WelcomeTutorial
                    enabled={!authLoading && !isAuthDialogOpen && !isDemoLibraryOpen && !isPreviousJobsOpen}
                />
                <DemoLibrary
                    isOpen={isDemoLibraryOpen}
                    jobs={demoCatalog.jobs}
                    activeDemoId={activeDemoId}
                    onSelect={openDemoJob}
                    onClose={() => setIsDemoLibraryOpen(false)}
                />
                <PreviousJobs
                    isOpen={isPreviousJobsOpen}
                    onClose={() => setIsPreviousJobsOpen(false)}
                    jobs={previousJobs}
                    activeJobId={activeJobId}
                    isLoading={isPreviousJobsLoading}
                    error={previousJobsError}
                    onSelect={selectPreviousJob}
                    onRefresh={fetchPreviousJobs}
                    onDelete={deletePreviousJob}
                    deletingJobId={deletingJobId}
                />
                {!authLoading && (!JOB_API_URL || (profile.requiresWebSocket && !WEBSOCKET_URL)) && (
                    <div style={{ margin: '-24px 20px 20px', color: '#8b5a00', fontSize: '13px' }}>
                        {profile.messages.configurationWarning}
                    </div>
                )}
                <Routes>
                    <Route path="/" element={<div style={{ display: 'flex', justifyContent: 'center' }}><StemSplitter {...stemProps} /></div>} />
                    <Route path="/architecture" element={<ArchitecturePage />} />
                    <Route path="/k8" element={<K8Page />} />
                    <Route path="/cost" element={<CostPage />} />
                    <Route path="/stems" element={<Navigate to="/" replace />} />
                </Routes>
            </div>
        </BrowserRouter>
    );
}
