// Plain-language privacy policy. Written by the project, not a lawyer; kept
// honest and short because the data practices themselves are short.

export function Privacy() {
  return (
    <article className="max-w-2xl mx-auto flex flex-col gap-5 text-stone-700">
      <header>
        <h1 className="font-serif text-3xl text-forest-900 mb-1">Privacy policy</h1>
        <p className="text-sm text-stone-500">Last updated: July 2026</p>
      </header>

      <Section title="The short version">
        We store your email address if you create an account, count how much you use the free AI
        tier, and nothing else. No names, no tracking, no ads, no selling data. Deleting your
        account removes it all, yourself, with one button.
      </Section>

      <Section title="What we collect and why">
        <ul className="list-disc pl-5 flex flex-col gap-1">
          <li>
            <strong>Email address</strong> — your account identity, used to sign you in (magic
            link) or matched from your Google/GitHub sign-in. Never used for marketing.
          </li>
          <li>
            <strong>Usage counters</strong> — how many free-tier AI requests your account and your
            network address made today, kept to enforce fair daily limits. Counters reference your
            account id and reset daily.
          </li>
          <li>
            <strong>IP address</strong> — noted at signup and used for rate limiting and abuse
            prevention (for example, limiting how many accounts one network can create per day).
          </li>
        </ul>
      </Section>

      <Section title="What we deliberately don't do">
        <ul className="list-disc pl-5 flex flex-col gap-1">
          <li>No passwords — sign-in is by email link, code, or OAuth, so there is none to leak.</li>
          <li>No analytics or tracking cookies. The single cookie we set is your session.</li>
          <li>
            Your own API keys (if you bring one) stay in your browser and travel only as request
            headers; they are never stored or logged server-side.
          </li>
          <li>Dish lookups and questions are not linked to your account.</li>
        </ul>
      </Section>

      <Section title="Third parties">
        Sign-in emails are delivered by Resend; Google or GitHub handle their own sign-in and tell
        us only your verified email. Free-tier AI requests are processed by OpenAI without your
        identity attached. Cloudflare fronts the site (and its Turnstile bot check protects the
        sign-in form).
      </Section>

      <Section title="Your rights and deletion">
        You can delete your account at any time from your profile page; that removes your email,
        account, and usage counters immediately. For anything else (GDPR access, correction,
        complaints), open an issue on GitHub or email privacy@histaminefighter.com.
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
