/**
 * Frontend Domain and Wire Contracts.
 * Authoritative types are derived directly from the generated OpenAPI schema.
 */

import type { components } from './generated-schema';

export type Schemas = components['schemas'];

// ----------------------------------------------------------------------------
// Mission
// ----------------------------------------------------------------------------
export type MissionKind = Schemas['MissionKind'];
export type MissionStatus = Schemas['MissionStatus'];
export type Mission = Schemas['MissionResponse'];
export type CreateMissionRequest = Schemas['CreateMissionRequest'];
export type TransitionMissionRequest = Schemas['TransitionMissionRequest'];

// ----------------------------------------------------------------------------
// Case
// ----------------------------------------------------------------------------
export type CaseType = Schemas['CaseType'];
export type CaseStatus = Schemas['CaseStatus'];
export type Case = Schemas['CaseResponse'];
export type CreateCaseRequest = Schemas['CreateCaseRequest'];
export type TransitionCaseRequest = Schemas['TransitionCaseRequest'];
export type ResolveCaseRequest = Schemas['ResolveCaseRequest'];

// ----------------------------------------------------------------------------
// Opportunity
// ----------------------------------------------------------------------------
export type OpportunityType = Schemas['OpportunityType'];
export type OpportunityStatus = Schemas['OpportunityStatus'];
export type Opportunity = Schemas['OpportunityResponse'];
export type CreateOpportunityRequest = Schemas['CreateOpportunityRequest'];
export type TransitionOpportunityRequest = Schemas['TransitionOpportunityRequest'];

// ----------------------------------------------------------------------------
// Action
// ----------------------------------------------------------------------------
export type ActionStatus = Schemas['ActionStatus'];
export type RiskLevel = Schemas['RiskLevel'];
export type Action = Schemas['ActionResponse'];
export type ProposeActionRequest = Schemas['ProposeActionRequest'];

// ----------------------------------------------------------------------------
// Approval
// ----------------------------------------------------------------------------
export type ApprovalStatus = Schemas['ApprovalStatus'];
export type Approval = Schemas['ApprovalResponse'];
export type ApproveActionRequest = Schemas['ApproveActionRequest'];
export type RejectActionRequest = Schemas['RejectActionRequest'];
export type ApprovalDecision = Schemas['ApprovalDecisionResponse'];

// ----------------------------------------------------------------------------
// Context Vault & Fact
// ----------------------------------------------------------------------------
export type SensitivityLevel = Schemas['SensitivityLevel'];
export type VerificationStatus = Schemas['VerificationStatus'];
export type SourceType = Schemas['SourceType'];
export type FactSummary = Schemas['FactSummaryResponse'];
export type FactDetail = Schemas['FactResponse'];
export type SafeSource = Schemas['SafeSourceResponse'];
export type RequirementItem = Schemas['RequirementItem'];
export type ProfileReadiness = Schemas['ProfileReadinessResponse'];
export type RecordFactRequest = Schemas['RecordFactRequest'];
export type VerifyFactRequest = Schemas['VerifyFactRequest'];
export type RejectFactRequest = Schemas['RejectFactRequest'];
export type SupersedeFactRequest = Schemas['SupersedeFactRequest'];

// ----------------------------------------------------------------------------
// Claim
// ----------------------------------------------------------------------------
export type ClaimStatus = Schemas['ClaimStatus'];
export type Claim = Schemas['ClaimResponse'];
export type ProposeClaimRequest = Schemas['ProposeClaimRequest'];
export type EvaluateClaimRequest = Schemas['EvaluateClaimRequest'];
export type RejectClaimRequest = Schemas['RejectClaimRequest'];

// ----------------------------------------------------------------------------
// Activity & Domain Events
// ----------------------------------------------------------------------------
export type DomainEvent = Schemas['DomainEventResponse'];

// ----------------------------------------------------------------------------
// Common Envelopes & UI Models
// ----------------------------------------------------------------------------
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface CursorPaginatedEvents {
  items: DomainEvent[];
  limit: number;
  after_position?: number | null;
  next_position?: number | null;
  has_more?: boolean;
}

export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  invalid_params?: Array<{ name: string; reason: string }>;
}
