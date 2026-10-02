import SignInEventsSection from "../../components/settings/SignInEventsSection";

export default function AdminSignIns() {
  return (
    <section>
      <div className="page-header">
        <div>
          <h1>Break-glass sign-ins</h1>
          <p className="page-subtitle">
            Every sign-in attempt and password change on the break-glass admin account. Sign-ins through an
            organization appear in that organization's own Sign-in activity instead.
          </p>
        </div>
      </div>
      <SignInEventsSection endpoint="/admin/sign-in-events" />
    </section>
  );
}
