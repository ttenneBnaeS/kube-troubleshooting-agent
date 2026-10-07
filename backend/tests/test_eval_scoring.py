"""Eval scoring: the deterministic half the headline accuracy number rests on."""

import pytest
from pydantic import ValidationError

from eval import scorers
from eval.scenarios import SCENARIOS, GoldenLabel
from eval.scorers import JudgeVerdict, _contains, check_signals, cited_urls, corpus_urls, score_diagnosis

GOLDEN = GoldenLabel(
    root_cause="r",
    remediation="m",
    remediation_category="c",
    required_signals=(("secret", "secretkeyref"), ("db-credentials",), ("not found", "missing")),
    forbidden_terms=("oomkilled",),
)


class TestContains:
    def test_hyphen_is_a_word_character(self):
        # "payments" must not match inside a different resource name.
        assert not _contains("calls payments-api", "payments")
        assert _contains("calls payments-api", "payments-api")
        assert not _contains("the dns-demo pod", "dns")

    def test_case_insensitive_and_punctuated_terms(self):
        assert _contains("Secret NOT FOUND.", "not found")
        assert _contains("GET /health returned 404", "/health")


class TestSignals:
    def test_all_groups_hit(self):
        result = check_signals(GOLDEN, "secretKeyRef points at db-credentials, which is missing")
        assert result.passed and result.missing_signals == []

    def test_reports_which_group_missed(self):
        result = check_signals(GOLDEN, "the secret db-credentials exists")
        assert not result.passed
        assert result.missing_signals == [["not found", "missing"]]

    def test_forbidden_terms_are_advisory(self):
        result = check_signals(GOLDEN, "not OOMKilled: the secret db-credentials is missing")
        assert result.passed
        assert result.forbidden_hits == ["oomkilled"]


class TestCitations:
    def test_in_cluster_addresses_are_not_citations(self):
        text = (
            "See [Secrets](https://kubernetes.io/docs/concepts/configuration/secret/#uses). "
            "Then `curl http://payments-api:8080/health` and http://web.default.svc/x."
        )
        assert cited_urls(text) == ["https://kubernetes.io/docs/concepts/configuration/secret"]

    def test_corpus_urls_are_normalized(self):
        urls = corpus_urls()
        assert urls and all(u.startswith("https://") and not u.endswith("/") for u in urls)


class TestScoreDiagnosis:
    async def test_without_judge_falls_back_to_signals(self):
        score = await score_diagnosis(GOLDEN, "secret db-credentials not found", "create it", use_judge=False)
        assert score.correct and score.judge is None and score.scorers_agree

    async def test_empty_output_is_never_correct(self):
        empty = GoldenLabel(root_cause="r", remediation="m", remediation_category="c", required_signals=())
        score = await score_diagnosis(empty, "", "", use_judge=True)
        assert not score.correct

    async def test_judge_verdict_wins_and_disagreement_is_recorded(self, monkeypatch):
        async def judge(*_):
            return JudgeVerdict(root_cause_correct=True, remediation_appropriate=True, identified_cause="x", reasoning="y")

        monkeypatch.setattr(scorers, "judge_diagnosis", judge)
        score = await score_diagnosis(GOLDEN, "a paraphrase with no keywords", "fix", use_judge=True)
        assert score.correct and not score.signal_check.passed
        assert score.scorers_agree is False

    async def test_judge_retries_once_then_falls_back(self, monkeypatch):
        calls = []

        async def broken_judge(*_):
            calls.append(1)
            JudgeVerdict.model_validate({})  # raises ValidationError

        monkeypatch.setattr(scorers, "judge_diagnosis", broken_judge)
        score = await score_diagnosis(GOLDEN, "secret db-credentials not found", "fix", use_judge=True)
        assert len(calls) == 2
        assert score.judge is None and score.judge_error
        assert score.correct  # signal-check fallback


def test_judge_verdict_has_no_defaults():
    # A defaulted root_cause_correct would record a verdict nobody gave.
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate({"identified_cause": "x", "reasoning": "y"})


class TestScenarioRegistry:
    def test_ids_unique(self):
        ids = [s.id for s in SCENARIOS]
        assert len(ids) == len(set(ids))

    def test_manifests_exist_and_have_no_namespace(self):
        # Manifests carry no namespace so the harness can apply them into a
        # throwaway eval-<id> namespace.
        for s in SCENARIOS:
            for path in s.manifest_paths():
                assert path.exists(), path
                assert "\n  namespace:" not in path.read_text(), f"{path} pins a namespace"

    def test_requests_name_their_namespace(self):
        for s in SCENARIOS:
            assert "{namespace}" in s.user_request, s.id

    def test_follow_up_routes_are_valid(self):
        for s in SCENARIOS:
            for f in s.follow_ups:
                assert f.expected_route in ("followup", "investigate")
