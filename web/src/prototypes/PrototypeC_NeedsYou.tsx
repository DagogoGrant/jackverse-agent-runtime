import React, { useState } from 'react';
import { DragToAuthorize } from '../components/ui/DragToAuthorize';
import { TactileButton } from '../components/ui/TactileButton';
import { TextureBadge } from '../components/ui/TextureBadge';
import { ShieldCheck, ChevronDown, ChevronUp } from 'lucide-react';

export const PrototypeC_NeedsYou: React.FC = () => {
  const [showParameters, setShowParameters] = useState(true);
  const [authorized, setAuthorized] = useState(false);

  return (
    <div className="space-y-12 font-interface text-paper max-w-4xl">
      {/* Header */}
      <div className="border-b border-grey-700 pb-6 space-y-2">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 font-machine text-xs text-grey-500">
          <span>APPROVAL QUEUE // CONSTITUTIONAL GATE</span>
          <span>1 PENDING DECISION</span>
        </div>
        <h1 className="font-display text-4xl text-pure tracking-tight">
          Requires Your Authorization
        </h1>
      </div>

      {/* Flagship Authorization Dossier */}
      <div className="border border-grey-700 bg-ink p-5 md:p-8 space-y-6 md:space-y-8">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-machine text-xs text-grey-500">ACTION 01 //</span>
            <TextureBadge status="blocked" />
            <span className="font-machine text-xs tracking-wider uppercase text-pure">
              HIGH IMPACT CONFLICT GATE
            </span>
          </div>

          <h2 className="font-interface font-semibold text-2xl lg:text-3xl text-pure tracking-tight">
            Submit Application: Forward-Deployed AI Engineer
          </h2>

          <div className="font-machine text-xs text-grey-300">
            TARGET: NORTHSTAR AI (MUNICH) · CASE: C-102 · MISSION: M-001
          </div>
        </div>

        {/* Narrative Rationale */}
        <div className="border-l-2 border-paper pl-4 py-1 text-sm text-grey-300 leading-relaxed space-y-1">
          <div className="font-medium text-pure">Why your consent is required:</div>
          <div>
            JackVerse prepared an external submission package containing verified
            identity claims and career assets. Under JackVerse security policy,
            external communication and third-party submissions cannot be automated
            without explicit human authorization.
          </div>
        </div>

        {/* Expanding Parameter Inspector */}
        <div className="border border-grey-700">
          <button
            type="button"
            onClick={() => setShowParameters(!showParameters)}
            className="w-full flex items-center justify-between px-4 py-3 bg-canvas text-xs font-machine text-grey-300 hover:text-pure border-b border-grey-700"
          >
            <span>INSPECT SUBMISSION PAYLOAD & ACCESSED FACTS</span>
            {showParameters ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>

          {showParameters && (
            <div className="p-4 space-y-4 font-machine text-xs bg-ink/80">
              <div className="space-y-1">
                <span className="text-grey-500 uppercase">Documents to transmit:</span>
                <ul className="list-disc list-inside text-pure space-y-0.5">
                  <li>Synthetic_CV.pdf (Generated 30 Sep 2026)</li>
                  <li>CoverLetter_Northstar_Platform.pdf</li>
                </ul>
              </div>

              <div className="space-y-1">
                <span className="text-grey-500 uppercase">Verified Vault Facts Referenced:</span>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                  <div className="p-2 border border-grey-700 bg-canvas">
                    <span className="text-grey-500">identity.legal_name:</span>{' '}
                    <span className="text-pure">Alex Mercer</span>
                  </div>
                  <div className="p-2 border border-grey-700 bg-canvas">
                    <span className="text-grey-500">contact.email:</span>{' '}
                    <span className="text-pure">alex@example.test</span>
                  </div>
                  <div className="p-2 border border-grey-700 bg-canvas">
                    <span className="text-grey-500">career.experience:</span>{' '}
                    <span className="text-pure">5 years distributed systems</span>
                  </div>
                  <div className="p-2 border border-grey-700 bg-canvas">
                    <span className="text-grey-500">legal.right_to_work:</span>{' '}
                    <span className="text-pure">Authorized (EU)</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Authorization Interaction Section */}
        <div className="pt-4 border-t border-grey-700 space-y-4">
          {authorized ? (
            <div className="flex items-center gap-3 p-4 border border-pure bg-pure text-canvas font-interface font-medium">
              <ShieldCheck className="w-5 h-5 shrink-0" />
              <span>Authorization recorded. Execution is not connected in this phase.</span>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Bencho-inspired Drag to Authorize Signature Control */}
              <DragToAuthorize
                label="Slide to authorize external submission"
                consequentialDescription="Consequential action: This will record your authorization for external dispatch."
                onAuthorize={() => setAuthorized(true)}
              />

              <div className="flex items-center justify-end gap-4 pt-2">
                <TactileButton variant="danger" size="md">
                  Reject Proposal ✕
                </TactileButton>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
