import SignInEventsSection from "../../components/settings/SignInEventsSection";

export default function SignInActivityTab() {
  return (
    <section>
      <h3 className="section-title">Sign-in activity</h3>
      <p className="section-hint">Successful and failed sign-ins to your organization, most recent first.</p>
      <SignInEventsSection />
    </section>
  );
}
