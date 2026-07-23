from __future__ import annotations

from api.geo_services import load_create_report, load_crawl_site


def test_crawl_captures_criterion_specific_eeat_snippets() -> None:
    crawl = load_crawl_site()
    html = """
    <html><body>
      <article>
        <p>We tested this process with 120 customers and found that completion improved by 28 percent.</p>
        <p>Written by Dr Jane Smith, a certified specialist with 15 years of professional experience.</p>
        <p>Our organisation is accredited by the Example Standards Board and was recognised for this work.</p>
        <p>Our editorial policy explains every source, and each article is reviewed by a named specialist.</p>
      </article>
    </body></html>
    """

    signals = crawl.compute_page_content_signals(html)

    assert signals["eeat_evidence"]["experience"]
    assert signals["eeat_evidence"]["expertise"]
    assert signals["eeat_evidence"]["authoritativeness"]
    assert signals["eeat_evidence"]["trust"]


def test_routine_first_hand_process_is_not_original_information() -> None:
    crawl = load_crawl_site()
    signals = crawl.compute_page_content_signals(
        """
        <html><body><article>
          <p>Our team used this standard process in practice and learned how to complete each step efficiently.</p>
        </article></body></html>
        """
    )

    assert signals["eeat_evidence"]["experience"]
    assert signals["originality_evidence"] == []
    assert signals["originality_signal_types"] == []


def test_originality_requires_dedicated_novel_contribution_signals() -> None:
    crawl = load_crawl_site()
    signals = crawl.compute_page_content_signals(
        """
        <html><body><article>
          <p>We analysed our proprietary dataset of 2,400 customer responses and identified a 31 percent difference between the two groups.</p>
          <p>We developed our custom decision model to benchmark 85 products against the resulting criteria.</p>
        </article></body></html>
        """
    )

    assert signals["originality_evidence"]
    assert "first_party_data" in signals["originality_signal_types"]
    assert "proprietary_framework" in signals["originality_signal_types"]


def test_eeat_scores_use_direct_content_and_return_url_evidence() -> None:
    crawl = load_crawl_site()
    report = load_create_report()
    html = """
    <html><body><article>
      <p>We tested this method in practice and measured a 35 percent improvement across three projects.</p>
      <p>Our research methodology and source data are reviewed by a certified industry specialist.</p>
      <p>We are accredited by the Example Council and have been recognised by independent industry judges.</p>
      <p>Our editorial policy names every source and the page is reviewed by a qualified expert.</p>
    </article></body></html>
    """
    audit = {
        "pages": [{
            "url": "https://example.com/research",
            "final_url": "https://example.com/research",
            "page_title": "Our research",
            "http_status": 200,
            "content_signals": crawl.compute_page_content_signals(html),
        }]
    }

    breakdown = report._eeat_breakdown(audit=audit, agents=[], ai_crawler_score=0)

    assert all(item["score"] > 0 for item in breakdown)
    assert all(item["evidence"][0]["url"] == "https://example.com/research" for item in breakdown)
    assert all("direct 0–100 assessment" in item["how_scored"] for item in breakdown)
    assert all("overall content quality" not in item["how_scored"] for item in breakdown)


def test_low_eeat_score_explains_missing_content() -> None:
    report = load_create_report()
    audit = {
        "pages": [{
            "url": "https://example.com/",
            "page_title": "Home",
            "http_status": 200,
            "content_signals": {"has_editorial_content": False, "eeat_evidence": {}, "eeat_signals": {}},
        }]
    }

    breakdown = report._eeat_breakdown(audit=audit, agents=[], ai_crawler_score=0)

    assert all(item["score"] == 0 for item in breakdown)
    assert all("No matching" in item["evidence_note"] for item in breakdown)


def test_crawl_captures_answerability_formatting_evidence() -> None:
    crawl = load_crawl_site()
    html = """
    <html><body><article>
      <h2>What is passage-level answerability?</h2>
      <p>Passage-level answerability is the ability of a self-contained paragraph to answer a specific question.</p>
      <h2>How should teams improve it?</h2>
      <p>Teams should place a direct answer beneath a descriptive heading because this preserves context when quoted.</p>
    </article></body></html>
    """

    signals = crawl.compute_page_content_signals(html)

    assert signals["heading_h2_h3_n"] == 2
    assert signals["faq_question_n"] >= 2
    assert signals["direct_answer_n"] >= 1
    assert any(value.startswith("Heading:") for value in signals["formatting_evidence"])
