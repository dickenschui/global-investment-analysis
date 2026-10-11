import json
import smtplib

import pytest

from global_investment_research import mailer

ENV = {"MAIL_USERNAME": "owner@gmail.com", "MAIL_APP_PASSWORD": "abcd efgh ijkl mnop"}


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, context=None, fail=None):
        self.host, self.port, self.sent, self.login_args = host, port, [], None
        self.fail = fail
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        self.login_args = (user, password)
        if self.fail == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted " + password.encode())

    def send_message(self, msg):
        if self.fail == "io":
            raise OSError(f"boom {ENV['MAIL_APP_PASSWORD']}")
        self.sent.append(msg)
        return {}


@pytest.fixture(autouse=True)
def reset():
    FakeSMTP.instances.clear()


def test_send_builds_multipart_and_defaults_recipient():
    res = mailer.send("Alerts 2026-10-12", "<p>hi</p>", "hi", env=ENV, smtp_factory=FakeSMTP)
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 465)
    assert smtp.login_args == ("owner@gmail.com", "abcd efgh ijkl mnop")
    msg = smtp.sent[0]
    assert msg["To"] == "owner@gmail.com" and msg["From"] == "owner@gmail.com" and msg["Subject"] == "Alerts 2026-10-12"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]
    assert res["status"] == "sent" and res["recipients"] == 1


def test_mail_to_and_explicit_to():
    env = {**ENV, "MAIL_TO": "a@example.com; b@example.org"}
    mailer.send("s", "<p/>", "t", env=env, smtp_factory=FakeSMTP)
    assert FakeSMTP.instances[0].sent[0]["To"] == "a@example.com, b@example.org"
    mailer.send("s", "<p/>", "t", to="c@example.com", env=env, smtp_factory=FakeSMTP)
    assert FakeSMTP.instances[1].sent[0]["To"] == "c@example.com"
    with pytest.raises(mailer.MailError):
        mailer.send("s", "<p/>", "t", to="not-an-address", env=env, smtp_factory=FakeSMTP)


@pytest.mark.parametrize("env", [{}, {"MAIL_USERNAME": "owner@gmail.com"}, {"MAIL_USERNAME": "", "MAIL_APP_PASSWORD": ""}])
def test_refuses_when_unconfigured(env):
    assert not mailer.configured(env)
    with pytest.raises(mailer.MailNotConfigured, match="MAIL_USERNAME and MAIL_APP_PASSWORD"):
        mailer.send("s", "<p/>", "t", env=env, smtp_factory=FakeSMTP)
    assert FakeSMTP.instances == []
    assert mailer.send_if_configured("s", "<p/>", "t", env=env, smtp_factory=FakeSMTP)["reason"].startswith("email skipped")


def test_ledger_prevents_second_send_same_subject_and_day(tmp_path):
    ledger = tmp_path / "state" / "mail_ledger.json"
    first = mailer.send("Alerts", "<p/>", "t", env=ENV, smtp_factory=FakeSMTP, ledger=ledger, day="2026-10-12")
    again = mailer.send("Alerts", "<p/>", "t", env=ENV, smtp_factory=FakeSMTP, ledger=ledger, day="2026-10-12")
    assert first["status"] == "sent" and again["status"] == "already sent"
    assert len(FakeSMTP.instances) == 1
    mailer.send("Alerts", "<p/>", "t", env=ENV, smtp_factory=FakeSMTP, ledger=ledger, day="2026-10-13")
    assert len(FakeSMTP.instances) == 2
    text = ledger.read_text()
    assert set(json.loads(text)["sent"]) == {"2026-10-12|Alerts", "2026-10-13|Alerts"}
    assert ENV["MAIL_APP_PASSWORD"] not in text and "owner@gmail.com" not in text


@pytest.mark.parametrize("fail", ["auth", "io"])
def test_errors_never_contain_secret_and_ledger_not_written(tmp_path, fail):
    ledger = tmp_path / "l.json"
    factory = lambda *a, **k: FakeSMTP(*a, fail=fail, **k)  # noqa: E731
    with pytest.raises(mailer.MailError) as info:
        mailer.send("s", "<p/>", "t", env=ENV, smtp_factory=factory, ledger=ledger, day="2026-10-12")
    assert ENV["MAIL_APP_PASSWORD"] not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert not ledger.exists()
