"""The code checks of the behaviour eval (backend/evals/behavior_eval.py): a needle may be a tuple of acceptable alternatives, and the delete route's answer is judged on what it does, not on one reply style."""
from backend.evals import behavior_eval as be

FENCE = "`" * 3


def _answer(*code_blocks: str) -> be.Observation:
    return be.Observation(answer="Here is the change.\n\n" + "\n\n".join(f"{FENCE}python\n{block}\n{FENCE}" for block in code_blocks))


# What Claude Sonnet 5.5 really wrote for codegen_delete_customer (2026-11-02): the route answers "", 204 and never says jsonify, and the check marked it wrong.
SONNET_DELETE = _answer(
    "def delete_customer(customer_id):\n    return _CUSTOMERS.pop(customer_id)",
    'from tallybox.models.customer import (\n    delete_customer,\n    find_customer,\n    list_customers,\n    new_customer,\n)\n\n'
    '@bp.route("/customers/<int:customer_id>", methods=["DELETE"])\n@require_auth\ndef destroy(customer_id):\n    delete_customer(customer_id)\n    return "", 204',
)

JSONIFY_DELETE = _answer(
    '@bp.route("/customers/<int:customer_id>", methods=["DELETE"])\n@require_auth\ndef destroy(customer_id):\n    delete_customer(customer_id)\n    return jsonify({"deleted": customer_id})'
)


def test_a_delete_route_that_answers_no_content_passes_like_one_that_answers_jsonify():
    assert be.score_codegen_delete_customer(SONNET_DELETE) == (True, "")
    assert be.score_codegen_delete_customer(JSONIFY_DELETE) == (True, "")


def test_a_delete_route_that_is_not_the_repos_kind_still_fails():
    no_auth = _answer('@bp.route("/customers/<int:customer_id>", methods=["DELETE"])\ndef destroy(customer_id):\n    return jsonify({})')
    ok, why = be.score_codegen_delete_customer(no_auth)
    assert not ok and "@require_auth" in why
    plain_text = _answer('@bp.route("/customers/<int:customer_id>", methods=["DELETE"])\n@require_auth\ndef destroy(customer_id):\n    return "gone"')
    ok, why = be.score_codegen_delete_customer(plain_text)
    assert not ok and "('jsonify', '204')" in why                       # the report names the alternatives, not one of them
    ok, why = be.score_codegen_delete_customer(be.Observation(answer="I would add a route that deletes the customer."))
    assert not ok and "no code" in why


def test_the_overdue_rule_may_be_a_timedelta_or_a_days_comparison_but_must_apply_the_grace_days():
    with_timedelta = _answer("def is_overdue(invoice, settings, today):\n    return invoice.status == 'sent' and invoice.due_date + timedelta(days=settings.grace_days) < today")
    with_days = _answer("def is_overdue(invoice, settings, today):\n    return invoice.status == 'sent' and (today - invoice.due_date).days > settings.grace_days")
    assert be.score_codegen_is_overdue(with_timedelta) == (True, "")
    assert be.score_codegen_is_overdue(with_days) == (True, "")
    no_grace = _answer("def is_overdue(invoice, today):\n    return invoice.status == 'sent' and (today - invoice.due_date).days > 0")
    ok, why = be.score_codegen_is_overdue(no_grace)
    assert not ok and "grace_days" in why


def test_a_needle_may_be_a_plain_string_or_a_tuple_of_alternatives():
    code = _answer("x = 1  # uses alpha")
    assert be._codegen_score(code, "alpha") == (True, "")
    assert be._codegen_score(code, ("beta", "alpha")) == (True, "")
    ok, why = be._codegen_score(code, ("beta", "gamma"))
    assert not ok and "beta" in why and "gamma" in why


def test_the_failing_answer_kept_in_the_report_is_as_long_as_asked(monkeypatch):
    observation = be.Observation(actions=["(final)"], answer="word " * 200)
    assert len(be._summary(observation)) < 260                          # the default keeps just enough to see which way a run went
    monkeypatch.setattr(be, "ANSWER_EXCERPT_CHARS", 4000)
    assert len(be._summary(observation)) > 900                          # EVAL_ANSWER_CHARS=4000 keeps a whole written change
