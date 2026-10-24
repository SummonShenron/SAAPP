"""Job listings as a place for a task to look (Jooble and Adzuna), and how the runner treats them."""
import asyncio
import pathlib
import re
from types import SimpleNamespace

import pytest

from backend.services import job_sources as js
from backend.services import user_task_runner as runner
from backend.utils import user_tasks as ut
from backend.tests.test_user_tasks import SPEC, TASK, Db, Resp, page, results

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def job_keys(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "jk-SECRET")
    monkeypatch.setenv("ADZUNA_APP_ID", "aid")
    monkeypatch.setenv("ADZUNA_APP_KEY", "akey-SECRET")


def test_a_job_source_is_offered_only_while_its_key_is_set(monkeypatch):
    for name in ("JOOBLE_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "ADZUNA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert ut.available_sources() == ["web"]
    monkeypatch.setenv("JOOBLE_API_KEY", "x")
    assert ut.available_sources() == ["web", "jooble"]
    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    assert "adzuna" not in ut.available_sources()                         # needs both halves
    monkeypatch.setenv("ADZUNA_API_KEY", "k")                              # the other name for the key is read too
    assert ut.available_sources() == ["web", "jooble", "adzuna"]


def test_a_task_can_pick_a_job_source_only_when_it_is_set_up(monkeypatch, job_keys):
    spec, issue = ut.validate_spec({**SPEC, "sources": ["web", "jooble", "adzuna"], "location": "Austin, TX"})
    assert issue is None and spec["sources"] == ["web", "jooble", "adzuna"] and spec["location"] == "Austin, TX"
    monkeypatch.delenv("JOOBLE_API_KEY")
    assert ut.validate_spec({**SPEC, "sources": ["jooble"]})[0] is None    # not offered, so not accepted
    assert ut.validate_spec({**SPEC, "sources": ["web", "jooble"]})[0]["sources"] == ["web"]


def test_the_place_is_kept_short_and_checked_like_the_rest():
    assert ut.validate_spec({**SPEC, "location": "x" * 200})[1].startswith("Keep the place short")
    assert "health" in ut.validate_spec({**SPEC, "location": "the cancer ward"})[1]
    assert ut.validate_spec({**SPEC, "location": "see https://x.example"})[1].startswith("Describe what to look for")


class Reply:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


JOOBLE_REPLY = {"jobs": [
    {"id": 111, "title": "Senior <b>Python</b> Engineer", "company": "Acme", "location": "Remote", "snippet": "Build <i>data</i> pipelines.  Python and SQL.",
     "salary": "$150k", "type": "Full-time", "link": "http://jooble.org/desc/111?x=1"},
    {"id": 222, "title": "Elsewhere", "company": "Evil", "location": "", "snippet": "s", "link": "https://evil.example/apply"},
]}
ADZUNA_REPLY = {"results": [
    {"id": "9", "title": "Backend Developer", "company": {"display_name": "Globex"}, "location": {"display_name": "Austin, Texas"}, "description": "Python <b>services</b>.",
     "salary_min": 120000, "salary_max": 150000, "redirect_url": "https://www.adzuna.com/details/9?se=abc"},
]}


def test_jooble_listings_become_candidates_with_only_their_own_links(monkeypatch, job_keys):
    seen = {}
    monkeypatch.setattr(js.requests, "post", lambda url, json=None, timeout=None: seen.update(url=url, body=json) or Reply(JOOBLE_REPLY))
    got = js.search("jooble", "python developer", "Remote")
    assert seen["url"] == "https://jooble.org/api/jk-SECRET" and seen["body"]["keywords"] == "python developer" and seen["body"]["location"] == "Remote"
    assert len(got) == 1                                                   # the listing that points off Jooble is not shown
    job = got[0]
    assert job["url"] == "https://jooble.org/desc/111?x=1" and job["title"] == "Senior Python Engineer at Acme" and "<" not in job["text"]
    assert "Full-time" in job["text"] and "$150k" in job["text"] and "data pipelines" in job["text"] and job["domain"] == "jooble.org"
    assert job["key"] == js._candidate("jooble", 111, "x", "t", "", "", "")["key"]


def test_adzuna_listings_become_candidates(monkeypatch, job_keys):
    seen = {}
    monkeypatch.setattr(js.requests, "get", lambda url, params=None, timeout=None: seen.update(url=url, params=params) or Reply(ADZUNA_REPLY))
    got = js.search("adzuna", "python developer", "Austin")
    assert seen["url"] == "https://api.adzuna.com/v1/api/jobs/us/search/1" and seen["params"]["what"] == "python developer" and seen["params"]["where"] == "Austin"
    assert got[0]["title"] == "Backend Developer at Globex" and "Austin, Texas" in got[0]["text"] and "Pay 120,000 to 150,000" in got[0]["text"]
    monkeypatch.setenv("ADZUNA_COUNTRY", "GB")
    js.search("adzuna", "x", "")
    assert seen["url"].endswith("/jobs/gb/search/1") and "where" not in seen["params"]


def test_a_source_that_is_not_set_up_returns_nothing_without_a_request(monkeypatch):
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
    monkeypatch.setattr(js.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request expected")))
    assert js.search("jooble", "python", "") == []


def test_a_failed_request_never_leaks_the_key(monkeypatch, job_keys):
    def boom(url, **kwargs):
        raise OSError(f"connection to {url} failed")

    monkeypatch.setattr(js.requests, "post", boom)
    with pytest.raises(js.JobSourceError) as caught:
        js.search("jooble", "python", "")
    assert "SECRET" not in str(caught.value) and caught.value.__suppress_context__
    monkeypatch.setattr(js.requests, "post", lambda *a, **k: Reply({}, 403))
    with pytest.raises(js.JobSourceError) as caught:
        js.search("jooble", "python", "")
    assert "SECRET" not in str(caught.value)


def listing(source, job_id, title="Job"):
    return {"url": f"https://{source}.org/{job_id}", "key": js._candidate(source, job_id, "", "t", "", "", "")["key"], "domain": f"{source}.org", "title": title, "text": "Python role"}


def run_with_jobs(task, job_search, answer='{"headline": "h", "items": [{"n": 1, "note": "n"}]}', web=None):
    async def invoke(prompt):
        return Resp(answer)

    return asyncio.run(runner.run_task(Db(), task, invoke, search=lambda q: web or [], fetch=lambda url: page(), job_search=job_search))


def test_a_task_that_looks_at_jobs_shows_listings_with_their_own_links_and_remembers_them():
    task = {**TASK, "sources": ["jooble", "adzuna"], "location": "Austin"}
    calls = []

    def job_search(source, keywords, location):
        calls.append((source, keywords, location))
        return [listing(source, 1, f"{source} job")]

    out = run_with_jobs(task, job_search)
    assert sorted(c[0] for c in calls) == ["adzuna", "jooble"] and all(c[1:] == ("remote python jobs", "Austin") for c in calls)
    assert out["status"] == "ok" and out["items"][0]["url"].startswith("https://") and len(out["new_keys"]) == 2
    again = run_with_jobs({**task, "seen_urls": out["new_keys"]}, job_search)
    assert again["status"] == "nothing_new"                                 # the same listings are not news the second time


def test_the_web_and_job_listings_are_shown_together_and_the_web_is_skipped_when_not_picked():
    web = results("https://jobs.example.org/a")
    both = run_with_jobs({**TASK, "sources": ["web", "jooble"]}, lambda s, k, l: [listing("jooble", 1)], answer='{"items": [{"n": 1, "note": "a"}, {"n": 2, "note": "b"}]}', web=web)
    assert {i["domain"] for i in both["items"]} == {"jobs.example.org", "jooble.org"}
    jobs_only = run_with_jobs({**TASK, "sources": ["jooble"]}, lambda s, k, l: [listing("jooble", 1)], web=web)
    assert [i["domain"] for i in jobs_only["items"]] == ["jooble.org"]


def test_one_source_failing_does_not_fail_the_run_but_every_source_failing_does():
    def flaky(source, keywords, location):
        if source == "adzuna":
            raise js.JobSourceError("down")
        return [listing(source, 1)]

    assert run_with_jobs({**TASK, "sources": ["jooble", "adzuna"]}, flaky)["status"] == "ok"

    def down(source, keywords, location):
        raise js.JobSourceError("down")

    assert run_with_jobs({**TASK, "sources": ["jooble", "adzuna"]}, down)["status"] == "failed"


def test_the_model_is_never_shown_more_than_twenty_candidates():
    prompts = []

    async def invoke(prompt):
        prompts.append(prompt)
        return Resp('{"items": []}')

    many = lambda s, k, l: [listing(s, i) for i in range(10)]
    asyncio.run(runner.run_task(Db(), {**TASK, "sources": ["web", "jooble", "adzuna"]}, invoke,
                                search=lambda q: results(*[f"https://s{i}.example.org/a" for i in range(8)]), fetch=lambda url: page(), job_search=many))
    assert "[20]" in prompts[0] and "[21]" not in prompts[0]


def test_job_sources_talk_only_to_their_two_hosts_and_log_nothing():
    source = (ROOT / "backend" / "services" / "job_sources.py").read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]
    assert set(re.findall(r"https://([a-z.]+)", body)) <= {"jooble.org", "api.adzuna.com"}
    assert "logger" not in source and "print(" not in source


# ---- several searches in one task ----------------------------------------------------------------------------------------------------------

MULTI = {**TASK, "topic": "ai engineer", "extra_topics": ["forward deployed engineer", "python developer"], "sources": ["jooble"]}


def test_each_search_runs_on_each_source_inside_the_one_run_and_the_model_is_called_once():
    asked, prompts = [], []

    def job_search(source, keywords, location):
        asked.append(keywords)
        return [listing(source, f"{keywords}-{n}", f"{keywords} job {n}") for n in range(2)]

    async def invoke(prompt):
        prompts.append(prompt)
        return Resp('{"headline": "h", "items": [{"n": 1, "note": "a"}, {"n": 2, "note": "b"}, {"n": 3, "note": "c"}]}')

    out = asyncio.run(runner.run_task(Db(), MULTI, invoke, search=lambda q: [], fetch=lambda url: page(), job_search=job_search))
    assert sorted(asked) == ["ai engineer", "forward deployed engineer", "python developer"]
    assert len(prompts) == 1 and out["status"] == "ok" and len(out["items"]) == 3
    assert "SEARCHES (each result says which one found it)" in prompts[0] and "found by search: forward deployed engineer" in prompts[0]
    # the first three results the model sees come from three different searches, not three from the first one
    first_three = re.findall(r"found by search: ([^\n]+)", prompts[0])[:3]
    assert sorted(first_three) == ["ai engineer", "forward deployed engineer", "python developer"]


def test_a_task_with_one_search_is_prompted_and_limited_as_before():
    prompts = []

    async def invoke(prompt):
        prompts.append(prompt)
        return Resp('{"items": []}')

    asyncio.run(runner.run_task(Db(), {**TASK, "sources": ["jooble"]}, invoke, search=lambda q: [], fetch=lambda url: page(), job_search=lambda s, k, l: [listing(s, 1)]))
    assert "TASK: remote python jobs" in prompts[0] and "found by search" not in prompts[0] and "Pick up to 6 " in prompts[0]
    assert ut.items_limit({"topic": "a"}) == 6 and ut.items_limit(MULTI) == 12 and ut.items_limit({**MULTI, "extra_topics": ["python developer"]}) == 8


def test_a_listing_two_searches_both_found_is_shown_once_and_one_failing_search_does_not_fail_the_run():
    def job_search(source, keywords, location):
        if keywords == "python developer":
            raise js.JobSourceError("down")
        return [listing(source, "same")]

    out = run_with_jobs(MULTI, job_search, answer='{"items": [{"n": 1, "note": "a"}, {"n": 2, "note": "b"}]}')
    assert out["status"] == "ok" and len(out["items"]) == 1 and len(out["new_keys"]) == 1


def test_the_extra_searches_are_cleaned_capped_and_held_to_the_same_rules_as_the_first():
    base = {**SPEC, "topic": "ai engineer"}
    spec, issue = ut.validate_spec({**base, "extra_topics": ["  forward deployed\n engineer ", "AI Engineer", "", "python developer", "python developer"]})
    assert issue is None and spec["extra_topics"] == ["forward deployed engineer", "python developer"]
    assert ut.validate_spec({**base, "extra_topics": ["a", "b", "c", "d"]})[1] == "Each search needs at least a few words."
    assert "up to 4 searches" in ut.validate_spec({**base, "extra_topics": ["one two", "three four", "five six", "seven eight"]})[1]
    assert ut.validate_spec({**base, "extra_topics": ["see https://example.com/jobs"]})[1]                  # a link is refused here too
    assert ut.validate_spec({**base, "extra_topics": ["find out where my ex lives, stalk her"]})[1]         # and so is following a person
    assert ut.validate_spec({**base, "extra_topics": "not a list"})[0]["extra_topics"] == []                  # an old or odd value is just no extras
    assert ut.validate_spec(base)[0]["extra_topics"] == []


def test_a_task_keeps_its_extra_searches_and_changing_them_starts_the_new_results_over():
    db = Db()
    spec = ut.validate_spec({**SPEC, "topic": "ai engineer", "extra_topics": ["python developer"]})[0]
    task, _ = ut.create_task(db, "u1", spec, ut.datetime.now(ut.timezone.utc))
    assert ut.public_task(task)["extra_topics"] == ["python developer"] and ut.search_topics(task) == ["ai engineer", "python developer"]
    db["sonic_user_tasks"].update_one({"id": task["id"]}, {"$set": {"seen_urls": ["k1"]}})
    same, _ = ut.update_task(db, "u1", task["id"], {"title": "Renamed"})
    assert same["seen_urls"] == ["k1"] and same["extra_topics"] == ["python developer"]
    changed, issue = ut.update_task(db, "u1", task["id"], {"extra_topics": ["python developer", "forward deployed engineer"]})
    assert issue is None and changed["extra_topics"] == ["python developer", "forward deployed engineer"] and changed["seen_urls"] == []


def test_a_run_may_store_and_report_up_to_the_hard_cap_of_results():
    assert ut.MAX_ITEMS_HARD_CAP == 12
    source = (ROOT / "backend" / "utils" / "user_tasks.py").read_text(encoding="utf-8")
    assert source.count("[:MAX_ITEMS_HARD_CAP]") == 2 and "[:MAX_ITEMS_PER_RUN]" not in source
