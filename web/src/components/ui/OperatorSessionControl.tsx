import React, { useState } from 'react';
import {
  useCreateOperatorSession,
  useDeleteOperatorSession,
  useGoogleAuthStatus,
  useAuthorizeGoogleAuth,
  useDisconnectGoogleAuth,
} from '../../hooks/useCaseworker';
import { TactileButton } from './TactileButton';

export const OperatorSessionControl: React.FC = () => {
  const { data: googleAuth, isError, isLoading, refetch } = useGoogleAuthStatus();
  const createOperatorSession = useCreateOperatorSession();
  const deleteOperatorSession = useDeleteOperatorSession();
  const authorizeGoogle = useAuthorizeGoogleAuth();
  const disconnectGoogle = useDisconnectGoogleAuth();

  const [isOpen, setIsOpen] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [authenticatedOperatorId, setAuthenticatedOperatorId] = useState<string | null>(null);

  // Authenticated if operator session was created or googleAuth endpoint returned 200
  const isAuthenticated = Boolean(authenticatedOperatorId) || (!isError && !isLoading && googleAuth !== undefined);
  const isGoogleConnected = Boolean(googleAuth?.connected);
  const authenticatedEmail = googleAuth?.authenticated_email || null;
  const scopes = googleAuth?.scopes || [];
  const hasSendScope = scopes.some((s) => s.includes('gmail.send'));

  const handleOpen = () => {
    setErrorMsg(null);
    setSuccessMsg(null);
    setTokenInput('');
    setIsOpen(true);
  };

  const handleClose = () => {
    setTokenInput('');
    setErrorMsg(null);
    setSuccessMsg(null);
    setIsOpen(false);
  };

  const handleAuthenticate = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setSuccessMsg(null);

    const cleanToken = tokenInput.trim();
    if (!cleanToken) {
      setErrorMsg('Please enter a signed operator token.');
      return;
    }

    try {
      const res = await createOperatorSession.mutateAsync({ operator_token: cleanToken });
      setTokenInput(''); // Immediately clear credentials from memory
      setAuthenticatedOperatorId(res.operator_id ?? null);
      setSuccessMsg(`Session established for operator '${res.operator_id}'.`);
      await refetch();
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to establish operator session.');
    }
  };

  const handleLogout = async () => {
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      await deleteOperatorSession.mutateAsync();
      setAuthenticatedOperatorId(null);
      await refetch();
      setSuccessMsg('Operator session terminated.');
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Logout failed.');
    }
  };

  const handleConnectGoogle = async () => {
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      const res = await authorizeGoogle.mutateAsync();
      if (res?.authorization_url) {
        window.location.href = res.authorization_url;
      } else {
        setErrorMsg('Authorization URL was not returned by server.');
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to initiate Google authorization.');
    }
  };

  const handleDisconnectGoogle = async () => {
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      await disconnectGoogle.mutateAsync();
      await refetch();
      setSuccessMsg('Google account disconnected successfully.');
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to disconnect Google account.');
    }
  };

  return (
    <div className="relative inline-flex items-center">
      {/* Trigger Button in Application Header */}
      {isAuthenticated ? (
        <button
          type="button"
          onClick={handleOpen}
          className="flex items-center gap-1.5 px-2 py-0.5 border border-emerald-500/40 bg-emerald-950/20 hover:bg-emerald-950/30 text-emerald-400 text-[10px] sm:text-[11px] font-machine cursor-pointer transition-colors"
          title="Manage Operator Session & Google Connection"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block animate-pulse" />
          <span className="font-bold">OPERATOR</span>
          {isGoogleConnected ? (
            <span className="hidden sm:inline text-emerald-300">· {authenticatedEmail}</span>
          ) : (
            <span className="hidden sm:inline text-amber-400 font-normal">· CONNECT GOOGLE</span>
          )}
        </button>
      ) : (
        <button
          type="button"
          onClick={handleOpen}
          className="flex items-center gap-1.5 px-2 py-0.5 border border-jv-rule hover:border-jv-ink text-jv-ink text-[10px] sm:text-[11px] font-machine uppercase bg-jv-surface transition-colors cursor-pointer"
          title="Authenticate Operator Session"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />
          <span>Operator Session</span>
        </button>
      )}

      {/* Modal Dialog */}
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
            className="w-full max-w-lg border border-jv-rule bg-jv-bg p-6 text-jv-ink shadow-2xl relative"
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between border-b border-jv-rule pb-3 mb-4">
              <h3 id="modal-title" className="font-machine text-xs uppercase tracking-widest text-jv-ink font-bold flex items-center gap-2">
                <span className="w-2 h-2 bg-jv-ink inline-block" />
                {isAuthenticated ? 'Operator & Google Account Management' : 'Operator Session Bootstrap'}
              </h3>
              <button
                type="button"
                onClick={handleClose}
                className="text-jv-muted hover:text-jv-ink text-sm font-machine cursor-pointer"
                aria-label="Close dialog"
              >
                ✕
              </button>
            </div>

            {/* Status alerts */}
            {errorMsg && (
              <div className="mb-4 p-2.5 border border-rose-500/40 bg-rose-950/20 text-rose-400 text-xs font-machine leading-relaxed">
                {errorMsg}
              </div>
            )}
            {successMsg && (
              <div className="mb-4 p-2.5 border border-emerald-500/40 bg-emerald-950/20 text-emerald-400 text-xs font-machine leading-relaxed">
                {successMsg}
              </div>
            )}

            {/* Body: Authenticated vs Unauthenticated */}
            {!isAuthenticated ? (
              /* Unauthenticated: Operator Session Bootstrap Form */
              <form onSubmit={handleAuthenticate} className="space-y-4">
                {typeof window !== 'undefined' && window.location.protocol === 'http:' && (
                  <div className="p-2.5 border border-amber-500/40 bg-amber-950/20 text-amber-300 text-xs font-machine leading-relaxed space-y-2">
                    <div>
                      ⚠️ <strong>HTTPS Required:</strong> You are accessing JackVerse over insecure HTTP (port 3001). Production operator authentication requires HTTPS at <code className="text-amber-200">https://localhost:3443</code>.
                    </div>
                    <div>
                      <a
                        href={`https://${window.location.hostname}:3443${window.location.pathname}${window.location.search}`}
                        className="inline-block px-2.5 py-1 bg-amber-500 text-black font-bold uppercase text-[10px] tracking-wider hover:bg-amber-400 cursor-pointer"
                      >
                        Switch to https://localhost:3443
                      </a>
                    </div>
                  </div>
                )}
                <p className="text-xs text-jv-muted font-interface leading-relaxed">
                  Authenticate this browser session via <code className="bg-jv-surface px-1 py-0.5 text-jv-ink">POST /api/v1/auth/operator/session</code>.
                  Sets an HTTP-only secure session cookie at <code className="bg-jv-surface px-1 py-0.5 text-jv-ink">https://localhost:3443</code>.
                </p>

                <div>
                  <label htmlFor="operator-token-input" className="block text-[11px] font-machine uppercase tracking-wider text-jv-muted mb-1.5">
                    Signed Operator Token
                  </label>
                  <input
                    id="operator-token-input"
                    type="password"
                    autoComplete="off"
                    spellCheck="false"
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    placeholder="op.<operator_id>.<expires>.<signature>"
                    className="w-full px-3 py-2 bg-jv-surface border border-jv-rule focus:border-jv-ink focus:outline-none text-xs font-mono text-jv-ink"
                    disabled={createOperatorSession.isPending}
                  />
                  <span className="block mt-1 text-[10px] text-jv-muted font-machine">
                    Generate via: <code className="text-jv-ink">python3 scripts/generate_operator_token.py</code>. Credentials are kept out of URLs and localStorage.
                  </span>
                </div>

                <div className="flex items-center justify-end gap-2 pt-2 border-t border-jv-rule">
                  <TactileButton
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleClose}
                    disabled={createOperatorSession.isPending}
                  >
                    Cancel
                  </TactileButton>
                  <TactileButton
                    type="submit"
                    variant="primary"
                    size="sm"
                    loading={createOperatorSession.isPending}
                    disabled={!tokenInput.trim() || createOperatorSession.isPending}
                  >
                    Authenticate Session
                  </TactileButton>
                </div>
              </form>
            ) : (
              /* Authenticated: Operator Session & Independent Google OAuth Controls */
              <div className="space-y-5">
                {/* Section 1: Operator Session Info */}
                <div className="p-3 border border-jv-rule bg-jv-surface/50 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-machine uppercase tracking-wider text-jv-muted">
                      Operator Session
                    </span>
                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 text-[10px] font-machine uppercase tracking-wider border border-emerald-500/40 bg-emerald-950/20 text-emerald-400">
                      <span className="w-1 h-1 rounded-full bg-emerald-400" />
                      Active (Secure Cookie)
                    </span>
                  </div>
                  <div className="flex items-center justify-between pt-1">
                    <span className="text-xs text-jv-muted">Terminate active browser operator session:</span>
                    <TactileButton
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={handleLogout}
                      disabled={deleteOperatorSession.isPending}
                      className="text-[11px]"
                    >
                      {deleteOperatorSession.isPending ? 'Signing out…' : 'Sign Out'}
                    </TactileButton>
                  </div>
                </div>

                {/* Section 2: Google OAuth Connection Management (Phase 5.3.3.1) */}
                <div className="p-3 border border-jv-rule bg-jv-surface/50 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-machine uppercase tracking-wider text-jv-muted">
                      Google OAuth Connection
                    </span>
                    {isGoogleConnected ? (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 text-[10px] font-machine uppercase tracking-wider border border-emerald-500/40 bg-emerald-950/20 text-emerald-400">
                        <span className="w-1 h-1 rounded-full bg-emerald-400" />
                        Connected
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 text-[10px] font-machine uppercase tracking-wider border border-amber-500/40 bg-amber-950/20 text-amber-400">
                        <span className="w-1 h-1 rounded-full bg-amber-400" />
                        Not Connected
                      </span>
                    )}
                  </div>

                  {isGoogleConnected ? (
                    <div className="space-y-3">
                      <div>
                        <span className="block text-[11px] font-machine text-jv-muted uppercase">Linked Account:</span>
                        <span className="text-xs font-mono font-bold text-jv-ink">{authenticatedEmail}</span>
                      </div>

                      <div>
                        <span className="block text-[11px] font-machine text-jv-muted uppercase mb-1">Granted Scopes:</span>
                        <div className="flex flex-wrap gap-1">
                          {scopes.map((scope) => {
                            const isSend = scope.includes('gmail.send');
                            return (
                              <span
                                key={scope}
                                className={`px-1.5 py-0.5 text-[10px] font-mono border ${
                                  isSend
                                    ? 'border-emerald-500/40 bg-emerald-950/20 text-emerald-400 font-bold'
                                    : 'border-jv-rule bg-jv-bg text-jv-muted'
                                }`}
                              >
                                {isSend ? '✓ gmail.send' : scope.split('/').pop()}
                              </span>
                            );
                          })}
                        </div>
                        {!hasSendScope && (
                          <div className="mt-1 text-[11px] font-machine text-rose-400">
                            ⚠ Missing required 'gmail.send' permission. Reconnection required.
                          </div>
                        )}
                      </div>

                      <div className="pt-2 flex items-center justify-end gap-2 border-t border-jv-rule">
                        <TactileButton
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={handleConnectGoogle}
                          disabled={authorizeGoogle.isPending}
                          className="text-[11px]"
                        >
                          {authorizeGoogle.isPending ? 'Redirecting…' : 'Reconnect Google'}
                        </TactileButton>
                        <TactileButton
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={handleDisconnectGoogle}
                          disabled={disconnectGoogle.isPending}
                          className="text-[11px]"
                        >
                          {disconnectGoogle.isPending ? 'Disconnecting…' : 'Disconnect'}
                        </TactileButton>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <p className="text-xs text-jv-muted font-interface leading-relaxed">
                        Connect an external Google account to enable governed Gmail transmission once approved.
                        Directs browser to Google's consent page and returns to <code className="bg-jv-surface px-1 py-0.5 text-jv-ink">https://localhost:3443/api/v1/auth/google/callback</code>.
                      </p>
                      <div className="flex items-center justify-end pt-1">
                        <TactileButton
                          type="button"
                          variant="primary"
                          size="sm"
                          onClick={handleConnectGoogle}
                          disabled={authorizeGoogle.isPending}
                          className="text-[11px]"
                        >
                          {authorizeGoogle.isPending ? 'Connecting to Google…' : 'Connect Google Account'}
                        </TactileButton>
                      </div>
                    </div>
                  )}
                </div>

                {/* Section 3: Governance & Safety Notice */}
                <div className="p-2.5 border border-dashed border-jv-rule text-[11px] font-machine text-jv-muted leading-relaxed">
                  ⓘ <strong>Governance Notice:</strong> Account management actions only establish or clear credentials. They do NOT create execution attempts, approve applications, or transmit emails.
                </div>

                <div className="flex items-center justify-end pt-2 border-t border-jv-rule">
                  <TactileButton
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleClose}
                  >
                    Close
                  </TactileButton>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
