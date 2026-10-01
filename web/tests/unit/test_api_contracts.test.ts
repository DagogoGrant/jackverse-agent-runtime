import { describe, it, expect } from "vitest";
import fs from "fs";
import path from "path";

describe("Authoritative OpenAPI Contract Invariants", () => {
  const openapiPath = path.resolve(__dirname, "../../openapi.json");
  const openapi = JSON.parse(fs.readFileSync(openapiPath, "utf-8"));
  const schemas = openapi.components.schemas;
  const paths = openapi.paths;

  it("authoritative MissionKind enum values are opportunity_pursuit, problem_resolution, general_goal", () => {
    expect(schemas.MissionKind).toBeDefined();
    expect(schemas.MissionKind.enum).toEqual([
      "opportunity_pursuit",
      "problem_resolution",
      "general_goal",
    ]);
  });

  it("GET /api/v1/missions/{mission_id}/cases returns an array of CaseResponse, not a paginated envelope", () => {
    const route = paths["/api/v1/missions/{mission_id}/cases"];
    expect(route).toBeDefined();
    const get200 = route.get.responses["200"];
    const schema = get200.content["application/json"].schema;
    expect(schema.type).toBe("array");
    expect(schema.items.$ref).toContain("CaseResponse");
  });

  it("GET /api/v1/cases/{case_id}/actions returns an array of ActionResponse", () => {
    const route = paths["/api/v1/cases/{case_id}/actions"];
    expect(route).toBeDefined();
    const get200 = route.get.responses["200"];
    const schema = get200.content["application/json"].schema;
    expect(schema.type).toBe("array");
    expect(schema.items.$ref).toContain("ActionResponse");
  });

  it("GET /api/v1/approvals returns an array of ApprovalResponse and does NOT embed Action", () => {
    const route = paths["/api/v1/approvals"];
    expect(route).toBeDefined();
    const get200 = route.get.responses["200"];
    const schema = get200.content["application/json"].schema;
    expect(schema.type).toBe("array");
    expect(schema.items.$ref).toContain("ApprovalResponse");

    const approvalSchema = schemas.ApprovalResponse;
    expect(approvalSchema.properties.action).toBeUndefined();
    expect(approvalSchema.properties.action_id).toBeDefined();
    expect(approvalSchema.properties.action_fingerprint).toBeDefined();
  });

  it("POST /api/v1/approvals/{approval_id}/approve and /reject exist and return ApprovalDecisionResponse", () => {
    const approveRoute = paths["/api/v1/approvals/{approval_id}/approve"];
    expect(approveRoute).toBeDefined();
    expect(approveRoute.post).toBeDefined();
    const approveResp = approveRoute.post.responses["200"].content["application/json"].schema;
    expect(approveResp.$ref).toContain("ApprovalDecisionResponse");

    const rejectRoute = paths["/api/v1/approvals/{approval_id}/reject"];
    expect(rejectRoute).toBeDefined();
    expect(rejectRoute.post).toBeDefined();
    const rejectResp = rejectRoute.post.responses["200"].content["application/json"].schema;
    expect(rejectResp.$ref).toContain("ApprovalDecisionResponse");
  });

  it("OpportunityResponse matches backend fields and omits fictional description", () => {
    const opp = schemas.OpportunityResponse.properties;
    expect(opp.opportunity_id).toBeDefined();
    expect(opp.title).toBeDefined();
    expect(opp.organization).toBeDefined();
    expect(opp.location).toBeDefined();
    expect(opp.source_name).toBeDefined();
    expect(opp.source_url).toBeDefined();
    expect(opp.discovered_at).toBeDefined();
    expect(opp.requirements).toBeDefined();
    // Fictional fields must not exist
    expect(opp.description).toBeUndefined();
    expect(opp.created_at).toBeUndefined();
  });

  it("FactSummaryResponse omits raw value and source_reference", () => {
    const factSummary = schemas.FactSummaryResponse.properties;
    expect(factSummary.fact_id).toBeDefined();
    expect(factSummary.namespace).toBeDefined();
    expect(factSummary.key).toBeDefined();
    expect(factSummary.sensitivity).toBeDefined();
    expect(factSummary.verification_status).toBeDefined();
    expect(factSummary.has_value).toBeDefined();
    // Raw sensitive fields omitted in collection summary
    expect(factSummary.value).toBeUndefined();
    expect(factSummary.source_reference).toBeUndefined();
  });

  it("Profile readiness route exists for purpose evaluation", () => {
    const readinessRoute = paths["/api/v1/context/readiness/{purpose}"];
    expect(readinessRoute).toBeDefined();
    expect(readinessRoute.get).toBeDefined();
  });
});
