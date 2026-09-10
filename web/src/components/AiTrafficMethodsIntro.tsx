/** Plain-English intro for the two AI traffic measures on the dashboard. */

export const AI_TRAFFIC_METHODS_TITLE = "Two ways to look at AI traffic";

export const AI_TRAFFIC_METHODS_LEAD =
  "This page shows two different pictures of AI’s effect on the site. They answer different questions and should not be added together.";

export function AiTrafficMethodsIntro() {
  return (
    <section
      className="rounded-lg border border-neutral-300 bg-white px-5 py-4"
      aria-labelledby="ai-traffic-methods-heading"
    >
      <h2 id="ai-traffic-methods-heading" className="text-lg font-semibold text-neutral-900">
        {AI_TRAFFIC_METHODS_TITLE}
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-neutral-600">{AI_TRAFFIC_METHODS_LEAD}</p>
      <dl className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-sm font-semibold text-neutral-900">Direct AI traffic</dt>
          <dd className="mt-1 text-sm leading-relaxed text-neutral-600">
            Sessions Analytics already attributes to chatbots such as ChatGPT and Gemini. This is
            a count of visits labelled as coming from those tools.
          </dd>
        </div>
        <div>
          <dt className="text-sm font-semibold text-neutral-900">Estimated AI impact</dt>
          <dd className="mt-1 text-sm leading-relaxed text-neutral-600">
            A model of knock-on effects on ordinary <strong>SEO and Direct</strong> traffic as AI
            search grew in the market. It compares what actually happened with a baseline of what
            those weeks would likely have looked like if AI had stayed small. It is{" "}
            <strong>not</strong> a recount of chatbot referrals.
          </dd>
        </div>
      </dl>
      <p className="mt-4 text-xs leading-relaxed text-neutral-500">
        <span className="font-semibold text-neutral-700">Caveats.</span> This is not an experiment —
        nobody was randomly “exposed to AI.” We are not estimating sales or conversion-rate impact;
        purchases shown below are simply what Analytics already recorded. A new site is scored as
        typical of its group (retail, services, or publisher), not as a unique slope of its own.
        Anything else that rose at the same time as chatbots, and that we did not measure, can leak
        into the estimate.
      </p>
    </section>
  );
}
