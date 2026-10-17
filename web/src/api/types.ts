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
export type ShortlistOpportunityRequest = Schemas['ShortlistOpportunityRequest'];
export type ConvertToCaseRequest = Schemas['ConvertToCaseRequest'];
export type ConvertToCaseResponse = Schemas['ConvertToCaseResponse'];
export type EvaluationSummary = Schemas['EvaluationSummary'];
export type EvaluationSnapshotResponse = Schemas['EvaluationSnapshotResponse'];
export type EligibilityResultResponse = Schemas['EligibilityResultResponse'];
export type FitScoreComponentsResponse = Schemas['FitScoreComponentsResponse'];
export type RequirementEvidenceResponse = Schemas['RequirementEvidenceResponse'];
export type NormalizeOpportunityResponse = Schemas['NormalizeOpportunityResponse'];
export type NormalizedRequirementResponse = Schemas['NormalizedRequirementResponse'];

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
// Preparation Intelligence
// ----------------------------------------------------------------------------
export type PreparationIntelligenceReport = Schemas['PreparationIntelligenceReportResponse'];
export type PreparationStrategy = Schemas['PreparationStrategyResponse'];
export type EvidenceMappingItem = Schemas['EvidenceMappingItemResponse'];
export type GapItem = Schemas['GapItemResponse'];
export type ClaimIntent = Schemas['ClaimIntentResponse'];

// ----------------------------------------------------------------------------
// Prepared Application Artifacts (Phase 5.2.2)
// ----------------------------------------------------------------------------
export type PreparedArtifact = Schemas['PreparedArtifactResponse'];
export type PreparedArtifactList = Schemas['PreparedArtifactListResponse'];
export type ArtifactBlock = Schemas['ArtifactBlockResponse'];
export type ArtifactProvenance = Schemas['ArtifactProvenanceResponse'];

// ----------------------------------------------------------------------------
// Preparation Package & Verification (Phase 5.2.3)
// ----------------------------------------------------------------------------
export type PreparationPackage = Schemas['PreparationPackageResponse'];
export type VerifyPackageRequest = Schemas['VerifyPackageRequest'];
export type VerifyPackageResponse = Schemas['VerifyPackageResponse'];
export type VerificationFinding = Schemas['VerificationFindingResponse'];
export type ArtifactPackageSnapshot = Schemas['ArtifactPackageSnapshotResponse'];
export type SafeClaimSnapshot = Schemas['SafeClaimSnapshotResponse'];

// ----------------------------------------------------------------------------
// External Execution Plans (Phase 5.3.1)
// ----------------------------------------------------------------------------
export type ExternalExecutionPlan = Schemas['ExternalExecutionPlanResponse'];
export type CreateExecutionPlanRequest = Schemas['CreateExecutionPlanRequest'];
export type ExecutionDestination = Schemas['ExecutionDestinationSchema'];
export type ExecutionFieldMapping = Schemas['ExecutionFieldMappingResponse'];
export type ExecutionUploadAsset = Schemas['ExecutionUploadAssetResponse'];
export type ExecutionScreeningResponse = Schemas['ExecutionScreeningResponseDTO'];
export type ExecutionPlanFinding = Schemas['ExecutionPlanFindingResponse'];
export type TargetInteractionSchema = Schemas['TargetInteractionSchemaSchema'];
export type TargetFieldBinding = Schemas['TargetFieldBindingSchema'];
export type TargetSchemaField = Schemas['TargetSchemaFieldSchema'];
export type TargetSchemaQuestion = Schemas['TargetSchemaQuestionSchema'];
export type TargetUploadSlot = Schemas['TargetSchemaUploadSlotSchema'];
export type TargetSubmitControlSpec = Schemas['TargetSubmitControlSpecSchema'];


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
