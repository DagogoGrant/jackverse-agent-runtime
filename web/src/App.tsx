import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Shell } from './components/layout/Shell';
import { PrototypesPage } from './prototypes/PrototypesPage';
import { NotFoundView } from './views/NotFoundView';

// Production views will be imported here
import { HomeView } from './views/HomeView';
import { MissionsView } from './views/MissionsView';
import { MissionDetailView } from './views/MissionDetailView';
import { CaseDetailView } from './views/CaseDetailView';
import { OpportunitiesView } from './views/OpportunitiesView';
import { OpportunityDetailView } from './views/OpportunityDetailView';
import { NeedsYouView } from './views/NeedsYouView';
import { ContextView } from './views/ContextView';
import { ActivityView } from './views/ActivityView';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 1000 * 30, // 30 seconds
    },
  },
});

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Shell>
          <Routes>
            <Route path="/" element={<HomeView />} />
            <Route path="/missions" element={<MissionsView />} />
            <Route path="/missions/:missionId" element={<MissionDetailView />} />
            <Route path="/cases/:caseId" element={<CaseDetailView />} />
            <Route path="/opportunities" element={<OpportunitiesView />} />
            <Route path="/opportunities/:id" element={<OpportunityDetailView />} />
            <Route path="/approvals" element={<NeedsYouView />} />
            <Route path="/context" element={<ContextView />} />
            <Route path="/activity" element={<ActivityView />} />

            {/* Visual Prototype Inspection Routes */}
            <Route path="/prototypes" element={<PrototypesPage initialTab="a" />} />
            <Route path="/prototypes/a" element={<PrototypesPage initialTab="a" />} />
            <Route path="/prototypes/b" element={<PrototypesPage initialTab="b" />} />
            <Route path="/prototypes/c" element={<PrototypesPage initialTab="c" />} />

            {/* 404 Fallback */}
            <Route path="/404" element={<NotFoundView />} />
            <Route path="*" element={<NotFoundView />} />
          </Routes>
        </Shell>
      </BrowserRouter>
    </QueryClientProvider>
  );
};

export default App;
