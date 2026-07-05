// Plain-language terms of service. The clause that actually matters is the
// medical disclaimer; the rest keeps the free shared tier fair and revocable.

export function Terms() {
  return (
    <article className="max-w-2xl mx-auto flex flex-col gap-5 text-stone-700">
      <header>
        <h1 className="font-serif text-3xl text-forest-900 mb-1">Terms of service</h1>
        <p className="text-sm text-stone-500">Last updated: July 2026</p>
      </header>

      <Section title="Not medical advice">
        Histamine Fighter is an educational tool. Safety verdicts, meal suggestions, and articles
        are general guidance built from a curated ingredient index and an AI assistant — they can
        be incomplete or wrong, and histamine tolerance is highly individual. Always confirm with
        your doctor or dietitian before changing your diet. You use the service at your own
        judgment and risk.
      </Section>

      <Section title="The free shared tier">
        Signing in unlocks a free, operator-funded AI tier with daily limits per account, per
        network, and site-wide. It exists for people, not scripts: don't automate against it,
        resell it, or create multiple accounts to stack limits. We may throttle, suspend, or
        remove accounts that abuse it, and may change the limits or withdraw the tier at any
        time. Bringing your own API key is always available and unlimited on our side.
      </Section>

      <Section title="Your account">
        One account per person, created by proving control of an email address. You can delete it
        at any time from the settings drawer. We may terminate accounts that break these terms.
      </Section>

      <Section title="No warranty">
        The service is provided as-is, free of charge, with no warranty of availability or
        accuracy. To the maximum extent permitted by law, we are not liable for damages arising
        from its use. The software is open source under the MIT license; these terms cover the
        hosted service at this site, not your own self-hosted copy.
      </Section>

      <Section title="Changes">
        We may update these terms; material changes will be noted on this page. Continuing to use
        the service after a change means you accept it. Questions: open an issue on GitHub or
        email privacy@histaminefighter.com.
      </Section>
    </article>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="font-serif text-xl text-forest-900 mb-2">{title}</h2>
      <div className="text-sm leading-relaxed">{children}</div>
    </section>
  );
}
