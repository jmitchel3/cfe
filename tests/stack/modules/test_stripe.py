import json
import logging
from unittest import mock

import pytest
import stripe
from django.urls import include, path

from cfe.stack import hooks, loader
from cfe.stack.contrib.stripe import client
from cfe.stack.module import Env
from cfe.stack.modules.stripe import Stripe, key_mode
from tests.stack.helpers import apply_stack

urlpatterns = [path("stripe/", include("cfe.stack.contrib.stripe.urls"))]

WHSEC = "whsec_test_fake"
SK = "sk_test_fake"
URL = "/stripe/webhook/"
ENV = {"STRIPE_SECRET_KEY": SK, "STRIPE_WEBHOOK_SECRET": WHSEC}


@pytest.fixture(autouse=True)
def stripe_settings(settings):
    settings.ROOT_URLCONF = __name__
    settings.STRIPE_SECRET_KEY = SK
    settings.STRIPE_WEBHOOK_SECRET = WHSEC
    settings.STRIPE_API_VERSION = None
    hooks.clear()
    yield settings
    hooks.clear()


@pytest.fixture
def calls():
    recorded = []
    names = [
        "checkout_completed",
        "subscription_activated",
        "subscription_updated",
        "subscription_cancelled",
    ]
    for name in names:
        hooks.add_action(name, lambda _n=name, **kw: recorded.append((_n, kw)))
    return recorded


def make_event(etype, obj, previous=None):
    data = {"object": obj}
    if previous is not None:
        data["previous_attributes"] = previous
    return json.dumps({"id": "evt_1", "object": "event", "type": etype, "data": data})


def post(client_, payload, secret=WHSEC, header=None):
    if header is None:
        header = stripe.WebhookSignature.generate_signature_header(payload, secret)
    return client_.post(
        URL, data=payload, content_type="application/json", HTTP_STRIPE_SIGNATURE=header
    )


def sub(status="active", **extra):
    return {"id": "sub_1", "object": "subscription", "customer": "cus_1", "status": status, **extra}


def test_configure_sets_settings_apps_and_slot():
    ns = {"INSTALLED_APPS": []}
    ns = apply_stack("stripe", {**ENV, "STRIPE_API_VERSION": "2026-09-30"}, ns)
    assert ns["STRIPE_SECRET_KEY"] == SK
    assert ns["STRIPE_WEBHOOK_SECRET"] == WHSEC
    assert ns["STRIPE_PUBLISHABLE_KEY"] is None
    assert ns["STRIPE_API_VERSION"] == "2026-09-30"
    assert "cfe.stack.contrib.stripe" in ns["INSTALLED_APPS"]
    assert ns["CFE_STACK_SLOTS"] == {"billing": "stripe"}


def test_required_env():
    with pytest.raises(loader.StackConfigError) as exc:
        loader.build(["stripe"], environ={})
    assert "STRIPE_SECRET_KEY" in str(exc.value)
    assert "STRIPE_WEBHOOK_SECRET" in str(exc.value)
    secrets = {v.name for v in Stripe.env if v.secret}
    assert secrets == {"STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"}


def test_client_uses_settings(stripe_settings):
    stripe_settings.STRIPE_API_VERSION = "2026-09-30.endive"
    with mock.patch("stripe.StripeClient") as cls:
        client()
    cls.assert_called_once_with(SK, stripe_version="2026-09-30.endive")
    assert isinstance(client(), stripe.StripeClient)


def test_get_is_405(client):
    assert client.get(URL).status_code == 405


def test_bad_signature_is_400(client, calls):
    payload = make_event("customer.subscription.deleted", sub("canceled"))
    assert post(client, payload, secret="whsec_wrong").status_code == 400
    assert post(client, payload, header="").status_code == 400
    assert calls == []


def test_missing_signature_header_is_400(client):
    payload = make_event("customer.subscription.deleted", sub("canceled"))
    resp = client.post(URL, data=payload, content_type="application/json")
    assert resp.status_code == 400


def test_bad_payload_is_400(client):
    assert post(client, "not json").status_code == 400


def test_missing_webhook_secret_is_400(client, stripe_settings):
    stripe_settings.STRIPE_WEBHOOK_SECRET = None
    assert post(client, make_event("ping", {})).status_code == 400


