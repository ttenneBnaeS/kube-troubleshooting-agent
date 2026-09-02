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

import re
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel, Field

from models.config import ModelTier, get_chat_model
from prompts import load_prompt

from .scenarios import GoldenLabel

JUDGE_PROMPT = "eval_judge_v2"


class JudgeVerdict(BaseModel):
    """Structured output from the LLM judge."""

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

    def to_dict(self) -> dict:
        return {
            "correct": self.correct,
            "remediation_appropriate": self.remediation_appropriate,
            "scorers_agree": self.scorers_agree,
            "signal_check": self.signal_check.to_dict(),
            "judge": self.judge.model_dump() if self.judge else None,
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
    model = get_chat_model(ModelTier.REASONING).with_structured_output(JudgeVerdict)
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
    if use_judge and produced_output:
        verdict = await judge_diagnosis(golden, diagnosis_text, recommendation_text)

    if verdict is not None:
        correct = verdict.root_cause_correct
        remediation_ok = verdict.remediation_appropriate
    else:
        # No judge (disabled, or the agent produced nothing): fall back to
        # the deterministic check so a run without an API budget still
        # yields a number.
        correct = signal_check.passed and produced_output
        remediation_ok = correct

    return DiagnosisScore(
        signal_check=signal_check,
        judge=verdict,
        correct=correct,
        remediation_appropriate=remediation_ok,
        scorers_agree=signal_check.passed == correct,
    )
