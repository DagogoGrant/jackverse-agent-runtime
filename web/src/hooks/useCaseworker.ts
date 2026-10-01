import { useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '../api/client';
import type {
  Mission,
  Case,
  Opportunity,
  FactSummary,
  FactDetail,
  ProfileReadiness,
  Claim,
  Action,
  Approval,
  ApprovalDecision,
  PaginatedResponse,
  CursorPaginatedEvents,
  CreateOpportunityRequest,
  SupersedeFactRequest,
} from '../api/types';

// Invalidate on dev user switch
export function useDevUserSync() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const handleUserChange = () => {
      // Invalidate and completely remove all cached queries when user switches
      queryClient.clear();
    };

    window.addEventListener('jv:dev_user_change', handleUserChange);
    return () => window.removeEventListener('jv:dev_user_change', handleUserChange);
  }, [queryClient]);
}

// ----------------------------------------------------------------------------
// Missions
// ----------------------------------------------------------------------------

export function useMissions() {
  return useQuery({
    queryKey: ['missions'],
    queryFn: async () => {
      const res = await apiRequest<PaginatedResponse<Mission>>('/missions?limit=100');
      return res.data.items;
    },
  });
}

export function useMission(missionId?: string) {
  return useQuery({
    queryKey: ['missions', missionId],
    queryFn: async () => {
      if (!missionId) return null;
      const res = await apiRequest<Mission>(`/missions/${missionId}`);
      return { mission: res.data, etag: res.etag };
    },
    enabled: Boolean(missionId),
  });
}