def test_raw_event_hook_and_unmapped_type(client, calls):
    seen = []
    hooks.add_action("stripe.invoice.paid", lambda event: seen.append(event.type))
    resp = post(client, make_event("invoice.paid", {"id": "in_1", "object": "invoice"}))
    assert resp.status_code == 200
    assert seen == ["invoice.paid"]
    assert calls == []


def test_checkout_completed(client, calls):
    obj = {
        "id": "cs_1",
        "object": "checkout.session",
        "customer": "cus_1",
        "subscription": "sub_1",
        "status": "complete",
    }
    assert post(client, make_event("checkout.session.completed", obj)).status_code == 200
    [(name, kw)] = calls
    assert name == "checkout_completed"
    assert kw["stripe_customer_id"] == "cus_1"
    assert kw["stripe_subscription_id"] == "sub_1"
    assert kw["status"] == "complete"
    assert kw["event"].type == "checkout.session.completed"


def test_checkout_completed_without_subscription(client, calls):
    obj = {"id": "cs_1", "object": "checkout.session", "customer": None, "subscription": None}
    post(client, make_event("checkout.session.completed", obj))
    [(_name, kw)] = calls
    assert kw["stripe_customer_id"] is None
    assert "stripe_subscription_id" not in kw
    assert "status" not in kw


def test_subscription_created_activates(client, calls):
    post(client, make_event("customer.subscription.created", sub("trialing")))
    [(name, kw)] = calls
    assert name == "subscription_activated"
    assert kw["stripe_subscription_id"] == "sub_1"
    assert kw["status"] == "trialing"


def test_subscription_updated_into_active(client, calls):
    post(client, make_event("customer.subscription.updated", sub(), {"status": "incomplete"}))
    assert [n for n, _ in calls] == ["subscription_updated", "subscription_activated"]
    assert all(kw["stripe_customer_id"] == "cus_1" for _, kw in calls)


@pytest.mark.parametrize(
    "previous",
    [{"cancel_at_period_end": True}, {"status": "trialing"}],
)
def test_subscription_updated_not_activation(client, calls, previous):
    post(client, make_event("customer.subscription.updated", sub(), previous))
    assert [n for n, _ in calls] == ["subscription_updated"]


def test_subscription_deleted(client, calls):
    obj = sub("canceled", customer={"id": "cus_9", "object": "customer"})
    post(client, make_event("customer.subscription.deleted", obj))
    [(name, kw)] = calls
    assert name == "subscription_cancelled"
    assert kw["stripe_customer_id"] == "cus_9"
    assert kw["status"] == "canceled"


def test_handler_error_is_not_500(client, caplog):
    def boom(**kw):
        raise RuntimeError("handler bug")

    hooks.add_action("subscription_cancelled", boom)
    with caplog.at_level(logging.INFO):
        resp = post(client, make_event("customer.subscription.deleted", sub("canceled")))
    assert resp.status_code == 200
    assert "handler bug" in caplog.text
    assert WHSEC not in caplog.text
    assert "cus_1" not in caplog.text


def test_key_mode():
    assert key_mode("sk_test_x") == "test"
    assert key_mode("rk_live_x") == "live"
    assert key_mode("pk_live_x") == "live"
    assert key_mode("") == "unknown"
    assert key_mode(None) == "unknown"


def _env(**over):
    return Env(
        {
            "STRIPE_SECRET_KEY": "sk_test_supersecret",
            "STRIPE_PUBLISHABLE_KEY": None,
            "STRIPE_WEBHOOK_SECRET": "whsec_supersecret",
            "STRIPE_API_VERSION": None,
            **over,
        }
    )


def test_checks_ok():
    with mock.patch("stripe.StripeClient") as cls:
        assert Stripe().checks(_env(STRIPE_PUBLISHABLE_KEY="pk_test_x")) == []
    cls.return_value.v1.accounts.retrieve_current.assert_called_once_with()


def test_checks_failure_hides_key():
    err = stripe.AuthenticationError("Invalid API Key provided: sk_test_****cret", http_status=401)
    with mock.patch("stripe.StripeClient") as cls:
        cls.return_value.v1.accounts.retrieve_current.side_effect = err
        problems = Stripe().checks(_env(STRIPE_PUBLISHABLE_KEY="pk_live_x"))
    text = " ".join(problems)
    assert "AuthenticationError, HTTP 401" in text
    assert "test mode" in text
    assert "does not match" in text
    assert "supersecret" not in text
    assert "sk_test" not in text and "cret" not in text
