import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NavRail } from '../../src/components/layout/NavRail';

describe('Contextual Editorial Book Spine Navigation', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  it('renders numbered routes 01 to 06 with keyboard-focusable links', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <NavRail />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByRole('link', { name: /01 home/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /02 missions/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /03 opportunities/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /04 needs you/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /05 my context/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /06 activity/i })).toBeInTheDocument();
  });

  it('renders contextual folio breadcrumb when inside a mission detail route', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/missions/mission-123']}>
          <NavRail />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const backBtn = screen.getByRole('button', { name: /missions index/i });
    expect(backBtn).toBeInTheDocument();
  });

  it('renders full drawer navigation when isMobileDrawer is true', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <NavRail isMobileDrawer />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Editorial System/i)).toBeInTheDocument();
    expect(screen.getByText('Home')).toBeInTheDocument();
  });
});
