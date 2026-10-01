/**
 * Seed Demo Data for JackVerse Caseworker
 * Deterministic synthetic demo records for Alice and Bob.
 * NO real personal data.
 */

const API_BASE = process.env.CASEWORKER_API_URL || "http://127.0.0.1:8088/api/v1";

interface RequestOptions {
  user: string;
  method?: string;
  body?: any;
  etag?: string;
}

interface RequestResult<T = any> {
  data: T;
  etag: string | null;
}

async function request<T = any>(endpoint: string, opts: RequestOptions): Promise<RequestResult<T>> {
  const url = `${API_BASE}${endpoint}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-JackVerse-User": opts.user,
  };
  if (opts.etag) {
    headers["If-Match"] = opts.etag;
  }

  const res = await fetch(url, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  const etag = res.headers.get("ETag");

  if (!res.ok) {
    let errBody = "";
    try {
      errBody = await res.text();
    } catch {}
    throw new Error(`[${opts.user}] ${opts.method || "GET"} ${endpoint} failed with ${res.status}: ${errBody}`);
  }

  if (res.status === 204) {
    return { data: null as any, etag };
  }

  const data = await res.json();
  return { data: data as T, etag };
}

async function seedUserAlice() {
  console.log("--> Seeding Alice (Senior AI Systems Architect)...");

  // 1. Context Facts
  const facts = [
    { namespace: "personal", key: "full_name", value: "Alice Vance", sensitivity: "personal", allowed_purposes: ["job_application", "general"], source_reference: "passport_scan_2025.pdf" },
    { namespace: "contact", key: "email", value: "alice.vance@example.org", sensitivity: "personal", allowed_purposes: ["job_application", "general"], source_reference: "verified_email_token" },
    { namespace: "career", key: "primary_role", value: "Senior AI Systems Architect", sensitivity: "personal", allowed_purposes: ["job_application"], source_reference: "linkedin_profile" },
    { namespace: "career", key: "years_experience", value: 6, sensitivity: "personal", allowed_purposes: ["job_application"], source_reference: "employment_record_2026" },
    { namespace: "career", key: "github", value: "github.com/alice-vance-ai", sensitivity: "public", allowed_purposes: ["job_application", "general"], source_reference: "user_entry" },
    { namespace: "career", key: "cv_summary", value: "Specialized in deterministic agent runtimes, tool protocols, and safe multi-agent execution.", sensitivity: "personal", allowed_purposes: ["job_application"], source_reference: "cv_v4.pdf" },
    { namespace: "legal", key: "work_authorization", value: "European Union (Permanent)", sensitivity: "sensitive", allowed_purposes: ["job_application"], source_reference: "residence_permit_scan" },
    { namespace: "compensation", key: "salary_expectation_eur", value: 135000, sensitivity: "sensitive", allowed_purposes: ["job_application"], source_reference: "user_input" },
  ];

  for (const f of facts) {
    try {
      await request("/context/facts", {
        user: "alice",
        method: "POST",
        body: {
          namespace: f.namespace,
          key: f.key,
          value: f.value,
          sensitivity: f.sensitivity,
          allowed_purposes: f.allowed_purposes,
          source_type: "document",
          source_reference: f.source_reference,
        },
      });
      console.log(`    ✓ Fact ${f.namespace}.${f.key}`);
    } catch (e: any) {
      console.log(`    · Fact ${f.namespace}.${f.key}: ${e.message}`);
    }
  }

  // 2. Missions
  let missionId = "";
  try {
    const res = await request("/missions", {
      user: "alice",
      method: "POST",
      body: {
        title: "Lead Autonomous Systems Role in Berlin",
        kind: "opportunity_pursuit",
        goal: "Secure lead architecture role in production agent infrastructure in Berlin or remote EU",
      },
    });
    missionId = res.data.mission_id;
    console.log(`    ✓ Mission: ${res.data.title} (${missionId})`);
  } catch (e: any) {
    console.log(`    · Mission: ${e.message}`);
  }

  // 3. Cases under Mission
  if (missionId) {
    try {
      const c1 = await request(`/missions/${missionId}/cases`, {
        user: "alice",
        method: "POST",
        body: {
          title: "Applied AI Systems / Principal Lead",
          case_type: "job_application",
          goal: "Apply for principal agent lead position",
        },
      });
      console.log(`    ✓ Case: ${c1.data.title} (${c1.data.case_id})`);

      // Add a proposed action needing authorization
      const act = await request(`/cases/${c1.data.case_id}/actions`, {
        user: "alice",
        method: "POST",
        body: {
          action_type: "submit_form",
          description: "Submit comprehensive candidacy packet and verified credentials to hiring team",
          parameters: {
            recipient_email: "careers@applied-ai-systems.de",
            attachments: ["CV_Alice_Vance_2026.pdf", "Work_Authorization_DE.pdf"],
          },
        },
      });
      console.log(`    ✓ Consequential Action created (${act.data.action_id})`);

      // Request approval so an Approval record is created
      if (act.etag) {
        await request(`/actions/${act.data.action_id}/request-approval`, {
          user: "alice",
          method: "POST",
          etag: act.etag,
        });
        console.log(`    ✓ Approval requested under Action ETag (${act.data.action_id})`);
      }
    } catch (e: any) {
      console.log(`    · Case / Action: ${e.message}`);
    }

    try {
      const c2 = await request(`/missions/${missionId}/cases`, {
        user: "alice",
        method: "POST",
        body: {
          title: "QuantAgentics / Core Runtime Engineer",
          case_type: "job_application",
          goal: "Explore quantitative agent runtime opportunity",
        },
      });
      console.log(`    ✓ Case: ${c2.data.title} (${c2.data.case_id})`);
    } catch (e: any) {
      console.log(`    · Case: ${e.message}`);
    }
  }

  // 4. Opportunities
  const opps = [
    {
      title: "Founding Engineer - Autonomous Agent Platform",
      opportunity_type: "job",
      organization: "Stealth AI",
      location: "Berlin, Germany / Remote",
      source_name: "Hacker News",
      source_url: "https://news.ycombinator.com/item?id=410022",
      requirements: ["5+ years distributed systems", "Experience with agent runtimes"],
    },
    {
      title: "Staff Infrastructure Engineer - Agent Runtime",
      opportunity_type: "job",
      organization: "Berlin Tech Ventures",
      location: "Berlin, Germany",
      source_name: "Berlin Startup Jobs",
      source_url: "https://berlinstartupjobs.com/engineering/btb-8891",
      requirements: ["High-throughput execution engines", "Python / Rust runtime experience"],
    },
  ];

  for (const op of opps) {
    try {
      const res = await request("/opportunities", {
        user: "alice",
        method: "POST",
        body: op,
      });
      console.log(`    ✓ Opportunity: ${op.title} (${res.data.opportunity_id})`);
    } catch (e: any) {
      console.log(`    · Opportunity: ${e.message}`);
    }
  }

  // 5. Claims
  const claims = [
    { purpose: "job_application", text: "6 years production experience scaling agentic LLM platforms", mission_id: missionId || null, supporting_fact_ids: [] },
    { purpose: "job_application", text: "Legally authorized to work full-time in Germany without sponsorship", mission_id: missionId || null, supporting_fact_ids: [] },
  ];

  for (const cl of claims) {
    try {
      const res = await request("/claims", {
        user: "alice",
        method: "POST",
        body: cl,
      });
      console.log(`    ✓ Claim: "${cl.text.slice(0, 45)}..." (${res.data.claim_id})`);
    } catch (e: any) {
      console.log(`    · Claim: ${e.message}`);
    }
  }
}

async function seedUserBob() {
  console.log("\n--> Seeding Bob (Full-Stack AI Developer)...");

  // 1. Context Facts
  const facts = [
    { namespace: "personal", key: "full_name", value: "Bob Stone", sensitivity: "personal", allowed_purposes: ["job_application", "general"], source_reference: "id_card_2024.pdf" },
    { namespace: "contact", key: "email", value: "bob.stone@example.net", sensitivity: "personal", allowed_purposes: ["job_application", "general"], source_reference: "verified_email_token" },
    { namespace: "career", key: "primary_role", value: "Full-Stack AI Application Developer", sensitivity: "personal", allowed_purposes: ["job_application"], source_reference: "github_profile" },
    { namespace: "career", key: "years_experience", value: 3, sensitivity: "personal", allowed_purposes: ["job_application"], source_reference: "cv_2026.pdf" },
  ];

  for (const f of facts) {
    try {
      await request("/context/facts", {
        user: "bob",
        method: "POST",
        body: {
          namespace: f.namespace,
          key: f.key,
          value: f.value,
          sensitivity: f.sensitivity,
          allowed_purposes: f.allowed_purposes,
          source_type: "document",
          source_reference: f.source_reference,
        },
      });
      console.log(`    ✓ Fact ${f.namespace}.${f.key}`);
    } catch (e: any) {
      console.log(`    · Fact ${f.namespace}.${f.key}: ${e.message}`);
    }
  }

  // 2. Mission
  try {
    const res = await request("/missions", {
      user: "bob",
      method: "POST",
      body: {
        title: "Relocate to Munich AI Cluster",
        kind: "opportunity_pursuit",
        goal: "Secure full-stack AI engineering role in Munich with relocation support",
      },
    });
    console.log(`    ✓ Mission: ${res.data.title} (${res.data.mission_id})`);
  } catch (e: any) {
    console.log(`    · Mission: ${e.message}`);
  }

  // 3. Opportunity
  try {
    const res = await request("/opportunities", {
      user: "bob",
      method: "POST",
      body: {
        title: "Mid-level Agent Engineer at Munich Labs",
        opportunity_type: "job",
        organization: "Munich Labs",
        location: "Munich, Germany",
        source_name: "Munich AI Hub",
        source_url: "https://munich-ai.org/jobs/102",
        requirements: ["Python", "React", "Human-in-the-loop workflows"],
      },
    });
    console.log(`    ✓ Opportunity: ${res.data.title} (${res.data.opportunity_id})`);
  } catch (e: any) {
    console.log(`    · Opportunity: ${e.message}`);
  }
}

async function main() {
  console.log("=================================================");
  console.log("  JackVerse Caseworker: Synthetic Demo Seeder   ");
  console.log(`  Target API: ${API_BASE}`);
  console.log("=================================================");

  try {
    const health = await fetch(`${API_BASE.replace("/api/v1", "")}/health`);
    if (!health.ok) {
      console.warn("⚠️ API health check returned non-200. Proceeding anyway...");
    } else {
      console.log("✓ API connection established.");
    }
  } catch (e: any) {
    console.warn(`⚠️ Could not reach ${API_BASE}: ${e.message}`);
    console.log("Ensure the caseworker backend is running before invoking seed in production/docker.");
    process.exit(0);
  }

  await seedUserAlice();
  await seedUserBob();
  console.log("\n=================================================");
  console.log("✓ Demo dataset population completed successfully.");
  console.log("=================================================");
}

main().catch((err) => {
  console.error("Seeding encountered an error:", err);
  process.exit(1);
});
