export type MissionKind = 'opportunity_pursuit' | 'capability_expansion' | 'dispute_resolution' | 'ongoing_monitoring';
export type MissionStatus = 'draft' | 'active' | 'paused' | 'completed' | 'cancelled' | 'failed';

export interface Mission {
  mission_id: string;
  user_id: string;
  kind: MissionKind | string;
  title: string;
  goal: string;
  status: MissionStatus | string;
  constraints: string[];
  success_criteria: string[];
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  outcome: string | null;
  version: number;
}

export type CaseType = 'job_application' | 'housing_search' | 'grant_submission' | 'visa_processing' | 'dispute' | 'general';
export type CaseStatus =
  | 'new'
  | 'intake'
  | 'investigating'
  | 'planning'
  | 'action_required'
  | 'awaiting_approval'
  | 'action_in_progress'
  | 'waiting_external'
  | 'follow_up_due'
  | 'resolved'
  | 'blocked'
  | 'escalated'
  | 'cancelled';

export interface Case {
  case_id: string;
  mission_id: string | null;
  user_id: string;
  case_type: CaseType | string;
  title: string;
  goal: string;
  status: CaseStatus | string;
  success_criteria: string[];
  constraints: string[];
  created_at: string;
  updated_at: string;
  deadline: string | null;
  resolved_at: string | null;
  outcome: string | null;
  version: number;
}

export type OpportunityStatus =
  | 'discovered'
  | 'normalized'
  | 'evaluating'
  | 'shortlisted'
  | 'converted_to_case'
  | 'archived'
  | 'rejected';

export interface Opportunity {
  opportunity_id: string;
  user_id: string;
  opportunity_type: string;
  organization: string;
  title: string;
  description: string;
  requirements: string[];
  source_url: string;
  source_reference: string;
  status: OpportunityStatus | string;
  fingerprint: string;
  created_at: string;
  updated_at: string;
  deadline: string | null;
  version: number;
}

export interface FactSummary {
  fact_id: string;
  user_id: string;
  namespace: string;
  key: string;
  sensitivity: 'public' | 'personal' | 'sensitive';
  verification_status: 'unverified' | 'user_verified' | 'source_verified' | 'rejected';
  source_id: string | null;
  source_type: string;
  confidence: number;
  updated_at: string;
  expires_at: string | null;
  version: number;
  has_value: boolean;
  preview: string | null;
}

export interface FactDetail extends FactSummary {
  value: any;
  source_reference: string;
  rejection_reason: string | null;
  allowed_purposes: string[];
  created_at: string;
  superseded_by_fact_id: string | null;
}

export interface RequirementItem {
  requirement_id: string;
  purpose: string;
  namespace: string;
  key: string;
  label: string;
  is_mandatory: boolean;
  minimum_verification: string;
}

export interface ProfileReadiness {
  purpose: string;
  title: string;
  is_ready: boolean;
  completeness_ratio: number;
  satisfied_count: number;
  missing_count: number;
  unverifiable_count: number;
  expired_count: number;
  satisfied: RequirementItem[];
  missing: RequirementItem[];
  unverifiable: RequirementItem[];
  expired: RequirementItem[];
}

export type ClaimStatus = 'proposed' | 'supported' | 'unsupported' | 'conflicted';

export interface Claim {
  claim_id: string;
  user_id: string;
  purpose: string;
  text: string;
  case_id: string | null;
  mission_id: string | null;
  status: ClaimStatus | string;
  supporting_fact_ids: string[];
  created_at: string;
  updated_at: string;
  verified_at: string | null;
  rejection_reason: string | null;
  version: number;
}

export type ActionStatus = 'proposed' | 'awaiting_approval' | 'approved' | 'rejected' | 'in_progress' | 'completed' | 'failed' | 'cancelled';
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';

export interface Action {
  action_id: string;
  case_id: string;
  action_type: string;
  description: string;
  status: ActionStatus | string;
  risk_level: RiskLevel | string;
  requires_approval: boolean;
  parameters: Record<string, any>;
  result: Record<string, any>;
  created_at: string;
  executed_at: string | null;
  version: number;
}

export type ApprovalStatus = 'pending' | 'approved' | 'rejected';

export interface Approval {
  approval_id: string;
  action_id: string;
  user_id: string;
  action_fingerprint: string;
  status: ApprovalStatus | string;
  decision_reason: string | null;
  decided_at: string | null;
  decided_by: string | null;
  created_at: string;
  version: number;
  action?: Action;
}

export interface DomainEvent {
  event_id: string;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  aggregate_version: number;
  user_id: string;
  timestamp: string;
  payload: Record<string, any>;
  position?: number;
}

export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  invalid_params?: Array<{ name: string; reason: string }>;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface CursorPaginatedEvents {
  items: DomainEvent[];
  limit: number;
  after_position: number | null;
  next_position: number | null;
}
