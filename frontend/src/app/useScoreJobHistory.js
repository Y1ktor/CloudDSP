/** Score library requests are canceled on refresh, account change, or unmount. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { loadJobHistory } from './jobHistory';

export function useScoreJobHistory(authenticatedFetch, kind = 'score') {
    const [jobs, setJobs] = useState([]);
    const [isOpen, setIsOpen] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState('');
    const requestRef = useRef(null);

    useEffect(() => () => requestRef.current?.abort(), []);

    const refresh = useCallback(async () => {
        requestRef.current?.abort();
        const controller = new AbortController();
        requestRef.current = controller;
        setJobs([]);
        setError('');
        if (!authenticatedFetch) return [];
        setIsLoading(true);
        try {
            const saved = await loadJobHistory(authenticatedFetch, kind, controller.signal);
            if (controller.signal.aborted) return [];
            setJobs(saved);
            return saved;
        } catch (failure) {
            if (!controller.signal.aborted) setError(failure.message || 'Could not load score history.');
            return [];
        } finally {
            if (!controller.signal.aborted) setIsLoading(false);
        }
    }, [authenticatedFetch, kind]);

    const open = useCallback(() => {
        setIsOpen(true);
        void refresh();
    }, [refresh]);

    return { jobs, isOpen, setIsOpen, isLoading, error, refresh, open };
}
