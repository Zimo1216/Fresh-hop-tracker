"""Regression tests for two real bad records a user found in production
(fix-request #4, 2026-09-23):

  Case 1 (hallucination): a "Human People" release was generated from a
  newschoolbeer.com roundup article that never actually mentions Human
  People — the model attached it to the wrong brewery.

  Case 2 (stale source): a Black Raven Brewing release cited an Instagram
  post that was ~207 weeks (~4 years) old, far outside the accepted window.

  Both existed only because they were inserted before ANY grounding/date
  check existed in the code (see MEMORY.md / project notes) — but these
  tests pin down the *current* hardened checks (`_passes_grounding`,
  `_is_stale_result` in backend/scraper.py) directly against fixtures
  shaped like the real cases, with no network calls, so a future change
  that reintroduces either hole fails a test instead of shipping silently.

Run from the project root (with the venv active):
    python -m unittest backend.tests.test_regression -v
"""
import unittest

from backend import scraper


class HallucinationRegressionTest(unittest.TestCase):
    """Case 1: an article about OTHER breweries must not ground a release
    for a brewery it never mentions — even if the model claims otherwise
    via evidence_snippet."""

    def setUp(self):
        # Representative of the real newschoolbeer.com roundup: real fresh
        # hop content, but about breweries other than Human People, and
        # Human People's name never appears anywhere in it.
        self.roundup_article = {
            "url": "https://newschoolbeer.com/home/2026/8/fresh-hop-season-gets-underway",
            "title": "Fresh Hop Season Gets Underway",
            "content": (
                "Fresh hop season is here. Pfriem Family Brewers will release their fresh hop "
                "pale ale on September 25th. Breakside Brewing plans a fresh hop IPA release on "
                "October 2nd. Fort George Brewery will tap their fresh hop lager on September "
                "28th at their Astoria pub. Several Portland-area breweries are also releasing "
                "collaborative wet hop beers this month."
            ),
        }
        self.results_by_url = {scraper._normalize_url(self.roundup_article["url"]): self.roundup_article}

    def test_ungrounded_release_is_rejected(self):
        passes = scraper._passes_grounding(self.roundup_article["url"], "Human People", self.results_by_url)
        self.assertFalse(passes, "Human People isn't mentioned in the article — grounding must reject it")

    def test_fabricated_evidence_snippet_cannot_bypass_the_check(self):
        """This is the actual loophole that let case 1 through: the old
        check trusted evidence_snippet as one of two acceptable proofs. A
        model that fabricates a quote naming the brewery would still pass.
        `_passes_grounding` takes no evidence_snippet argument at all now —
        this test documents that it structurally can't be fooled this way.
        """
        import inspect

        params = inspect.signature(scraper._passes_grounding).parameters
        self.assertNotIn(
            "evidence_snippet",
            params,
            "grounding check must verify against the real fetched source text only, "
            "never accept the model's self-reported evidence_snippet as proof",
        )

    def test_unmatched_source_url_is_rejected(self):
        """If the model cites a URL that isn't among the results we actually
        fetched (hallucinated or mismatched), that's an automatic fail."""
        passes = scraper._passes_grounding(
            "https://example.com/not-a-real-fetched-result", "Human People", self.results_by_url
        )
        self.assertFalse(passes)

    def test_genuinely_grounded_release_still_passes(self):
        """Positive control: don't overcorrect into rejecting real mentions."""
        article = {
            "url": "https://newschoolbeer.com/home/2026/8/fresh-hop-season-gets-underway-2",
            "title": "Fresh Hop Roundup",
            "content": "Human People Beer in Seattle is releasing Alien Plant Farm, a fresh hop collab IPA.",
        }
        results_by_url = {scraper._normalize_url(article["url"]): article}
        passes = scraper._passes_grounding(article["url"], "Human People", results_by_url)
        self.assertTrue(passes)


class StaleSourceRegressionTest(unittest.TestCase):
    """Case 2: an Instagram post ~207 weeks old must be flagged stale before
    it ever reaches extraction, independent of whatever release_date the
    model later claims for it."""

    def test_207_week_old_post_is_flagged_stale(self):
        old_post = {
            "url": "https://www.instagram.com/p/CjJRjYiP8jM?igshid=YTgzYjQ4ZTY%3D",
            "title": "Black Raven Brewing on Instagram",
            "content": "Collab release with Gigantic Brewing dropping this weekend!",
            "published_date": "Wed, 05 Oct 2022 12:00:00 GMT",  # ~207 weeks before 2026-09-23
        }
        self.assertTrue(scraper._is_stale_result(old_post, "2026-09-01"))

    def test_recent_post_is_not_flagged_stale(self):
        recent_post = {
            "url": "https://www.instagram.com/p/somethingrecent",
            "title": "Fremont Brewing on Instagram",
            "content": "Dropping our fresh hop pils this Saturday!",
            "published_date": "Tue, 15 Sep 2026 17:00:00 GMT",
        }
        self.assertFalse(scraper._is_stale_result(recent_post, "2026-09-01"))

    def test_missing_published_date_falls_back_to_year_heuristic(self):
        no_date_but_old_year = {
            "url": "https://example.com/old-roundup",
            "title": "2022 Fresh Hop Roundup",
            "content": "Looking back at the 2022 fresh hop season...",
        }
        self.assertTrue(scraper._is_stale_result(no_date_but_old_year, "2026-09-01"))

    def test_missing_published_date_with_current_year_is_not_flagged(self):
        no_date_current_year = {
            "url": "https://example.com/2026-roundup",
            "title": "2026 Fresh Hop Roundup",
            "content": "The 2026 fresh hop season is underway.",
        }
        self.assertFalse(scraper._is_stale_result(no_date_current_year, "2026-09-01"))


if __name__ == "__main__":
    unittest.main()
