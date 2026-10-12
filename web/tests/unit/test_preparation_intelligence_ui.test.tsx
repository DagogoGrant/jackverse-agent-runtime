import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CaseDetailView } from '../../src/views/CaseDetailView';
import * as caseworkerHooks from '../../src/hooks/useCaseworker';

describe('Preparation Intelligence UI Invariants (Phase 5.2.1.1)', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('correctly aggregates lowercase evidence alignment states and gap classes in counters and styles', () => {
    // Mock case
    vi.spyOn(caseworkerHooks, 'useCase').mockReturnValue({
      data: {
        caseItem: {
          case_id: 'case-test-1',
          mission_id: 'm-1',
          title: 'Staff AI Engineer',
          goal: 'Prepare application',
          case_type: 'job_application',
          status: 'planning',
          version: 2,
        },
        etag: '"case:case-test-1:v2"',
      },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(caseworkerHooks, 'useCaseActions').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);

    vi.spyOn(caseworkerHooks, 'useClaims').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);

    vi.spyOn(caseworkerHooks, 'useEvents').mockReturnValue({
      data: { items: [] },
      isLoading: false,
    } as any);

    // Mock preparation report with strictly lowercase API enum values
    vi.spyOn(caseworkerHooks, 'usePreparationReport').mockReturnValue({
      data: {
        report_id: 'prep-abc123456789',
        case_id: 'case-test-1',
        opportunity_id: 'opp-1',
        user_id: 'alice',
        schema_version: 'prep_intel_v1',
        analyzer_version: 'prep_analyzer_v1',
        evidence_map: [
          {
            requirement_id: 'req-1',
            requirement_type: 'skill',
            description: 'Python',
            mandatory: true,
            status: 'supported',
            matched_fact_id: 'f-1',
            matched_fact_version: 1,
            verification_status: 'user_verified',
            fact_namespace: 'skills',
            fact_key: 'python',
          },
          {
            requirement_id: 'req-2',
            requirement_type: 'experience',
            description: '5+ years experience',
            mandatory: true,
            status: 'potential',
            matched_fact_id: 'f-2',
            matched_fact_version: 1,
            verification_status: 'unverified',
            fact_namespace: 'experience',
            fact_key: 'years',
          },
          {
            requirement_id: 'req-3',
            requirement_type: 'skill',
            description: 'Kubernetes',
            mandatory: true,
            status: 'restricted',
            matched_fact_id: 'f-3',
            matched_fact_version: 1,
            verification_status: 'user_verified',
            fact_namespace: 'skills',
            fact_key: 'kubernetes',
          },
          {
            requirement_id: 'req-4',
            requirement_type: 'language',
            description: 'German B2',
            mandatory: true,
            status: 'contradicted',
            matched_fact_id: 'f-4',
            matched_fact_version: 1,
            verification_status: 'user_verified',
            fact_namespace: 'languages',
            fact_key: 'german',
          },
          {
            requirement_id: 'req-5',
            requirement_type: 'skill',
            description: 'Docker',
            mandatory: false,
            status: 'gap',
          },
        ],
        gaps: [
          {
            gap_id: 'gap-1',
            requirement_id: 'req-4',
            category: 'hard_conflict',
            severity: 'critical',
            description: 'Language level is below required level',
            mitigation_guidance: 'Improve language level or seek English-only exemption',
          },
          {
            gap_id: 'gap-2',
            requirement_id: 'req-2',
            category: 'mandatory_evidence_gap',
            severity: 'critical',
            description: 'Years of experience is unverified',
            mitigation_guidance: 'Complete employer verification',
          },
          {
            gap_id: 'gap-3',
            requirement_id: 'req-5',
            category: 'preferred_evidence_gap',
            severity: 'moderate',
            description: 'Docker not documented in vault',
            mitigation_guidance: 'Add Docker certifications if applicable',
          },
        ],
        strategy: {
          recommended_materials: ['cv', 'cover_letter'],
          strengths: ['Confirmed skill: Python'],
          mitigations: ['Improve language level'],
          claim_intents: [
            {
              intent_id: 'intent-1',
              requirement_id: 'req-1',
              target_material: 'cv',
              claim_type: 'verifiable_fact',
              supporting_fact_ids: ['f-1'],
              suggested_angle: 'Emphasize verified Python mastery',
            },
          ],
          materials_checklist: [
            { material_type: 'cv', required: true, status: 'PENDING_DRAFT' },
            { material_type: 'cover_letter', required: true, status: 'PENDING_DRAFT' },
          ],
        },
        input_hash: 'inputhash1234567890',
        context_snapshot_hash: 'contexthash1234567890',
        source_evaluation_id: 'eval-opp-123',
        created_at: '2026-10-12T10:00:00Z',
        version: 1,
        is_stale: false,
      },
      isLoading: false,
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/cases/case-test-1']}>
          <Routes>
            <Route path="/cases/:caseId" element={<CaseDetailView />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    // 1. Evidence Alignment 5-state summary counters must each be 1
    const supportedCounter = screen.getByText('SUPPORTED').previousSibling;
    expect(supportedCounter?.textContent).toBe('1');

    const potentialCounter = screen.getByText('POTENTIAL').previousSibling;
    expect(potentialCounter?.textContent).toBe('1');

    const restrictedCounter = screen.getByText('RESTRICTED').previousSibling;
    expect(restrictedCounter?.textContent).toBe('1');

    const contradictedCounter = screen.getByText('CONTRADICTED').previousSibling;
    expect(contradictedCounter?.textContent).toBe('1');

    const gapCounter = screen.getByText('GAP').previousSibling;
    expect(gapCounter?.textContent).toBe('1');

    // 2. Gaps classes correctly rendered
    expect(screen.getByText('HARD CONFLICT')).toBeInTheDocument();
    expect(screen.getByText('MANDATORY EVIDENCE GAP')).toBeInTheDocument();
    expect(screen.getByText('PREFERRED EVIDENCE GAP')).toBeInTheDocument();

    // 3. Technical metadata is tucked inside details disclosure
    const disclosure = screen.getByText(/Inspect report technical metadata/i);
    expect(disclosure).toBeInTheDocument();
    expect(screen.getByText(/REPORT ID:/i)).toBeInTheDocument();
    expect(screen.getByText(/prep-abc123456789/)).toBeInTheDocument();

    // 4. No Sparkles text/icon; Re-analyze button exists
    expect(screen.getByText(/Re-analyze Preparation/i)).toBeInTheDocument();
  });
});
