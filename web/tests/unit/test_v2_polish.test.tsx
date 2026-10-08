import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, renderHook, act, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import fs from 'fs';
import path from 'path';

import {
  humanizeEvent,
  formatEventTime,
  getEventSubtitle,
  formatEditorialDate,
  formatMissionKind,
} from '../../src/lib/eventPresentation';
import { ActivityView } from '../../src/views/ActivityView';
import { NavRail } from '../../src/components/layout/NavRail';
import { HomeView } from '../../src/views/HomeView';
import { OpportunitiesView } from '../../src/views/OpportunitiesView';
import { NeedsYouView } from '../../src/views/NeedsYouView';
import { MissionDetailView } from '../../src/views/MissionDetailView';
import { NumberRoll } from '../../src/components/ui/NumberRoll';
import { AmbientCanvas } from '../../src/components/ui/AmbientCanvas';
import { TextureBadge } from '../../src/components/ui/TextureBadge';
import { useTheme } from '../../src/hooks/useTheme';

import * as caseworkerHooks from '../../src/hooks/useCaseworker';

describe('Design V2.0.1 Craft & Accessibility Polish', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    localStorage.clear();
    delete document.documentElement.dataset.theme;
    document.documentElement.className = '';
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('1. Centralized Human Event Presentation', () => {
    it('correctly translates JackVerse dotted domain events', () => {
      expect(humanizeEvent('mission.created')).toBe('Mission created');
      expect(
        humanizeEvent({
          event_type: 'case.status_changed',
          payload: { new_status: 'investigation' },
        })
      ).toBe('Case moved to Investigation');
      expect(humanizeEvent('approval.requested')).toBe('Your approval was requested');
      expect(humanizeEvent('approval.approved')).toBe('You approved an action');
      expect(humanizeEvent('context.fact_superseded')).toBe('Profile information updated');
      expect(humanizeEvent('mission.archived')).toBe('Mission archived');
      expect(humanizeEvent('mission.restored')).toBe('Mission restored from archive');
    });

    it('gracefully handles unknown dot and underscore event formats', () => {
      expect(humanizeEvent('runtime.container_restarted')).toBe('Runtime Container Restarted');
      expect(humanizeEvent('custom_stream_flushed')).toBe('Custom Stream Flushed');
      expect(humanizeEvent('')).toBe('System event');
    });

    it('formats event timestamps cleanly into 24h clock', () => {
      expect(formatEventTime(undefined)).toBe('--:--');
      expect(formatEventTime('invalid-date')).toBe('--:--');
      const timeStr = formatEventTime('2026-10-01T14:30:00Z');
      expect(timeStr).toMatch(/\d{2}:\d{2}/);
    });
  });

  describe('2. ActivityView Technical Metadata Isolation', () => {
    it('hides technical metadata (POS, aggregate) until Inspect drawer is opened', () => {
      vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
        data: {
          items: [
            {
              event_id: 'ev-100',
              event_type: 'case.status_changed',
              payload: { new_status: 'in_review' },
              aggregate_type: 'case',
              aggregate_id: 'case-998877665544',
              aggregate_version: 3,
              position: 42,
              occurred_at: new Date().toISOString(),
            },
          ],
          next_position: null,
        },
        isLoading: false,
      } as any);

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <ActivityView />
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Human title is present in the main row
      expect(screen.getByText('Case moved to In Review')).toBeInTheDocument();

      // Technical POS # and aggregate version are NOT in the unexpanded primary view
      expect(screen.queryByText(/POS #42/)).not.toBeInTheDocument();
      expect(screen.queryByText(/case-998877665544/)).not.toBeInTheDocument();

      // Click Inspect button
      const inspectBtn = screen.getByRole('button', { name: /inspect/i });
      fireEvent.click(inspectBtn);

      // Now technical metadata is visible inside the Inspect drawer
      expect(screen.getByText(/POS #42/)).toBeInTheDocument();
      expect(screen.getByText(/AGGREGATE: \[case \/ case-998877665544 v3\]/)).toBeInTheDocument();
    });
  });

  describe('3. Case Book-Spine Parent Navigation', () => {
    it('navigates to parent mission when mission_id is resolved on /cases/:id', () => {
      vi.spyOn(caseworkerHooks, 'useCase').mockReturnValue({
        data: {
          caseItem: {
            case_id: 'c-100',
            mission_id: 'm-200',
            title: 'Test Case',
            case_type: 'job_application',
            status: 'new',
            version: 1,
            created_at: new Date().toISOString(),
          },
          etag: '"v1"',
        },
        isLoading: false,
      } as any);

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/cases/c-100']}>
            <NavRail />
          </MemoryRouter>
        </QueryClientProvider>
      );

      const parentBtn = screen.getByRole('button', { name: /return to parent mission/i });
      expect(parentBtn).toBeInTheDocument();
      expect(parentBtn).toHaveTextContent(/Parent Mission/i);
    });

    it('falls back to Missions Index if case has no parent mission_id', () => {
      vi.spyOn(caseworkerHooks, 'useCase').mockReturnValue({
        data: {
          caseItem: {
            case_id: 'c-100',
            mission_id: null,
            title: 'Standalone Case',
            case_type: 'job_application',
            status: 'new',
            version: 1,
            created_at: new Date().toISOString(),
          },
          etag: '"v1"',
        },
        isLoading: false,
      } as any);

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/cases/c-100']}>
            <NavRail />
          </MemoryRouter>
        </QueryClientProvider>
      );

      const fallbackBtn = screen.getByRole('button', { name: /return to missions index/i });
      expect(fallbackBtn).toBeInTheDocument();
      expect(fallbackBtn).toHaveTextContent(/Missions Index/i);
    });
  });

  describe('4. Semantic Keyboard Navigation & Consumer Polish', () => {
    it('renders mission rows as semantic Link anchors on HomeView', () => {
      vi.spyOn(caseworkerHooks, 'useMissions').mockReturnValue({
        data: [
          {
            mission_id: 'm-001',
            title: 'Lead AI Engineer at DeepMind',
            goal: 'Secure an interview',
            kind: 'opportunity_pursuit',
            status: 'active',
            version: 1,
            created_at: new Date().toISOString(),
          },
        ],
        isLoading: false,
      } as any);

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <HomeView />
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Check placeholder polish
      expect(
        screen.getByPlaceholderText('Describe what you want to accomplish…')
      ).toBeInTheDocument();

      // Check mission row is a semantic Link
      const link = screen.getByRole('link', { name: /lead ai engineer at deepmind/i });
      expect(link).toBeInTheDocument();
      expect(link).toHaveAttribute('href', '/missions/m-001');
    });

    it('renders opportunity rows as semantic Link anchors on OpportunitiesView', () => {
      vi.spyOn(caseworkerHooks, 'useOpportunities').mockReturnValue({
        data: [
          {
            opportunity_id: 'opp-100',
            title: 'Senior Systems Architect',
            organization: 'Anthropic',
            opportunity_type: 'job',
            status: 'discovered',
            requirements: [],
            created_at: new Date().toISOString(),
          },
        ],
        isLoading: false,
      } as any);

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <OpportunitiesView />
          </MemoryRouter>
        </QueryClientProvider>
      );

      const link = screen.getByRole('link', { name: /senior systems architect/i });
      expect(link).toBeInTheDocument();
      expect(link).toHaveAttribute('href', '/opportunities/opp-100');
    });

    it('renders approval list items as semantic buttons in NeedsYouView', () => {
      vi.spyOn(caseworkerHooks, 'useApprovals').mockReturnValue({
        data: [
          {
            approval_id: 'app-999',
            action_id: 'act-111',
            case_id: 'case-222',
            status: 'pending',
            requested_at: new Date().toISOString(),
          },
        ],
        isLoading: false,
      } as any);

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <NeedsYouView />
          </MemoryRouter>
        </QueryClientProvider>
      );

      const button = screen.getByRole('button', { name: /approval #app-999/i });
      expect(button).toBeInTheDocument();
      expect(button.tagName.toLowerCase()).toBe('button');
    });
  });

  describe('5. NumberRoll Spatial Transitions', () => {
    it('renders the initial value statically without animation markup', () => {
      const { container } = render(<NumberRoll value={5} />);
      expect(screen.getByText('5')).toBeInTheDocument();
      // Should not have rollState (absolute layer) on first mount
      expect(container.querySelectorAll('.absolute').length).toBe(0);
    });

    it('respects prefers-reduced-motion by updating instantly without roll layers', async () => {
      window.matchMedia = vi.fn().mockImplementation((query) => ({
        matches: query === '(prefers-reduced-motion: reduce)',
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }));

      const { rerender, container } = render(<NumberRoll value={1} />);
      expect(screen.getByText('1')).toBeInTheDocument();

      rerender(<NumberRoll value={2} />);
      expect(await screen.findByText('2')).toBeInTheDocument();
      expect(container.querySelectorAll('.absolute').length).toBe(0);
    });
  });

  describe('6. Theme Wash Timer Race Prevention', () => {
    it('cancels previous wash timer when rapidly toggling themes', () => {
      vi.useFakeTimers();

      const { result } = renderHook(() => useTheme());

      act(() => {
        result.current.setTheme('ink');
      });
      expect(document.documentElement.classList.contains('theme-wash')).toBe(true);

      // Fast forward only 200ms (less than 450ms timeout) and toggle again
      act(() => {
        vi.advanceTimersByTime(200);
        result.current.setTheme('paper');
      });
      expect(document.documentElement.classList.contains('theme-wash')).toBe(true);

      // Fast forward another 300ms (original timer would have fired at 450ms, but was reset)
      act(() => {
        vi.advanceTimersByTime(300);
      });
      // Second timer is still alive (only 300ms of 450ms elapsed)
      expect(document.documentElement.classList.contains('theme-wash')).toBe(true);

      // Fast forward remaining 200ms -> total 500ms since second toggle
      act(() => {
        vi.advanceTimersByTime(200);
      });
      expect(document.documentElement.classList.contains('theme-wash')).toBe(false);

      vi.useRealTimers();
    });
  });

  describe('7. AmbientCanvas Reduced Motion & Lifecycle', () => {
    it('does not start animation loop when prefers-reduced-motion is active', () => {
      const rafSpy = vi.spyOn(window, 'requestAnimationFrame');

      window.matchMedia = vi.fn().mockImplementation((query) => ({
        matches: query === '(prefers-reduced-motion: reduce)',
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }));

      render(<AmbientCanvas />);
      expect(rafSpy).not.toHaveBeenCalled();
    });
  });

  describe('8. TextureBadge Dual-State Archival Representation', () => {
    it('renders dual states with ARCHIVED prefix for archived statuses', () => {
      const { rerender } = render(<TextureBadge status="paused" archived={true} />);
      expect(screen.getByText(/archived · paused/i)).toBeInTheDocument();

      rerender(<TextureBadge status="cancelled" archived={true} />);
      expect(screen.getByText(/archived · cancelled/i)).toBeInTheDocument();

      rerender(<TextureBadge status="completed" archived={true} />);
      expect(screen.getByText(/archived · completed/i)).toBeInTheDocument();

      rerender(<TextureBadge status="active" archived={false} />);
      expect(screen.getByText('active')).toBeInTheDocument();
      expect(screen.queryByText(/archived/i)).not.toBeInTheDocument();
    });
  });

  describe('9. Authoritative CaseType Dropdown Invariants', () => {
    it('renders all 8 authoritative OpenAPI CaseTypes and omits stale grant_submission/dispute', () => {
      vi.spyOn(caseworkerHooks, 'useMission').mockReturnValue({
        data: {
          mission: {
            mission_id: 'm-alpha-001',
            user_id: 'alice',
            title: 'Strategic Campaign',
            goal: 'Fulfill campaign objectives',
            kind: 'general_goal',
            status: 'active',
            success_criteria: [],
            constraints: [],
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            archived: false,
            archived_at: null,
            version: 1,
          },
          etag: '"w/etag-001"',
        },
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      } as any);

      vi.spyOn(caseworkerHooks, 'useMissionCases').mockReturnValue({
        data: [],
        isLoading: false,
      } as any);

      vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
        data: { items: [] },
        isLoading: false,
      } as any);

      vi.spyOn(caseworkerHooks, 'useCreateCase').mockReturnValue({
        mutateAsync: vi.fn(),
        isPending: false,
      } as any);

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/missions/m-alpha-001']}>
            <Routes>
              <Route path="/missions/:missionId" element={<MissionDetailView />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Open new case form
      const newCaseBtn = screen.getByRole('button', { name: /\+ new case/i });
      fireEvent.click(newCaseBtn);

      const select = screen.getByRole('combobox');
      expect(select).toBeInTheDocument();

      const options = Array.from(select.querySelectorAll('option')).map((opt) => opt.value);
      const expectedValues = [
        'job_application',
        'scholarship_application',
        'grant_pursuit',
        'housing_search',
        'package_investigation',
        'refund_request',
        'service_complaint',
        'general',
      ];

      expect(options).toEqual(expectedValues);
      expect(options).not.toContain('grant_submission');
      expect(options).not.toContain('dispute');
      expect(screen.queryByRole('option', { name: /grant submission/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('option', { name: /dispute resolution/i })).not.toBeInTheDocument();
    });
  });

  describe('10. Active Mission Archival Warning Actions', () => {
    it('exposes Pause mission, Cancel mission, and Dismiss options on active archive attempt', async () => {
      const mockTransitionMutate = vi.fn().mockResolvedValue({});
      const mockCancelMutate = vi.fn().mockResolvedValue({});

      vi.spyOn(caseworkerHooks, 'useMission').mockReturnValue({
        data: {
          mission: {
            mission_id: 'm-active-123',
            user_id: 'alice',
            title: 'Live Operational Mission',
            goal: 'Goal',
            kind: 'general_goal',
            status: 'active',
            success_criteria: [],
            constraints: [],
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            archived: false,
            archived_at: null,
            version: 2,
          },
          etag: '"w/etag-active-2"',
        },
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      } as any);

      vi.spyOn(caseworkerHooks, 'useMissionCases').mockReturnValue({
        data: [],
        isLoading: false,
      } as any);

      vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
        data: { items: [] },
        isLoading: false,
      } as any);

      vi.spyOn(caseworkerHooks, 'useTransitionMission').mockReturnValue({
        mutateAsync: mockTransitionMutate,
        isPending: false,
      } as any);

      vi.spyOn(caseworkerHooks, 'useCancelMission').mockReturnValue({
        mutateAsync: mockCancelMutate,
        isPending: false,
      } as any);

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/missions/m-active-123']}>
            <Routes>
              <Route path="/missions/:missionId" element={<MissionDetailView />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Click Archive button for active mission
      const archiveBtn = screen.getByRole('button', { name: /^archive$/i });
      fireEvent.click(archiveBtn);

      // Warning modal must be displayed
      expect(screen.getByText('Active mission cannot be archived')).toBeInTheDocument();
      expect(
        screen.getByText('Active missions must be paused or cancelled before archiving.')
      ).toBeInTheDocument();

      const modal = screen.getByText('Active mission cannot be archived').closest('div.fixed')!;
      expect(modal).toBeInTheDocument();

      // Must expose Pause mission, Cancel mission, and Dismiss inside the modal
      const pauseBtn = within(modal).getByRole('button', { name: /^pause mission$/i });
      const cancelBtn = within(modal).getByRole('button', { name: /^cancel mission$/i });
      const dismissBtn = within(modal).getByRole('button', { name: /^dismiss$/i });

      expect(pauseBtn).toBeInTheDocument();
      expect(cancelBtn).toBeInTheDocument();
      expect(dismissBtn).toBeInTheDocument();

      // Test Cancel mission routes to dedicated 'Cancel this mission?' confirmation modal
      fireEvent.click(cancelBtn);
      expect(screen.queryByText('Active mission cannot be archived')).not.toBeInTheDocument();

      const cancelConfirmModal = screen.getByText('Cancel this mission?').closest('div.fixed')!;
      expect(cancelConfirmModal).toBeInTheDocument();
      expect(
        within(cancelConfirmModal).getByRole('button', { name: /^keep mission$/i })
      ).toBeInTheDocument();

      const confirmCancelBtn = within(cancelConfirmModal).getByRole('button', { name: /^cancel mission$/i });
      fireEvent.click(confirmCancelBtn);

      expect(mockCancelMutate).toHaveBeenCalledWith({
        missionId: 'm-active-123',
        etag: '"w/etag-active-2"',
        reason: 'Mission cancelled by user',
      });
    });
  });

  describe('11. Strict Monochrome Design V2 Compliance', () => {
    it('verifies zero red CSS classes in MissionDetailView source code', () => {
      const sourcePath = path.resolve(__dirname, '../../src/views/MissionDetailView.tsx');
      const sourceCode = fs.readFileSync(sourcePath, 'utf-8');

      expect(sourceCode).not.toContain('bg-red-700');
      expect(sourceCode).not.toContain('hover:bg-red-800');
      expect(sourceCode).not.toContain('text-red-600');
      expect(sourceCode).not.toMatch(/\bred-\d{2,3}\b/);
      expect(sourceCode).not.toMatch(/\btext-red\b/);
      expect(sourceCode).not.toMatch(/\bbg-red\b/);
      expect(sourceCode).not.toMatch(/\bborder-red\b/);
    });
  });

  describe('12. Mission Dossier V2.0.3 Targeted Editorial Polish', () => {
    describe('Editorial presentation helpers', () => {
      it('formats editorial date deterministically in UTC', () => {
        expect(formatEditorialDate('2026-10-08T04:49:00Z')).toBe('08 Oct 2026');
        expect(formatEditorialDate('2026-01-01T00:00:00Z')).toBe('01 Jan 2026');
        expect(formatEditorialDate(undefined)).toBe('-- --- ----');
        expect(formatEditorialDate('invalid-date')).toBe('-- --- ----');
      });

      it('humanizes mission kind taxonomy into product-facing strings', () => {
        expect(formatMissionKind('opportunity_pursuit')).toBe('Opportunity Pursuit');
        expect(formatMissionKind('problem_resolution')).toBe('Problem Resolution');
        expect(formatMissionKind('general_goal')).toBe('General Goal');
        expect(formatMissionKind(null)).toBe('General Goal');
        expect(formatMissionKind('custom_kind_slug')).toBe('Custom Kind Slug');
      });

      it('extracts event subtitle strictly for case.created and avoids assuming titles on other events', () => {
        expect(
          getEventSubtitle({
            event_type: 'case.created',
            payload: { title: 'Find suitable Agentic AI roles' },
          })
        ).toBe('Find suitable Agentic AI roles');

        expect(
          getEventSubtitle({
            event_type: 'case_created',
            payload: { title: 'Proposal submission' },
          })
        ).toBe('Proposal submission');

        // Strictly returns null for events like case.status_changed that lack title in event schema
        expect(
          getEventSubtitle({
            event_type: 'case.status_changed',
            payload: { old_status: 'new', new_status: 'investigation', reason: 'Triage' },
          })
        ).toBeNull();

        expect(getEventSubtitle('case.created')).toBeNull();
        expect(getEventSubtitle(null)).toBeNull();
      });
    });

    describe('Mission Dossier Title/Goal Hierarchy & Redundant Box Elimination', () => {
      it('omits deck when title and goal are normalized-identical and does not render Operational goal box', () => {
        vi.spyOn(caseworkerHooks, 'useMission').mockReturnValue({
          data: {
            mission: {
              mission_id: 'm-dedup-1',
              user_id: 'alice',
              title: 'Land Principal AI Engineer Role',
              goal: '  land   principal ai engineer role  ',
              kind: 'opportunity_pursuit',
              status: 'active',
              version: 3,
              created_at: '2026-10-08T04:49:00Z',
            },
            etag: '"etag-1"',
          },
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useMissionCases').mockReturnValue({
          data: [],
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
          data: { items: [] },
          isLoading: false,
        } as any);

        render(
          <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={['/missions/m-dedup-1']}>
              <Routes>
                <Route path="/missions/:missionId" element={<MissionDetailView />} />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        );

        // Title is rendered once as h1
        const h1 = screen.getByRole('heading', { level: 1 });
        expect(h1).toHaveTextContent('Land Principal AI Engineer Role');

        // Deck is NOT rendered because title matches normalized goal
        expect(screen.queryByText('land principal ai engineer role')).not.toBeInTheDocument();

        // Old "Operational goal & scope" box is deleted
        expect(screen.queryByText(/operational goal & scope/i)).not.toBeInTheDocument();
      });

      it('renders goal deck below h1 when title and goal are distinct', () => {
        vi.spyOn(caseworkerHooks, 'useMission').mockReturnValue({
          data: {
            mission: {
              mission_id: 'm-distinct-1',
              user_id: 'alice',
              title: 'German AI Market Campaign',
              goal: 'Secure high-impact agentic engineering roles across Munich and Berlin tech hubs',
              kind: 'opportunity_pursuit',
              status: 'active',
              version: 2,
              created_at: '2026-10-08T04:49:00Z',
            },
            etag: '"etag-2"',
          },
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useMissionCases').mockReturnValue({
          data: [],
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
          data: { items: [] },
          isLoading: false,
        } as any);

        render(
          <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={['/missions/m-distinct-1']}>
              <Routes>
                <Route path="/missions/:missionId" element={<MissionDetailView />} />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        );

        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('German AI Market Campaign');
        expect(
          screen.getByText('Secure high-impact agentic engineering roles across Munich and Berlin tech hubs')
        ).toBeInTheDocument();

        // Operational goal & scope card is NOT rendered
        expect(screen.queryByText(/operational goal & scope/i)).not.toBeInTheDocument();
      });
    });

    describe('Taxonomy and Metadata Presentation', () => {
      it('renders humanized kind and editorial date without KIND: or STARTED:', () => {
        vi.spyOn(caseworkerHooks, 'useMission').mockReturnValue({
          data: {
            mission: {
              mission_id: 'm-tax-1',
              user_id: 'alice',
              title: 'Incident Mitigation',
              goal: 'Resolve production latency',
              kind: 'problem_resolution',
              status: 'active',
              version: 4,
              created_at: '2026-10-08T12:00:00Z',
            },
            etag: '"etag-3"',
          },
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useMissionCases').mockReturnValue({
          data: [],
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
          data: { items: [] },
          isLoading: false,
        } as any);

        render(
          <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={['/missions/m-tax-1']}>
              <Routes>
                <Route path="/missions/:missionId" element={<MissionDetailView />} />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        );

        expect(screen.getByText('Problem Resolution')).toBeInTheDocument();
        expect(screen.getByText('08 Oct 2026')).toBeInTheDocument();
        expect(screen.getByText('v4')).toBeInTheDocument();

        expect(screen.queryByText(/KIND:/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/STARTED:/i)).not.toBeInTheDocument();
      });
    });

    describe('Cases Section & Oldest-First Folios', () => {
      it('renders Cases heading with quiet count and 01, 02 folios sorted oldest first', () => {
        vi.spyOn(caseworkerHooks, 'useMission').mockReturnValue({
          data: {
            mission: {
              mission_id: 'm-cases-1',
              user_id: 'alice',
              title: 'Talent Dispatch',
              goal: 'Goal',
              kind: 'general_goal',
              status: 'active',
              version: 1,
              created_at: '2026-10-08T00:00:00Z',
            },
            etag: '"etag-cases"',
          },
          isLoading: false,
        } as any);

        // Case 2 was created earlier than Case 1 to verify oldest-first sort
        vi.spyOn(caseworkerHooks, 'useMissionCases').mockReturnValue({
          data: [
            {
              case_id: 'case-bbb-later-2222',
              mission_id: 'm-cases-1',
              title: 'Later Created Case',
              goal: 'Goal 2',
              case_type: 'grant_pursuit',
              status: 'investigating',
              created_at: '2026-10-08T06:00:00Z',
            },
            {
              case_id: 'case-aaa-earlier-1111',
              mission_id: 'm-cases-1',
              title: 'Earlier Created Case',
              goal: 'Goal 1',
              case_type: 'job_application',
              status: 'open',
              created_at: '2026-10-08T02:00:00Z',
            },
          ],
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
          data: { items: [] },
          isLoading: false,
        } as any);

        render(
          <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={['/missions/m-cases-1']}>
              <Routes>
                <Route path="/missions/:missionId" element={<MissionDetailView />} />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        );

        // Heading is "Cases", count is "2 cases"
        expect(screen.getByRole('heading', { level: 2, name: 'Cases' })).toBeInTheDocument();
        expect(screen.getByText('2 cases')).toBeInTheDocument();
        expect(screen.queryByText('Active Cases Dossier')).not.toBeInTheDocument();

        // Folios 01 and 02
        const folio1 = screen.getByTitle('case-aaa-earlier-1111');
        const folio2 = screen.getByTitle('case-bbb-later-2222');

        expect(folio1).toHaveTextContent('01');
        expect(folio2).toHaveTextContent('02');

        // Raw UUID slices should not appear as visible text
        expect(screen.queryByText('case-aaa')).not.toBeInTheDocument();
        expect(screen.queryByText('case-bbb')).not.toBeInTheDocument();
      });
    });

    describe('Mission Chronicle Two-Line Treatment', () => {
      it('renders Case opened with payload title subtitle and Case moved to {Status}', () => {
        vi.spyOn(caseworkerHooks, 'useMission').mockReturnValue({
          data: {
            mission: {
              mission_id: 'm-chron-1',
              user_id: 'alice',
              title: 'Chronicle Test',
              goal: 'Goal',
              kind: 'general_goal',
              status: 'active',
              version: 1,
              created_at: '2026-10-08T00:00:00Z',
            },
            etag: '"etag-chron"',
          },
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useMissionCases').mockReturnValue({
          data: [],
          isLoading: false,
        } as any);

        vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
          data: {
            items: [
              {
                event_id: 'ev-1',
                event_type: 'case.created',
                payload: {
                  title: 'Find suitable Agentic AI roles',
                  case_id: 'c-1',
                  mission_id: 'm-chron-1',
                },
                occurred_at: '2026-10-08T04:49:00Z',
              },
              {
                event_id: 'ev-2',
                event_type: 'case.status_changed',
                payload: {
                  old_status: 'open',
                  new_status: 'investigating',
                  reason: 'Initial triage',
                  mission_id: 'm-chron-1',
                },
                occurred_at: '2026-10-08T05:00:00Z',
              },
            ],
          },
          isLoading: false,
        } as any);

        render(
          <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={['/missions/m-chron-1']}>
              <Routes>
                <Route path="/missions/:missionId" element={<MissionDetailView />} />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        );

        // Case opened event with subtitle
        expect(screen.getByText('Case opened')).toBeInTheDocument();
        expect(screen.getByText('Find suitable Agentic AI roles')).toBeInTheDocument();

        // Case moved event
        expect(screen.getByText('Case moved to Investigating')).toBeInTheDocument();
      });
    });
  });
});