export function useCreateMission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { title: string; goal: string; kind?: string; constraints?: string[]; success_criteria?: string[] }) => {
      const res = await apiRequest<Mission>('/missions', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['missions'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useTransitionMission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ missionId, newStatus, etag }: { missionId: string; newStatus: string; etag: string }) => {
      const res = await apiRequest<Mission>(
        `/missions/${missionId}/transition`,
        {
          method: 'POST',
          body: JSON.stringify({ new_status: newStatus }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['missions'] });
      queryClient.invalidateQueries({ queryKey: ['missions', vars.missionId] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

// ----------------------------------------------------------------------------
// Cases
// ----------------------------------------------------------------------------

export function useMissionCases(missionId?: string) {
  return useQuery({
    queryKey: ['cases', 'mission', missionId],
    queryFn: async () => {
      if (!missionId) return [];
      // Backend returns CaseResponse[] directly (plain list, not PaginatedResponse)
      const res = await apiRequest<Case[]>(`/missions/${missionId}/cases`);
      return res.data;
    },
    enabled: Boolean(missionId),
  });
}

export function useCase(caseId?: string) {
  return useQuery({
    queryKey: ['cases', caseId],
    queryFn: async () => {
      if (!caseId) return null;
      const res = await apiRequest<Case>(`/cases/${caseId}`);
      return { caseItem: res.data, etag: res.etag };
    },
    enabled: Boolean(caseId),
  });
}

export function useCreateCase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      missionId,
      title,
      goal,
      caseType = 'job_application',
    }: {
      missionId: string;
      title: string;
      goal: string;
      caseType?: string;
    }) => {
      const res = await apiRequest<Case>(`/missions/${missionId}/cases`, {
        method: 'POST',
        body: JSON.stringify({ title, goal, case_type: caseType }),
      });
      return res.data;
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['cases', 'mission', vars.missionId] });
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useTransitionCase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ caseId, newStatus, etag }: { caseId: string; newStatus: string; etag: string }) => {
      const res = await apiRequest<Case>(
        `/cases/${caseId}/transition`,
        {
          method: 'POST',
          body: JSON.stringify({ new_status: newStatus }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['cases', vars.caseId] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useResolveCase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ caseId, outcome, etag }: { caseId: string; outcome: string; etag: string }) => {
      const res = await apiRequest<Case>(
        `/cases/${caseId}/resolve`,
        {
          method: 'POST',
          body: JSON.stringify({ outcome }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['cases', vars.caseId] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

// ----------------------------------------------------------------------------
// Actions
// ----------------------------------------------------------------------------

export function useCaseActions(caseId?: string) {
  return useQuery({
    queryKey: ['actions', 'case', caseId],
    queryFn: async () => {
      if (!caseId) return [];
      // Backend returns ActionResponse[] directly
      const res = await apiRequest<Action[]>(`/cases/${caseId}/actions`);
      return res.data;
    },
    enabled: Boolean(caseId),
  });
}

export function useAction(actionId?: string) {
  return useQuery({
    queryKey: ['actions', actionId],
    queryFn: async () => {
      if (!actionId) return null;
      const res = await apiRequest<Action>(`/actions/${actionId}`);
      return { action: res.data, etag: res.etag };
    },
    enabled: Boolean(actionId),
  });
}

export function useProposeAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      caseId,
      actionType,
      description,
      parameters = {},
    }: {
      caseId: string;
      actionType: string;
      description: string;
      parameters?: Record<string, any>;
    }) => {
      const res = await apiRequest<Action>(`/cases/${caseId}/actions`, {
        method: 'POST',
        body: JSON.stringify({
          action_type: actionType,
          description,
          parameters,
        }),
      });
      return { action: res.data, etag: res.etag };
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['actions', 'case', vars.caseId] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useRequestActionApproval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ actionId, etag }: { actionId: string; etag: string }) => {
      const res = await apiRequest<Approval>(
        `/actions/${actionId}/request-approval`,
        {
          method: 'POST',
        },
        etag
      );
      return { approval: res.data, etag: res.etag };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      queryClient.invalidateQueries({ queryKey: ['actions'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

// ----------------------------------------------------------------------------
// Approvals
// ----------------------------------------------------------------------------

export function useApprovals(statusFilter?: string) {
  return useQuery({
    queryKey: ['approvals', statusFilter],
    queryFn: async () => {
      const url = statusFilter ? `/approvals?status_filter=${statusFilter}` : '/approvals';
      // Backend returns ApprovalResponse[] directly
      const res = await apiRequest<Approval[]>(url);
      return res.data;
    },
  });
}

export function useApproval(approvalId?: string | null) {
  return useQuery({
    queryKey: ['approvals', approvalId],
    queryFn: async () => {
      if (!approvalId) return null;
      const res = await apiRequest<Approval>(`/approvals/${approvalId}`);
      return { approval: res.data, etag: res.etag };
    },
    enabled: Boolean(approvalId),
  });
}

export function useApproveApproval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ approvalId, etag, reason }: { approvalId: string; etag: string; reason?: string }) => {
      const res = await apiRequest<ApprovalDecision>(
        `/approvals/${approvalId}/approve`,
        {
          method: 'POST',
          body: JSON.stringify({ reason: reason || 'Authorized by human operator' }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      queryClient.invalidateQueries({ queryKey: ['actions'] });
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useRejectApproval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ approvalId, etag, reason }: { approvalId: string; etag: string; reason?: string }) => {
      const res = await apiRequest<ApprovalDecision>(
        `/approvals/${approvalId}/reject`,
        {
          method: 'POST',
          body: JSON.stringify({ reason: reason || 'Declined by human operator' }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      queryClient.invalidateQueries({ queryKey: ['actions'] });
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

// ----------------------------------------------------------------------------
// Opportunities
// ----------------------------------------------------------------------------

export function useOpportunities() {
  return useQuery({
    queryKey: ['opportunities'],
    queryFn: async () => {
      const res = await apiRequest<PaginatedResponse<Opportunity>>('/opportunities?limit=100');
      return res.data.items;
    },
  });
}

export function useOpportunity(opportunityId?: string) {
  return useQuery({
    queryKey: ['opportunities', opportunityId],
    queryFn: async () => {
      if (!opportunityId) return null;
      const res = await apiRequest<Opportunity>(`/opportunities/${opportunityId}`);
      return { opportunity: res.data, etag: res.etag };
    },
    enabled: Boolean(opportunityId),
  });
}

export function useCreateOpportunity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateOpportunityRequest) => {
      const res = await apiRequest<Opportunity>('/opportunities', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      return { opportunity: res.data, etag: res.etag };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useTransitionOpportunity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, newStatus, etag, reason }: { id: string; newStatus: string; etag: string; reason?: string }) => {
      const res = await apiRequest<Opportunity>(
        `/opportunities/${id}/transition`,
        {
          method: 'POST',
          body: JSON.stringify({ new_status: newStatus, reason }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['opportunities', vars.id] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

// ----------------------------------------------------------------------------
// Context Vault & Readiness
// ----------------------------------------------------------------------------

export function useContextFacts() {
  return useQuery({
    queryKey: ['context', 'facts'],
    queryFn: async () => {
      const res = await apiRequest<PaginatedResponse<FactSummary>>('/context/facts?limit=100');
      return res.data.items;
    },
  });
}

// On-demand reveal query (staleTime: 0, gcTime: 0 — never retained unnecessarily)
export function useFactDetail(factId: string | null) {
  return useQuery({
    queryKey: ['context', 'facts', 'detail', factId],
    queryFn: async () => {
      if (!factId) return null;
      const res = await apiRequest<FactDetail>(`/context/facts/${factId}`);
      return { detail: res.data, etag: res.etag };
    },
    enabled: Boolean(factId),
    staleTime: 0,
    gcTime: 0,
  });
}

export function useProfileReadiness(purpose: string = 'job_application') {
  return useQuery({
    queryKey: ['context', 'readiness', purpose],
    queryFn: async () => {
      const res = await apiRequest<ProfileReadiness>(`/context/readiness/${purpose}`);
      return res.data;
    },
  });
}

export function useRecordFact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      namespace: string;
      key: string;
      value: any;
      sensitivity?: string;
      source_id?: string;
      confidence?: number;
      allowed_purposes?: string[];
    }) => {
      const res = await apiRequest<FactDetail>('/context/facts', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      return { fact: res.data, etag: res.etag };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['context', 'facts'] });
      queryClient.invalidateQueries({ queryKey: ['context', 'readiness'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useVerifyFact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ factId, status = 'user_verified', etag }: { factId: string; status?: string; etag: string }) => {
      const res = await apiRequest<FactDetail>(
        `/context/facts/${factId}/verify`,
        {
          method: 'POST',
          body: JSON.stringify({ status }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['context', 'facts'] });
      queryClient.invalidateQueries({ queryKey: ['context', 'readiness'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useRejectFact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ factId, reason, etag }: { factId: string; reason?: string; etag: string }) => {
      const res = await apiRequest<FactDetail>(
        `/context/facts/${factId}/reject`,
        {
          method: 'POST',
          body: JSON.stringify({ reason: reason || 'Rejected by human user' }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['context', 'facts'] });
      queryClient.invalidateQueries({ queryKey: ['context', 'readiness'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useSupersedeFact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ factId, payload, etag }: { factId: string; payload: SupersedeFactRequest; etag: string }) => {
      const res = await apiRequest<FactDetail>(
        `/context/facts/${factId}/supersede`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
        etag
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['context', 'facts'] });
      queryClient.invalidateQueries({ queryKey: ['context', 'readiness'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

// ----------------------------------------------------------------------------
// Claims
// ----------------------------------------------------------------------------

export function useClaims(filters?: { case_id?: string; mission_id?: string; status?: string }) {
  return useQuery({
    queryKey: ['claims', filters],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters?.case_id) params.set('case_id', filters.case_id);
      if (filters?.mission_id) params.set('mission_id', filters.mission_id);
      if (filters?.status) params.set('status_filter', filters.status);
      params.set('limit', '100');

      const res = await apiRequest<PaginatedResponse<Claim>>(`/claims?${params.toString()}`);
      return res.data.items;
    },
  });
}

export function useClaim(claimId?: string) {
  return useQuery({
    queryKey: ['claims', claimId],
    queryFn: async () => {
      if (!claimId) return null;
      const res = await apiRequest<Claim>(`/claims/${claimId}`);
      return { claim: res.data, etag: res.etag };
    },
    enabled: Boolean(claimId),
  });
}

export function useProposeClaim() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      purpose: string;
      text: string;
      supporting_fact_ids: string[];
      case_id?: string;
      mission_id?: string;
      auto_evaluate?: boolean;
    }) => {
      const res = await apiRequest<Claim>('/claims', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      return { claim: res.data, etag: res.etag };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['claims'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useEvaluateClaim() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ claimId, etag }: { claimId: string; etag: string }) => {
      const res = await apiRequest<Claim>(
        `/claims/${claimId}/evaluate`,
        {
          method: 'POST',
          body: JSON.stringify({}),
        },
        etag
      );
      return res.data;
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['claims'] });
      queryClient.invalidateQueries({ queryKey: ['claims', vars.claimId] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

export function useRejectClaim() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ claimId, etag, reason }: { claimId: string; etag: string; reason?: string }) => {
      const res = await apiRequest<Claim>(
        `/claims/${claimId}/reject`,
        {
          method: 'POST',
          body: JSON.stringify({ reason: reason || 'Rejected assertion' }),
        },
        etag
      );
      return res.data;
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['claims'] });
      queryClient.invalidateQueries({ queryKey: ['claims', vars.claimId] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
  });
}

// ----------------------------------------------------------------------------
// Activity Events
// ----------------------------------------------------------------------------

export function useEvents(afterPosition?: number) {
  return useQuery({
    queryKey: ['events', afterPosition],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: '50' });
      if (afterPosition !== undefined) params.set('after_position', String(afterPosition));
      const res = await apiRequest<CursorPaginatedEvents>(`/events?${params.toString()}`);
      return res.data;
    },
  });
}
