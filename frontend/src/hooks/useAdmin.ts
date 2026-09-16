import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "../api/client";
import { queryKeys } from "./queryKeys";
import { useCursorPage } from "./useCursorPage";

export interface AdminOrganization {
  id: string;
  name: string;
  entra_tenant_id: string | null;
  status: "active" | "suspended";
  is_operator: boolean;
  created_at: string;
  mailbox_connection: {
    id: string;
    mailbox_address: string;
    consent_status: "pending" | "granted" | "revoked";
    consent_granted_at: string | null;
    last_sync_at: string | null;
    last_sync_status: "success" | "error" | null;
    last_sync_error: string | null;
  } | null;
  entra_consent_urls: {
    mail_access_consent_url: string;
    sso_consent_url: string;
  } | null;
  domain_count: number;
  job_error_count_7d: number;
  last_report_at: string | null;
}

export interface AdminOrganizationsPage {
  organizations: AdminOrganization[];
  has_more: boolean;
  summary: { total: number; active: number; suspended: number; orgs_with_job_errors_7d: number };
}

export interface UpdateStatus {
  running_version: string;
  latest_version: string | null;
  latest_release_url: string | null;
  latest_release_notes: string | null;
  latest_published_at: string | null;
  checked_at: string | null;
  check_error: string | null;
  include_prereleases: boolean;
  update_available: boolean;
  is_dev_build: boolean;
}

export interface AdminJobRun {
  id: string;
  job_type: string;
  organization_id: string | null;
  domain_id: string | null;
  status: "success" | "failure";
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  stats: Record<string, unknown> | null;
}

export interface AdminJobRunsSummary {
  last_failure: { job_type: string; organization_id: string | null; started_at: string; error_message: string | null } | null;
  success_rate_pct_24h: number | null;
  latest_mailbox_poll_at: string | null;
  reports_processed_today: number;
}

export function useAdminOrganizations(search: string) {
  return useCursorPage<AdminOrganizationsPage>(
    queryKeys.admin.organizations(search),
    (cursor) => {
      const qs = new URLSearchParams();
      if (search) qs.set("search", search);
      if (cursor) qs.set("before_id", cursor);
      return api.get<AdminOrganizationsPage>(`/admin/organizations${qs.toString() ? `?${qs.toString()}` : ""}`);
    },
    (lastPage) => {
      if (!lastPage.has_more) return undefined;
      const orgs = lastPage.organizations;
      return orgs[orgs.length - 1]?.id;
    },
  );
}

type AdminUpdatesOptions = Pick<UseQueryOptions<UpdateStatus>, "enabled" | "staleTime">;

export function useAdminUpdates(options: AdminUpdatesOptions = {}) {
  return useQuery({
    queryKey: queryKeys.admin.updates,
    queryFn: () => api.get<UpdateStatus>("/admin/updates"),
    ...options,
  });
}

export function useAdminJobRunsSummary() {
  return useQuery({ queryKey: queryKeys.admin.jobRunsSummary, queryFn: () => api.get<AdminJobRunsSummary>("/admin/job-runs/summary") });
}

export interface AdminJobRunsPage {
  job_runs: AdminJobRun[];
  has_more: boolean;
}

export function useAdminJobRuns(filters: string) {
  return useCursorPage<AdminJobRunsPage>(
    queryKeys.admin.jobRuns(filters),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<AdminJobRunsPage>(`/admin/job-runs?${qs.toString()}`);
    },
    (lastPage) => (lastPage.has_more ? lastPage.job_runs[lastPage.job_runs.length - 1]?.id : undefined),
  );
}

function useInvalidateAdminOrganizations() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: queryKeys.admin.organizationsAll });
}

export function useCreateAdminOrganization(onSuccess?: () => void, onError?: (error: Error) => void) {
  const invalidate = useInvalidateAdminOrganizations();
  return useMutation({
    mutationFn: (body: { name: string; entra_tenant_id: string | null }) => api.post<AdminOrganization>("/admin/organizations", body),
    onSuccess: () => { invalidate(); onSuccess?.(); },
    onError,
  });
}

export function useUpdateAdminOrganization(orgId: string) {
  const invalidate = useInvalidateAdminOrganizations();
  return useMutation({
    mutationFn: (body: Partial<Pick<AdminOrganization, "entra_tenant_id" | "status">>) => api.patch<AdminOrganization>(`/admin/organizations/${orgId}`, body),
    onSuccess: invalidate,
  });
}

export function useSetAdminMailboxConnection(orgId: string) {
  const invalidate = useInvalidateAdminOrganizations();
  return useMutation({
    mutationFn: (body: { mailbox_address: string; consent_status?: string }) => api.post(`/admin/organizations/${orgId}/mailbox-connection`, body),
    onSuccess: invalidate,
  });
}

export function useCreateAdminUser(orgId: string, onSuccess?: (result: { setup_link: string }) => void, onError?: (error: Error) => void) {
  return useMutation({
    mutationFn: (email: string) => api.post<{ setup_link: string }>(`/admin/organizations/${orgId}/users`, { email }),
    onSuccess,
    onError,
  });
}

export function useDeleteAdminOrganization(orgId: string) {
  const invalidate = useInvalidateAdminOrganizations();
  return useMutation({ mutationFn: () => api.delete(`/admin/organizations/${orgId}`), onSuccess: invalidate });
}

export function useChangeAdminPassword(onSuccess?: () => void, onError?: (error: Error) => void) {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) => api.post("/admin/change-password", body),
    onSuccess,
    onError,
  });
}

export function useAdminUpdateActions() {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.admin.updates });
  const setPrereleases = useMutation({
    mutationFn: (include: boolean) => api.patch<UpdateStatus>("/admin/updates", { include_prereleases: include }),
    onSuccess: async () => { await api.post("/admin/updates/check-now"); await invalidate(); },
  });
  return {
    setPrereleases,
    checkNow: async () => { await api.post("/admin/updates/check-now"); await invalidate(); },
    triggerUpdate: () => api.post("/admin/updates/trigger"),
  };
}
