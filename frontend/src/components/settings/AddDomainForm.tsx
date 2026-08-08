import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api, ApiError } from "../../api/client";
import type { Domain, DomainMailProfile } from "../../api/types";
import { MailProfileSelect } from "../domain/shared";

// Shared between Settings.tsx and the onboarding wizard — one implementation
// of the add-domain mutation/UI, not two.
export default function AddDomainForm({ onAdded }: { onAdded?: (domain: Domain) => void }) {
  const queryClient = useQueryClient();
  const { data: domains } = useQuery({
    queryKey: ["domains"],
    queryFn: () => api.get<Domain[]>("/domains"),
  });

  const [name, setName] = useState("");
  const [parentId, setParentId] = useState<string>("");
  const [mailProfile, setMailProfile] = useState<DomainMailProfile>("sends_mail");
  const [formError, setFormError] = useState<string | null>(null);
  const [formNotice, setFormNotice] = useState<string | null>(null);

  const apexDomains = (domains ?? []).filter((d) => !d.parent_domain_id);
  const selectedParent = apexDomains.find((d) => d.id === parentId);
  // When adding a subdomain, the user only types the label (e.g. "mail") —
  // the parent's domain is appended automatically rather than requiring the
  // full FQDN to be retyped, which was silently rejected before (a bare
  // label like "mail" fails the full-FQDN validation, giving a confusing 422).
  const fullName = selectedParent ? `${name}.${selectedParent.name}` : name;

  const createDomain = useMutation({
    mutationFn: () =>
      api.post<Domain & { reattributed_reports: number; reattributed_records: number }>("/domains", {
        name: fullName,
        parent_domain_id: parentId || null,
        mail_profile: mailProfile,
      }),
    onSuccess: (created) => {
      setName("");
      setParentId("");
      setMailProfile("sends_mail");
      setFormError(null);
      const notices: string[] = [];
      if (created.reattributed_reports > 0) {
        notices.push(`Linked ${created.reattributed_reports} previously-unmatched report(s) to ${created.name}.`);
      }
      if (created.reattributed_records > 0) {
        // These were previously counted under a less-specific parent
        // domain (e.g. a subdomain's mail folded into the apex's stats,
        // since nothing more specific existed yet to match against) —
        // now correctly attributed to this domain instead.
        notices.push(`Re-attributed ${created.reattributed_records} record(s) from a parent domain to ${created.name}.`);
      }
      setFormNotice(notices.length > 0 ? notices.join(" ") : null);
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding-status"] });
      onAdded?.(created);
    },
    onError: (err) => {
      setFormNotice(null);
      setFormError(err instanceof ApiError ? err.message : "failed to add domain");
    },
  });

  return (
    <div className="card">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          createDomain.mutate();
        }}
        className="field-row"
      >
        <select className="input" value={parentId} onChange={(e) => setParentId(e.target.value)}>
          {/* Not just for apexes: this is also how to add a subdomain you can't
              verify via its own apex (e.g. one you don't control DNS for) —
              type the full name below and it gets its own independent TXT
              challenge, no apex verification required. */}
          <option value="">no parent (verify independently)</option>
          {apexDomains.map((d) => (
            <option key={d.id} value={d.id}>
              subdomain of {d.name}
            </option>
          ))}
        </select>
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={selectedParent ? "mail" : "example.com or sub.example.com"}
          required
        />
        {selectedParent && <span className="muted">.{selectedParent.name}</span>}
        <MailProfileSelect value={mailProfile} onChange={setMailProfile} />
        <button type="submit" className="btn btn--primary" disabled={createDomain.isPending}>
          <Plus />
          Add
        </button>
      </form>
      {formError && (
        <div className="alert alert--critical" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
          {formError}
        </div>
      )}
      {formNotice && (
        <div className="alert alert--good" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
          {formNotice}
        </div>
      )}
    </div>
  );
}
