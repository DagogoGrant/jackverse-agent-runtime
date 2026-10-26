import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OperatorSessionControl } from '../../src/components/ui/OperatorSessionControl';
import * as useCaseworkerHooks from '../../src/hooks/useCaseworker';

describe('OperatorSessionControl Component (Phase 5.3.3.1)', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const renderComponent = () => {
    return render(
      <QueryClientProvider client={queryClient}>
        <OperatorSessionControl />
      </QueryClientProvider>
    );
  };

  it('renders unauthenticated trigger button when no operator session exists', () => {
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: undefined,
      isError: true,
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    renderComponent();

    expect(screen.getByRole('button', { name: /operator session/i })).toBeInTheDocument();
    expect(screen.queryByText(/operator active/i)).not.toBeInTheDocument();
  });

  it('opens bootstrap modal with password input when trigger button is clicked', () => {
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: undefined,
      isError: true,
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    renderComponent();

    const trigger = screen.getByRole('button', { name: /operator session/i });
    fireEvent.click(trigger);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/operator session bootstrap/i)).toBeInTheDocument();
    const input = screen.getByLabelText(/signed operator token/i);
    expect(input).toHaveAttribute('type', 'password');
    expect(screen.getByText(/credentials are kept out of urls and localstorage/i)).toBeInTheDocument();
  });

  it('submits operator token to mutation and handles success', async () => {
    const mutateAsyncMock = vi.fn().mockResolvedValue({ success: true, operator_id: 'operator-1' });
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: undefined,
      isError: true,
      isLoading: false,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(useCaseworkerHooks, 'useCreateOperatorSession').mockReturnValue({
      mutateAsync: mutateAsyncMock,
      isPending: false,
    } as any);

    renderComponent();

    fireEvent.click(screen.getByRole('button', { name: /operator session/i }));

    const input = screen.getByLabelText(/signed operator token/i);
    fireEvent.change(input, { target: { value: 'op.operator-1.12345.abcdef' } });

    const submitBtn = screen.getByRole('button', { name: /authenticate session/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalledWith({
        operator_token: 'op.operator-1.12345.abcdef',
      });
      expect(screen.getByText(/session established for operator 'operator-1'/i)).toBeInTheDocument();
    });
  });

  it('renders authenticated trigger and opens management panel when clicked (Google not connected)', async () => {
    const authorizeGoogleMock = vi.fn().mockResolvedValue({
      authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?test=1',
    });
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: { connected: false, authenticated_email: null, scopes: [], expires_at: null },
      isError: false,
      isLoading: false,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(useCaseworkerHooks, 'useAuthorizeGoogleAuth').mockReturnValue({
      mutateAsync: authorizeGoogleMock,
      isPending: false,
    } as any);

    renderComponent();

    // Trigger button shows operator active and prompt to connect Google
    const trigger = screen.getByRole('button', { name: /operator · connect google/i });
    expect(trigger).toBeInTheDocument();

    // Clicking trigger opens management modal
    fireEvent.click(trigger);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/operator & google account management/i)).toBeInTheDocument();
    expect(screen.getByText(/active \(secure cookie\)/i)).toBeInTheDocument();

    // Connect Google Account button is independently accessible without any case or approved action
    const connectBtn = screen.getByRole('button', { name: /connect google account/i });
    expect(connectBtn).toBeInTheDocument();

    // Clicking initiates authorization
    fireEvent.click(connectBtn);
    await waitFor(() => {
      expect(authorizeGoogleMock).toHaveBeenCalled();
    });
  });

  it('renders connected account details, scopes, and disconnect control when Google is connected', async () => {
    const disconnectMock = vi.fn().mockResolvedValue({ success: true, disconnected: true });
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: {
        connected: true,
        authenticated_email: 'operator@example.com',
        scopes: ['openid', 'https://www.googleapis.com/auth/gmail.send'],
        expires_at: '2026-10-27T10:00:00Z',
      },
      isError: false,
      isLoading: false,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(useCaseworkerHooks, 'useDisconnectGoogleAuth').mockReturnValue({
      mutateAsync: disconnectMock,
      isPending: false,
    } as any);

    renderComponent();

    // Header displays connected email
    const trigger = screen.getByRole('button', { name: /operator · operator@example\.com/i });
    expect(trigger).toBeInTheDocument();

    // Open management modal
    fireEvent.click(trigger);
    expect(screen.getByText('operator@example.com')).toBeInTheDocument();
    expect(screen.getByText(/✓ gmail\.send/i)).toBeInTheDocument();

    // Disconnect button
    const disconnectBtn = screen.getByRole('button', { name: /disconnect/i });
    expect(disconnectBtn).toBeInTheDocument();

    fireEvent.click(disconnectBtn);
    await waitFor(() => {
      expect(disconnectMock).toHaveBeenCalled();
    });
  });
});
