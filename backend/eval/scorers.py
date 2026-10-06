"""Scoring a diagnosis against its golden label.

Two scorers run on every scenario, by design (plan §6.1):

- `signal_check` is deterministic keyword matching over the golden
  label's `required_signals`. It is cheap, reproducible, and — the actual
  reason it exists — it says *which* part of the expected answer was
  missing, which a bare pass/fail from a judge never does.
- `judge` is an LLM comparing the agent's diagnosis to the ground truth.
  It owns the verdict, because keyword matching cannot tell a valid
  paraphrase from a miss and would under-report correctness.

When the two disagree that is signal in itself — either the golden
label's synonym list is too narrow or the judge is being generous — so
the disagreement is recorded per scenario rather than smoothed over.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from models.config import ModelTier, get_chat_model
from prompts import load_prompt

from .scenarios import GoldenLabel

JUDGE_PROMPT = "eval_judge_v2"

CORPUS_MANIFEST = Path(__file__).resolve().parents[1] / "rag" / "corpus" / "manifest.json"
_URL = re.compile(r"https?://[^\s)\]>\"'`]+")


def _normalize_url(url: str) -> str:
    """Compare pages, not anchors: `.../probes/#readiness` cites `.../probes/`."""
    return url.split("#", 1)[0].rstrip("/.,;:")


@lru_cache(maxsize=1)
def corpus_urls() -> frozenset[str]:
    """Source URLs of every indexed doc page: the only URLs a recommendation can honestly cite."""
    return frozenset(_normalize_url(d["source_url"]) for d in json.loads(CORPUS_MANIFEST.read_text()))


def _is_citation(url: str) -> bool:
    """A link to a public site, not an in-cluster address in a command.

    Recommendations routinely contain `curl http://payments-api:8080/health`
    style commands; those hosts have no dot (or end in a cluster suffix) and
    aren't citations, so they mustn't count as made-up sources.
    """
    host = url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    return "." in host and not host.endswith((".local", ".svc", ".cluster"))


def cited_urls(text: str) -> list[str]:
    """Distinct cited URLs in a recommendation, normalized, in order of first appearance."""
    return list(dict.fromkeys(_normalize_url(u) for u in _URL.findall(text) if _is_citation(u)))


class JudgeVerdict(BaseModel):
    """Structured output from the LLM judge."""

    # No defaults here, so every field is already required in the schema.

    root_cause_correct: bool = Field(description="Did the agent identify the same underlying cause as the ground truth?")
    remediation_appropriate: bool = Field(description="Would acting on the agent's recommendation actually fix it?")
    identified_cause: str = Field(description="One neutral sentence summarizing what the agent concluded.")
    reasoning: str = Field(description="Brief justification naming what the agent got right or wrong.")


@dataclass
class SignalCheck:
    matched_groups: int
    total_groups: int
    missing_signals: list[list[str]] = field(default_factory=list)
    forbidden_hits: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.matched_groups == self.total_groups

    def to_dict(self) -> dict:
        return {**asdict(self), "passed": self.passed}


@dataclass
class DiagnosisScore:
    signal_check: SignalCheck
    judge: JudgeVerdict | None
    correct: bool
    remediation_appropriate: bool
    scorers_agree: bool
    # Set when the judge was asked but never produced a usable verdict, so
    # the verdict above fell back to the signal check.
    judge_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "correct": self.correct,
            "remediation_appropriate": self.remediation_appropriate,
            "scorers_agree": self.scorers_agree,
            "signal_check": self.signal_check.to_dict(),
            "judge": self.judge.model_dump() if self.judge else None,
            "judge_error": self.judge_error,
        }


def _contains(haystack: str, term: str) -> bool:
    """Substring match on identifier boundaries.

    Plain `in` would let "payments" match inside "payments-api" and score a
    wrong answer as right. The boundary class deliberately includes the
    hyphen, unlike a stock `\\b`: in Kubernetes names a hyphen joins words
    inside a *single* identifier, so "payments" and "payments-api" are
    different resources, and "dns" appearing only in "dns-demo" means the
    agent named the pod rather than said anything about DNS.

    Terms with regex-significant characters are escaped, and boundaries
    apply only at ends that are themselves identifier characters (so
    `/health` or `!key` still match).
    """
    escaped = re.escape(term)
    prefix = r"(?<![a-z0-9_-])" if re.match(r"[a-z0-9_-]", term[0], re.I) else ""
    suffix = r"(?![a-z0-9_-])" if re.search(r"[a-z0-9_-]$", term) else ""
    return re.search(f"{prefix}{escaped}{suffix}", haystack, re.I) is not None


def check_signals(golden: GoldenLabel, text: str) -> SignalCheck:
    haystack = text.lower()
    missing = [list(group) for group in golden.required_signals if not any(_contains(haystack, t) for t in group)]
    forbidden = [t for t in golden.forbidden_terms if _contains(haystack, t)]
    return SignalCheck(
        matched_groups=len(golden.required_signals) - len(missing),
        total_groups=len(golden.required_signals),
        missing_signals=missing,
        forbidden_hits=forbidden,
    )


async def judge_diagnosis(
    golden: GoldenLabel,
    diagnosis_text: str,
    recommendation_text: str,
) -> JudgeVerdict:
    # `json_schema` constrains decoding to the schema (`output_config.format`)
    # rather than forcing a tool call and validating afterwards. Under the
    # default `function_calling` method the model occasionally leaked
    # tool-call markup (`</parameter></invoke>`) into a string field,
    # swallowing the next field and failing validation.
    model = get_chat_model(ModelTier.REASONING).with_structured_output(JudgeVerdict, method="json_schema")
    payload = "\n\n".join(
        [
            # Flagged explicitly rather than left for the judge to infer from
            # the prose: the grading rule inverts for these, and inferring
            # "this one has no fault" from a paragraph is exactly the kind of
            # subtlety a judge silently gets wrong.
            *(["**NO-FAULT SCENARIO** — grade by the inverted rule."] if golden.no_fault else []),
            f"## Ground truth root cause\n{golden.root_cause}",
            f"## Expected remediation ({golden.remediation_category})\n{golden.remediation}",
            f"## The agent's diagnosis\n{diagnosis_text or '(the agent produced no diagnosis)'}",
            f"## The agent's recommendation\n{recommendation_text or '(the agent produced no recommendation)'}",
        ]
    )
    return await model.ainvoke(
        [
            {"role": "system", "content": load_prompt(JUDGE_PROMPT)},
            {"role": "user", "content": payload},
        ]
    )


async def score_diagnosis(
    golden: GoldenLabel,
    diagnosis_text: str,
    recommendation_text: str,
    use_judge: bool = True,
) -> DiagnosisScore:
    combined = f"{diagnosis_text}\n\n{recommendation_text}"
    signal_check = check_signals(golden, combined)

    produced_output = bool(diagnosis_text.strip() or recommendation_text.strip())
    verdict = None
    judge_error = None
    if use_judge and produced_output:
        verdict, judge_error = await _judge_with_retry(golden, diagnosis_text, recommendation_text)

    if verdict is not None:
        correct = verdict.root_cause_correct
        remediation_ok = verdict.remediation_appropriate
    else:
        # No judge (disabled, the agent produced nothing, or the judge
        # failed twice): fall back to the deterministic check so the
        # scenario still yields a number rather than dropping out of the
        # denominator.
        correct = signal_check.passed and produced_output
        remediation_ok = correct

    return DiagnosisScore(
        signal_check=signal_check,
        judge=verdict,
        correct=correct,
        remediation_appropriate=remediation_ok,
        scorers_agree=signal_check.passed == correct,
        judge_error=judge_error,
    )


async def _judge_with_retry(
    golden: GoldenLabel,
    diagnosis_text: str,
    recommendation_text: str,
    attempts: int = 2,
) -> tuple[JudgeVerdict | None, str | None]:
    """One retry on a malformed verdict, then give up rather than raise.

    No defaults on `JudgeVerdict` (unlike `Diagnosis`): a defaulted
    `root_cause_correct` would record a verdict the judge never gave.
    API errors are left to the SDK's own retries and propagate.
    """
    error = None
    for _ in range(attempts):
        try:
            return await judge_diagnosis(golden, diagnosis_text, recommendation_text), None
        except ValidationError as exc:
            error = f"ValidationError: {exc.error_count()} error(s), first: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}"
    return None, error
