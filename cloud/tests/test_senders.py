"""The Aliyun SMS sender: the two services, the request each one gets, the signature, the errors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse

import httpx
import pytest

from nanomuse_cloud import senders
from nanomuse_cloud.config import Settings
from nanomuse_cloud.identifiers import parse
from nanomuse_cloud.senders import AliyunSmsSender, BothSender, SendError, SmtpSender, make_sender


def aliyun_settings(**overrides) -> Settings:
    kw = dict(
        database=":memory:",
        secret="s",
        sender="aliyun",
        aliyun_access_key_id="LTAI-test",
        aliyun_access_key_secret="secret",
        aliyun_sms_sign="恒创联众",
        aliyun_sms_template="100001",
        code_ttl_s=600,
    )
    kw.update(overrides)
    return Settings(**kw)


class FakeGet:
    def __init__(self, body: dict):
        self.body, self.urls = body, []

    def __call__(self, url: str, timeout: float):
        self.urls.append(url)
        return httpx.Response(200, json=self.body)


def test_dypns_is_the_default_and_sends_our_code_with_the_minutes(monkeypatch):
    s = aliyun_settings()
    sender = AliyunSmsSender(s)
    assert sender.api == "dypns" and sender.endpoint == "https://dypnsapi.aliyuncs.com/"
    fake = FakeGet({"Code": "OK", "Model": {"BizId": "1"}})
    monkeypatch.setattr(senders.httpx, "get", fake)
    sender.send(parse("13800138000"), "482913")
    assert len(fake.urls) == 1
    q = dict(urllib.parse.parse_qsl(fake.urls[0].split("?", 1)[1], keep_blank_values=True))
    assert q["Action"] == "SendSmsVerifyCode" and q["Version"] == "2017-05-25"
    assert q["PhoneNumber"] == "13800138000" and q["CountryCode"] == "86"  # no +86 on the wire
    assert q["SignName"] == "恒创联众" and q["TemplateCode"] == "100001"
    assert json.loads(q["TemplateParam"]) == {"code": "482913", "min": "10"}
    assert q["ValidTime"] == "600" and q["ReturnVerifyCode"] == "false"
    assert "Signature" in q and "AccessKeyId" in q and "PhoneNumbers" not in q


def test_dysms_keeps_the_old_send_sms_request(monkeypatch):
    sender = AliyunSmsSender(aliyun_settings(aliyun_sms_api="dysms"))
    assert sender.endpoint == "https://dysmsapi.aliyuncs.com/"
    fake = FakeGet({"Code": "OK"})
    monkeypatch.setattr(senders.httpx, "get", fake)
    sender.send(parse("+85212345678"), "111222")  # non-mainland is fine here, country code and no plus
    q = dict(urllib.parse.parse_qsl(fake.urls[0].split("?", 1)[1]))
    assert q["Action"] == "SendSms" and q["PhoneNumbers"] == "85212345678"
    assert json.loads(q["TemplateParam"]) == {"code": "111222"}


def test_the_signature_is_the_rpc_hmac_sha1_over_the_sorted_query():
    sender = AliyunSmsSender(aliyun_settings())
    params = {"Action": "SendSmsVerifyCode", "AccessKeyId": "LTAI-test", "TemplateParam": '{"code":"1","min":"10"}', "Z": "a b~"}
    query = sender._signed_query(params)
    canonical, sig = query.rsplit("&Signature=", 1)
    assert canonical.startswith("AccessKeyId=LTAI-test&Action=SendSmsVerifyCode&TemplateParam=%7B%22code%22")
    assert canonical.endswith("&Z=a%20b~")  # spaces as %20, tilde untouched
    expected = base64.b64encode(
        hmac.new(b"secret&", ("GET&%2F&" + urllib.parse.quote(canonical, safe="~")).encode(), hashlib.sha1).digest()
    ).decode()
    assert urllib.parse.unquote(sig) == expected


def test_rejections_and_wrong_channels_are_send_errors_without_the_number(monkeypatch, caplog):
    sender = AliyunSmsSender(aliyun_settings())
    monkeypatch.setattr(senders.httpx, "get", FakeGet({"Code": "BUSINESS_LIMIT_CONTROL", "Message": "too many"}))
    with pytest.raises(SendError):
        sender.send(parse("13800138000"), "000000")
    assert "BUSINESS_LIMIT_CONTROL" in caplog.text and "13800138000" not in caplog.text
    with pytest.raises(SendError):  # dypns is mainland only
        sender.send(parse("+85212345678"), "000000")
    with pytest.raises(SendError):  # and never mail
        sender.send(parse("someone@example.com"), "000000")

    def boom(url, timeout):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(senders.httpx, "get", boom)
    with pytest.raises(SendError):
        sender.send(parse("13800138000"), "000000")


def test_make_sender_both_routes_phones_to_sms_and_addresses_to_mail(monkeypatch):
    s = aliyun_settings(sender="both", smtp_host="smtp.test", smtp_from="nanoMuse <no-reply@test>")
    both = make_sender(s)
    assert isinstance(both, BothSender) and isinstance(both.mail, SmtpSender) and isinstance(both.sms, AliyunSmsSender)
    fake = FakeGet({"Code": "OK"})
    monkeypatch.setattr(senders.httpx, "get", fake)
    both.send(parse("13800138000"), "123456")
    assert len(fake.urls) == 1
    mailed = []
    monkeypatch.setattr(both.mail, "send", lambda ident, code: mailed.append((ident.value, code)))
    both.send(parse("dev-a@example.com"), "654321")
    assert mailed == [("dev-a@example.com", "654321")] and len(fake.urls) == 1


def test_the_code_mail_states_the_lifetime_in_whole_minutes_like_the_sms(monkeypatch):
    """Both templates say how long the code is good for in minutes, rounded the same way:
    CODE_TTL_S=600 reads as ten, and a lifetime under a minute still reads as one, never
    as zero. The mail is composed with the settings' figure, in both languages."""
    sent = []
    monkeypatch.setattr(senders, "smtp_send", lambda s, msg: sent.append(msg))
    mail = SmtpSender(aliyun_settings(sender="smtp", smtp_host="smtp.test", smtp_from="nanoMuse <no-reply@test>"))
    mail.send(parse("dev-a@example.com"), "482913")
    body = sent[-1].get_body(("plain",)).get_content()
    assert "482913" in body and "10 分钟内有效" in body and "expires in 10 minutes" in body
    short = SmtpSender(aliyun_settings(sender="smtp", smtp_host="smtp.test", smtp_from="nanoMuse <no-reply@test>", code_ttl_s=30))
    short.send(parse("dev-a@example.com"), "482913")
    body = sent[-1].get_body(("plain",)).get_content()
    assert "1 分钟内有效" in body and "expires in 1 minutes" in body and "0 分钟" not in body
    sms = AliyunSmsSender(aliyun_settings(code_ttl_s=30))
    assert json.loads(sms.params(parse("13800138000"), "482913")["TemplateParam"]) == {"code": "482913", "min": "1"}


