# Gift Card Compare 🎁

A mobile-first Traditional Chinese **US gift card discount tracker** designed to be saved on an iPhone Home Screen.

## Features

- **Nine categories, each with 10 manually curated recommendations (90 cards total)**: restaurants, grocery/warehouse, general shopping, fashion/beauty, tech/gaming, travel/transport, entertainment/streaming, home improvement, and everyday services/auto. On the home screen, tap a category to view its ten recommended gift cards.
- Two clearly separated views: **Top 10 Recommendations** (editorial utility-based choices) and **Recent Discount Top 10** (only sourced article headlines with detectable savings). No invented discount percentages to fill a chart.
- Recommendation names and reasons live in `public/data/recommendations.json`. Rankings are subjective, not independently measured popularity or current discounts.

- Top 10 recent publicly reported gift card deals, ranked by stated or calculated estimated discount percentage.
- Search brands, browse categories, sort savings, save favorites in your browser.
- Links to original offer posts for verification.
- Free static hosting via GitHub Pages; home-screen web app (PWA), not an App Store native app.

## Enable your website

1. In the GitHub repository, open **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Open **Actions → Gift Card Compare Daily Refresh → Run workflow** (if not already running).
4. After a successful deployment, visit **https://ivyfarah.github.io/gift-card-compare/**.
5. In iPhone Safari, select **Share → Add to Home Screen**.

The workflow runs on changes to main and on a daily schedule at **16:00 UTC** (8:00 AM California winter / 9:00 AM summer), but GitHub scheduled tasks can be delayed or temporarily skipped.

## Source and price limitations

The `scripts/update_deals.py` job downloads public RSS headlines from Doctor of Credit's gift card deals feed and Slickdeals frontpage and popular-deals feeds. It attempts to parse **explicit** percentage-off discounts, ranges, or face-value / price pairs. It excludes obviously expired articles, rewards, cashback, and ambiguous offers, and only considers recently published posts. Unavailable feeds or no qualifying deals result in a clear status, **not fictional offers or a forced list of 10**.

**Not a live price feed.** RSS headlines are not authoritative checkout data and can be outdated. Memberships, store locations, fees, codes, expiration, availability, taxes, and terms require direct verification. A displayed discount is a **reported estimate**, not a guarantee. If an article refers to multiple brands, its discount may not apply to all of them. Buyers should check the actual redemption and checkout terms.

## Privacy and cost

Uses Python 3 standard library, HTML/CSS/JavaScript and GitHub Actions/Pages. No banking integrations, purchase capability, password storage, or paid API dependencies. Favorites are stored only in your local browser.

## Run the updater

```bash
python3 scripts/update_deals.py
```

The GitHub Actions workflow fetches data and **deploys directly** from the `public/` folder; it does not commit daily JSON outputs back to the repo. See `.github/workflows/daily.yml`.

## Troubleshooting first publication

If the initial workflow failed at **Configure GitHub Pages** because Pages was disabled, first choose **Settings → Pages → Build and deployment → Source → GitHub Actions**, then rerun the workflow. Any new commit on `main` also starts the publishing workflow automatically. A green **deploy** job indicates the first site publication succeeded.
