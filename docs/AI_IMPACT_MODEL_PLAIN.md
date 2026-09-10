# How the AI-impact model works (plain English)

This is the non-technical companion to `docs/AI_IMPACT_MODEL.md`.

## What we are trying to find out

When AI chatbots became a bigger part of how people find things online, did a website’s usual search traffic and “typed in the URL / came back directly” traffic go up, go down, or stay about the same?

We are **not** simply counting visits that Google Analytics already labels as coming from ChatGPT and similar tools. Those visits are measured separately. This model asks a different question: as AI search grew across the market, what happened to the site’s **normal SEO and Direct traffic**?

## The basic idea

Imagine two versions of the same weeks on a website:

1. **What actually happened**, with AI chatbots already in wide use.
2. **What would likely have happened** if AI chatbots had stayed at the low level we saw before they took off.

The difference between those two stories is the “AI impact” on SEO and Direct sessions.

We cannot observe the second story. We estimate it from patterns across many websites over several years.

## What the model looks at

The current model (`hierarchical-ai-v2-2026-09-05`) was trained on **61 web properties**, week by week, from June 2023 through July 2026. Twenty app properties were removed because app analytics cannot reliably identify AI referrals. Its corrected market-wide AI signal continues through the complete week starting 30 August 2026.

Each week, for each site, we know:

- How many people arrived via **search** (SEO)
- How many arrived **directly**
- How much people were **searching for that brand** on Google (Google Trends)
- How busy that time of year usually is

Separately, we build one **market-wide “AI is growing” measure**. It is the share of all visits, across all 61 sites, that came through AI chatbots that week, divided by GA4's exact all-session total. Every site in the model sees the same “how big is AI this week?” number. That is deliberate: we are studying the rise of AI in the market, not a site counting its own chatbot referrals.

Christmas and New Year weeks are left out. Shopping spikes in those weeks drown out the slower AI signal.

We do **not** use Search Console clicks or impressions inside the model.

## How it decides “this was AI, not something else”

A website’s traffic moves around for lots of reasons: it is a big brand or a small one, it is growing or shrinking, summer vs winter, a TV ad, a product launch.

The model tries to strip those out first:

- Each site has its own typical size
- Each site can have its own slow upward or downward drift
- Each type of business has its own seasonal pattern
- Each site’s own brand-search popularity is allowed to explain traffic

**Whatever movement is still left**, and that lines up with the market-wide rise of AI, is treated as the AI effect.

That is an informed estimate, not proof. If something else rose at the same time as chatbots and we did not measure it, it can leak into the answer.

## Why sites are grouped

We do not give every website its own private “AI effect.” The AI measure is the same for everyone in a given week, so there is not enough information to estimate 61 different AI effects reliably.

Instead we group sites into three kinds of business:

- **Retail advertisers** (shops)
- **Services advertisers** (bookings, quotes, and similar)
- **Publishers** (content sites)

Sites of the same type share an AI effect. Brand-search effects are allowed to differ more from site to site, because each site has its own Trends series.

## A new site we have never seen

When a client site is scored for the first time, we do not retrain the whole model.

1. We classify the site into one of the three groups.
2. We take that group’s already-learned AI effect.
3. We apply it to **this site’s actual** SEO and Direct visits in the recent weeks.

So a new shop is treated like other shops, not like a publisher.

Once we have at least **eight good weeks** of analytics plus brand-search data, we can run a heavier update that folds the new site into the portfolio picture. Even then, the site still borrows its AI effect from its group; it does not get a one-off private AI slope.

The portfolio baseline is retrained manually each month. GA4 and real Google Trends exports must both be refreshed first; missing Trends weeks are never invented or extrapolated.

## What the numbers mean

For the recent window (about the last 13 complete weeks) we report:

- A **central estimate**: our best guess of how many extra (or fewer) SEO and Direct sessions are tied to AI growth
- A **range**: we are reasonably sure the true figure sits in this band
- A **sensitivity check**: we rerun the same idea with the most extreme AI weeks tamed, to see if a few spike weeks are doing all the work

If both versions point the same way and the range does not include zero, we call the result more trustworthy.

A **positive** number means: as AI grew, this kind of site’s SEO or Direct traffic tended to be **higher** than it would have been otherwise.

A **negative** number means: AI growth tended to **replace** some of that traffic.

In the current promoted model:

- Retail sites: SEO and Direct both tend to be **up**
- Services sites: SEO and Direct both tend to be **down**
- Publisher sites: Direct tends to be **down**; SEO is close to flat

Those are group patterns, not a guarantee for every individual website. The model passed its promotion checks (stable fits and same direction when a site is held out), so these group patterns are what production scoring uses today.

The market AI signal is now measured with exact all-session totals. On a four-week blend it is about **3,300 AI chatbot sessions per million** all-channel sessions (week starting 30 Aug 2026) — see `docs/AI_ADOPTION_INDEX.md` and the chart there.

## What we do not claim

- We are not saying we ran an experiment. Nobody was randomly “exposed to AI.”
- We are not estimating sales or conversion-rate impact. Purchases shown on the dashboard are simply what analytics already recorded.
- We are not saying “this many visits came from ChatGPT.” Analytics already has that channel. This model is about knock-on effects on SEO and Direct.
- A brand-new site is scored as typical of its group, not as a unique snowflake, until more of its own history is available.