def test_unknown_api_and_missing_settings_are_refused_at_startup():
    with pytest.raises(ValueError):
        AliyunSmsSender(aliyun_settings(aliyun_sms_api="carrier-pigeon"))
    with pytest.raises(ValueError):
        AliyunSmsSender(aliyun_settings(aliyun_sms_template=""))


def test_senders_say_up_front_whom_they_can_reach():
    dypns = AliyunSmsSender(aliyun_settings())
    assert dypns.accepts(parse("13800138000"))
    assert not dypns.accepts(parse("+85212345678"))  # 号码认证: mainland only
    assert not dypns.accepts(parse("dev-a@example.com"))
    dysms = AliyunSmsSender(aliyun_settings(aliyun_sms_api="dysms"))
    assert dysms.accepts(parse("+85212345678")) and not dysms.accepts(parse("dev-a@example.com"))
    mail = SmtpSender(aliyun_settings(sender="smtp", smtp_host="smtp.test", smtp_from="nanoMuse <no-reply@test>"))
    assert mail.accepts(parse("dev-a@example.com")) and not mail.accepts(parse("13800138000"))
    both = BothSender(mail, dypns)
    assert both.accepts(parse("13800138000")) and both.accepts(parse("dev-a@example.com"))
    assert not both.accepts(parse("+14155550100"))


def test_a_number_the_sender_cannot_reach_is_a_400_before_any_code_exists(monkeypatch):
    """An overseas number gets `phone_region` right away — not a code that never arrives,
    not a 502 — and nothing is counted against it."""
    import asyncio

    from nanomuse_cloud.api import create_app
    from nanomuse_cloud.db import Database
    from nanomuse_cloud.service import Cloud

    s = aliyun_settings(sender="both", smtp_host="smtp.test", smtp_from="nanoMuse <no-reply@test>", public_base="http://cloud.test")
    calls = FakeGet({"Code": "OK"})
    monkeypatch.setattr(senders.httpx, "get", calls)
    cloud = Cloud(s, Database(":memory:"), make_sender(s))
    app = create_app(s, cloud)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test") as c:
            r = await c.post("/v1/auth/code", json={"identifier": "+1 415 555 0100"})
            assert r.status_code == 400 and r.json()["error"]["code"] == "phone_region"
            assert "e-mail" in r.json()["error"]["message"]
            r = await c.post("/v1/auth/code", json={"identifier": "+852 1234 5678"})
            assert r.status_code == 400 and r.json()["error"]["code"] == "phone_region"
            r = await c.post("/v1/auth/code", json={"identifier": "138 0013 8000"})
            assert r.status_code == 204

    asyncio.run(run())
    assert len(calls.urls) == 1  # only the mainland number went to Aliyun
    assert cloud.db.codes_recent_for(parse("+14155550100").hash(s.hmac_key), 0) == 0


def test_smtp_failures_are_logged_without_the_address():
    import smtplib

    refused = smtplib.SMTPRecipientsRefused({"someone@example.com": (550, b"no such user")})
    text = senders.smtp_error_text(refused)
    assert "someone@example.com" not in text and "550" in text and "1 recipient" in text
    sender_refused = smtplib.SMTPSenderRefused(553, b"relay denied", "relay@example.com")
    text = senders.smtp_error_text(sender_refused)
    assert "relay@example.com" not in text and "553" in text and "relay denied" in text
    assert senders.smtp_error_text(OSError("connection refused")).startswith("OSError: connection refused")
