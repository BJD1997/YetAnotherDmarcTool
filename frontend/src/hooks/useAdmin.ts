import { useQuery, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "../api/client";
import { queryKeys } from "./queryKeys";

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

export function useAdminOrganizations() {
  return useQuery({
    queryKey: queryKeys.admin.organizations,
    queryFn: () => api.get<AdminOrganization[]>("/admin/organizations"),
  });
}

type AdminUpdatesOptions = Pick<UseQueryOptions<UpdateStatus>, "enabled" | "staleTime">;

export function useAdminUpdates(options: AdminUpdatesOptions = {}) {
  return useQuery({
    queryKey: queryKeys.admin.updates,
    queryFn: () => api.get<UpdateStatus>("/admin/updates"),
    ...options,
  });
}
