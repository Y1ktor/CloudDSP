/** Load only the validated static demo catalog, cancelling on unmount. */
import { useEffect } from 'react';
import { loadDemoCatalog } from '../utils/demoCatalog';

export function useDemoCatalog(setDemoCatalog) {
    useEffect(() => {
        const controller = new AbortController();
        loadDemoCatalog({ signal: controller.signal })
            .then(setDemoCatalog)
            .catch((error) => {
                if (error.name === 'AbortError') return;
                // Demo publication is a separate deployment step. The signed-in
                // application remains fully usable while the catalog is absent
                // or temporarily unavailable.
                console.warn('[CloudDSP] Public demo catalog is unavailable:', error);
                setDemoCatalog({ jobs: [], defaultJobId: null });
            });
        return () => controller.abort();
    }, [setDemoCatalog]);
}
