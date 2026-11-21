"""Whether a message about a pull request asks for more than a summary of its code changes: what was SAID (comments, feedback, a thread), what was DECIDED (approved, changes requested, who still has to review),
whether its checks pass, or one file's patch (2026-11-20).

WHY. A message about a pull request was routed to the one-shot review node (agent_workflow.pr_summarizer_node), which writes an overview from the description, commits and patches in a single model call. It never fetches
comments, reviews or checks, and cannot take a follow-up, so "what did Dana say about the retry logic on PR 4?" got a summary of the diff instead of an answer. Such a question goes to the tool agent, which has
read_pull_request and read_pr_file_diff for exactly this. Plain "review PR #4" or "summarize the latest pull request" stays on the one-shot node: this only fires on the detail words below.

Decided in code with no model call, like the other route gates. A miss keeps the old one-shot answer; a false hit costs one tool-agent turn. Pure, never raises."""
import re

_PR = re.compile(r"\b(?:prs?|pull[- ]?requests?)\b|#\d+", re.IGNORECASE)
_DETAIL = re.compile(
    r"\b(?:comments?|commented|feedback|discussions?|threads?|conversations?|repl(?:y|ies|ied)|reviewers?|approv(?:ed|al|als|e)|changes? requested|requested changes|request(?:ed)? changes|"
    r"who (?:reviewed|approved|commented)|(?:reviewed|approved) by|waiting (?:on|for) (?:a )?review|"
    r"(?:status |ci |check )?checks?|check runs?|ci|passing|failing|green|red|mergeable|"
    r"what did \w+ (?:say|think|write|comment|ask|suggest)|which files|files? changed|"
    r"(?:diff|patch|changes?) (?:of|to|in|for|on) [\w./-]+\.\w+|line \d+ of|inline)\b",
    re.IGNORECASE,
)


def asks_pr_details(text: str) -> bool:
    """True when the message is about a pull request AND asks about its comments, reviews, approval, checks or one file's patch. Pure."""
    try:
        sample = str(text or "")
        if not sample.strip() or not _PR.search(sample):
            return False
        return bool(_DETAIL.search(sample))
    except Exception:
        return False
