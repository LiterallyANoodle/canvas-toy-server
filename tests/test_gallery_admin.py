"""Phases 2-4 (T-0049): the gallery page, anonymous comments, timed bans and /admin."""
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.admin_auth import AccessDenied, AccessVerifier
from app.main import clean_comment
from tests.test_app import make, submit  # noqa: F401  (the fixture)
from tests.test_units import canvas_like, data_url

IP = "203.0.113.7"
HOST = "canvas.example"


def draw(client, n=1, ip=IP):
    for _ in range(n):
        assert submit(client, data_url(canvas_like()), ip=ip).status_code == 200


def comment(client, number, body, ip=IP, **extra):
    return client.post(f"/dragon-gallery/image/{number}/comments", data={"body": body, **extra},
                       headers={"CF-Connecting-IP": ip}, follow_redirects=False)


# --- gallery ---------------------------------------------------------------
def test_gallery_root_goes_to_the_first_drawing(make):
    client, drawings, *_ = make()
    r = client.get("/dragon-gallery", follow_redirects=False)
    assert r.status_code == 200 and "No drawings yet" in r.text
    draw(client, 3)
    drawings.rows[1] = drawings.rows[1][:3] + (True,)          # #1 hidden still holds slot 1 (msg 557)
    assert client.get("/dragon-gallery", follow_redirects=False).headers["location"] == "/dragon-gallery/image/1"


def test_the_gallery_page_shows_the_drawing_in_the_frame(make):
    client, drawings, *_ = make()
    draw(client, 3)
    r = client.get("/dragon-gallery/image/2")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    drawing_id = drawings.rows[2][0]
    assert f'src="/images/{drawing_id}.png"' in r.text
    assert "Square-Gold-Frame-PNG-908289183.png" in r.text and "Dragon Gallery" in r.text
    assert ">Lot No. 2<" in r.text
    assert 'href="/dragon-gallery/image/1"' in r.text and 'href="/dragon-gallery/image/3"' in r.text
    for asset in ("Square-Gold-Frame-PNG-908289183.png", "white_marble.jpg", "sword3.gif", "wooden_sign_dark.png"):
        assert client.get(f"/Assets/{asset}").status_code == 200, asset


def test_navigation_steps_through_hidden_slots(make):
    client, drawings, *_ = make()
    draw(client, 3)
    drawings.rows[2] = drawings.rows[2][:3] + (True,)
    r = client.get("/dragon-gallery/image/1")
    assert 'id="nav-next" href="/dragon-gallery/image/2"' in r.text
    page = client.get("/dragon-gallery/image/2")
    assert page.status_code == 200 and "This lot has been hidden" in page.text


def test_a_missing_drawing_gets_a_themed_404(make):
    client, *_ = make()
    r = client.get("/dragon-gallery/image/42")
    assert r.status_code == 404 and "no drawing No. 42" in r.text


def test_the_public_page_never_shows_ips(make):
    client, *_ = make()
    draw(client)
    comment(client, 1, "hello")
    assert IP not in client.get("/dragon-gallery/image/1").text


def test_discord_caption_links_the_gallery_when_the_url_is_known(make):
    client, drawings, webhook, _ = make(public_base_url="https://canvas.example")
    draw(client)
    assert f"https://canvas.example/dragon-gallery/d/{drawings.rows[1][0]}" in webhook.calls[0][2]


# --- comments --------------------------------------------------------------
def test_a_comment_is_posted_and_shown_escaped(make):
    client, *_ = make()
    draw(client)
    r = comment(client, 1, "<script>alert(1)</script> nice dragon")
    assert r.status_code == 303 and r.headers["location"] == "/dragon-gallery/image/1?c=posted#comments"
    page = client.get(r.headers["location"]).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; nice dragon" in page and "<script>alert" not in page
    assert "Your comment is up" in page


def test_comment_rules(make):
    client, *_ = make(comment_max_chars=20)
    draw(client)
    assert comment(client, 1, "   \n\x00 ").headers["location"].endswith("c=empty#comments")
    assert comment(client, 1, "x" * 21).headers["location"].endswith("c=long#comments")
    assert comment(client, 2, "hi").status_code == 404
    assert client.comments.rows == {}


def test_the_honeypot_drops_bot_comments_quietly(make):
    client, *_ = make()
    draw(client)
    r = comment(client, 1, "buy pills", website="http://spam.example")
    assert r.headers["location"].endswith("c=posted#comments") and client.comments.rows == {}


def test_comments_are_rate_limited_per_ip(make):
    client, *_ = make(comment_rate_limit_per_ip=2)
    draw(client)
    keys = [comment(client, 1, f"c{i}").headers["location"].split("c=")[1] for i in range(3)]
    assert keys == ["posted#comments", "posted#comments", "slow#comments"]
    assert comment(client, 1, "other", ip="198.51.100.1").headers["location"].endswith("c=posted#comments")


def test_comments_need_the_client_ip_header(make):
    client, *_ = make()
    draw(client)
    r = client.post("/dragon-gallery/image/1/comments", data={"body": "hi"}, follow_redirects=False)
    assert r.headers["location"].endswith("c=error#comments") and client.comments.rows == {}


def test_query_notices_never_echo_input(make):
    client, *_ = make()
    draw(client)
    assert "<b>x</b>" not in client.get("/dragon-gallery/image/1?c=<b>x</b>").text


def test_clean_comment():
    assert clean_comment("  a\r\nb\x07\n\n\n\nc  ") == "a\nb\n\nc"


# --- bans ------------------------------------------------------------------
def ban(client, network, scope="all", minutes=60):
    import asyncio
    asyncio.run(client.bans.add(network, scope, datetime.now(timezone.utc) + timedelta(minutes=minutes), ""))


def test_a_ban_blocks_drawing_and_says_until_when(make):
    client, drawings, *_ = make()
    ban(client, "203.0.113.0/24")
    r = submit(client, data_url(canvas_like()))
    assert r.status_code == 403 and "timeout until" in r.text and drawings.rows == {}
    assert submit(client, data_url(canvas_like()), ip="198.51.100.1").status_code == 200


def test_ban_scopes(make):
    client, *_ = make()
    draw(client)
    ban(client, IP + "/32", scope="comment")
    assert comment(client, 1, "hi").headers["location"].endswith("c=banned&b=1#comments")
    draw(client)                                           # drawing still allowed
    ban(client, "198.51.100.1/32", scope="draw")
    assert comment(client, 1, "hi", ip="198.51.100.1").headers["location"].endswith("c=posted#comments")


def test_expired_bans_do_nothing(make):
    client, *_ = make()
    ban(client, IP + "/32", minutes=-1)
    draw(client)


# --- admin -----------------------------------------------------------------
class AllowAll:
    async def __call__(self, headers):
        if headers.get("Cf-Access-Jwt-Assertion") != "good":
            raise AccessDenied("nope")
        return "noodle@example.com"


ADMIN = {"Cf-Access-Jwt-Assertion": "good", "Host": HOST, "Origin": f"https://{HOST}"}


def admin_post(client, path, data=None, headers=ADMIN):
    return client.post(path, data=data or {}, headers=headers, follow_redirects=False)


def test_admin_is_off_until_access_is_configured(make):
    client, *_ = make()
    assert client.get("/admin").status_code == 404
    assert client.post("/admin/bans", data={"network": IP}).status_code == 404


def test_admin_needs_a_valid_access_token(make):
    client, *_ = make(admin_verifier=AllowAll())
    assert client.get("/admin").status_code == 403
    assert client.get("/admin", headers={"Cf-Access-Jwt-Assertion": "forged"}).status_code == 403
    r = client.get("/admin", headers=ADMIN)
    assert r.status_code == 200 and "noodle@example.com" in r.text
    assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"


def test_admin_posts_must_come_from_this_site(make):
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client)
    did = drawings.rows[1][0]
    for headers in ({"Cf-Access-Jwt-Assertion": "good", "Host": HOST},
                    {**ADMIN, "Origin": "https://evil.example"},
                    {**ADMIN, "Origin": "null"}):
        assert admin_post(client, f"/admin/drawings/{did}/hide", headers=headers).status_code == 403
    assert drawings.rows[1][3] is False
    referer_only = {"Cf-Access-Jwt-Assertion": "good", "Host": HOST, "Referer": f"https://{HOST}/admin"}
    assert admin_post(client, f"/admin/drawings/{did}/hide", headers=referer_only).status_code == 403
    assert admin_post(client, f"/admin/drawings/{did}/hide").status_code == 303


def test_admin_lists_everything_with_ips(make):
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "first!")
    drawings.rows[1] = drawings.rows[1][:3] + (True,)
    page = client.get("/admin", headers=ADMIN).text
    assert IP in page and "first!" in page and "hidden" in page
    did = drawings.rows[1][0]
    assert client.get(f"/admin/images/{did}.png", headers=ADMIN).status_code == 200   # hidden, still viewable here
    assert client.get(f"/images/{did}.png").status_code == 404
    assert client.get(f"/admin/images/{did}.png").status_code == 403


def test_admin_hides_unhides_and_deletes_drawings(make):
    client, drawings, _, settings = make(admin_verifier=AllowAll())
    draw(client)
    did = drawings.rows[1][0]
    assert admin_post(client, f"/admin/drawings/{did}/hide").headers["location"] == "/admin?n=hidden#drawings"
    assert client.get(f"/images/{did}.png").status_code == 404
    assert "This lot has been hidden" in client.get("/dragon-gallery/image/1").text
    admin_post(client, f"/admin/drawings/{did}/unhide")
    assert client.get(f"/images/{did}.png").status_code == 200
    assert admin_post(client, f"/admin/drawings/{did}/delete").headers["location"] == "/admin?n=deleted#drawings"
    assert drawings.rows == {} and not (settings.images_dir / f"{did}.png").exists()
    assert admin_post(client, f"/admin/drawings/{did}/delete").headers["location"] == "/admin?n=missing#drawings"
    assert admin_post(client, f"/admin/drawings/{did}/explode").status_code == 404


def test_admin_moderates_comments(make):
    client, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "rude words")
    admin_post(client, "/admin/comments/1/hide")
    assert "rude words" not in client.get("/dragon-gallery/image/1").text
    admin_post(client, "/admin/comments/1/delete")
    assert client.comments.rows == {}


def test_admin_bans_and_lifts(make):
    client, *_ = make(admin_verifier=AllowAll())
    r = admin_post(client, "/admin/bans", {"network": IP, "duration": "1d", "scope": "all", "reason": "spam"})
    assert r.headers["location"] == "/admin?n=banned#bans"
    (b,) = client.bans.rows.values()
    assert b["network"] == f"{IP}/32" and timedelta(hours=23) < b["expires_at"] - datetime.now(timezone.utc) <= timedelta(days=1)
    assert submit(client, data_url(canvas_like())).status_code == 403
    admin_post(client, "/admin/bans/1/lift")
    assert submit(client, data_url(canvas_like())).status_code == 200


@pytest.mark.parametrize("network,key", [("not an ip", "badnet"), ("0.0.0.0/0", "widenet"),
                                         ("10.0.0.0/8", "widenet"), ("2001:db8::/16", "widenet")])
def test_admin_refuses_bad_or_huge_ranges(make, network, key):
    client, *_ = make(admin_verifier=AllowAll())
    r = admin_post(client, "/admin/bans", {"network": network, "duration": "1h", "scope": "all"})
    assert r.headers["location"] == f"/admin?n={key}#bans" and client.bans.rows == {}


@pytest.mark.parametrize("form", [
    {"duration": "forever"},
    {"duration": "custom", "custom_amount": "", "custom_unit": "days"},
    {"duration": "custom", "custom_amount": "nan", "custom_unit": "days"},
    {"duration": "custom", "custom_amount": "inf", "custom_unit": "days"},
    {"duration": "custom", "custom_amount": "-3", "custom_unit": "days"},
    {"duration": "custom", "custom_amount": "30", "custom_unit": "seconds"},
    {"duration": "custom", "custom_amount": "600", "custom_unit": "weeks"},         # over 10 years
    {"duration": "custom", "custom_amount": "1e300", "custom_unit": "days"},
])
def test_a_ban_always_has_a_sane_end(make, form):
    client, *_ = make(admin_verifier=AllowAll())
    r = admin_post(client, "/admin/bans", {"network": IP, "scope": "all", **form})
    assert r.headers["location"] == "/admin?n=badtime#bans" and client.bans.rows == {}


def test_custom_ban_durations(make):
    client, *_ = make(admin_verifier=AllowAll())
    r = admin_post(client, "/admin/bans", {"network": IP, "scope": "comment", "duration": "custom",
                                           "custom_amount": "90", "custom_unit": "minutes", "reason": "cool off"})
    assert r.headers["location"] == "/admin?n=banned#bans"
    (b,) = client.bans.rows.values()
    left = b["expires_at"] - datetime.now(timezone.utc)
    assert timedelta(minutes=89) < left <= timedelta(minutes=90) and b["reason"] == "cool off"
    (entry,) = client.modlog.rows
    assert entry.action == "ban (comment)" and entry.reason == "cool off" and entry.admin == "noodle@example.com"


# --- the Cloudflare Access token check ------------------------------------
TEAM, AUD = "noodle.cloudflareaccess.com", "aud-tag"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key=KEY, **over):
    now = int(time.time())
    claims = {"iss": f"https://{TEAM}", "aud": [AUD], "email": "Noodle@Example.com", "iat": now, "exp": now + 300,
              "sub": str(uuid.uuid4())}
    claims.update(over)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def verify(tok, emails=frozenset()):
    import asyncio
    v = AccessVerifier(TEAM, AUD, emails, signing_key=lambda _t: KEY.public_key())
    return asyncio.run(v({"Cf-Access-Jwt-Assertion": tok} if tok else {}))


def test_a_valid_token_gives_the_email():
    assert verify(token()) == "noodle@example.com"
    assert verify(token(), frozenset({"noodle@example.com"})) == "noodle@example.com"


@pytest.mark.parametrize("bad", [
    lambda: token(key=OTHER_KEY),                          # forged signature
    lambda: token(aud=["someone-else"]),                   # another Access app's token
    lambda: token(iss="https://evil.cloudflareaccess.com"),
    lambda: token(exp=int(time.time()) - 10),              # expired
    lambda: token(email=None),
    lambda: jwt.encode({"iss": f"https://{TEAM}", "aud": AUD, "email": "a@b"}, "secret", algorithm="HS256"),
    lambda: "",
])
def test_bad_tokens_are_refused(bad):
    with pytest.raises(AccessDenied):
        verify(bad())


def test_the_email_allowlist_applies():
    with pytest.raises(AccessDenied):
        verify(token(email="stranger@example.com"), frozenset({"noodle@example.com"}))


def test_a_standard_drawing_fills_the_frame_and_a_small_one_does_not(make):
    client, drawings, *_ = make()
    draw(client)
    assert drawings.sizes[1] == (500, 500)
    page = client.get("/dragon-gallery/image/1").text
    assert 'class="opening standard"' in page and 'style="width: 100%"' in page
    assert submit(client, data_url(canvas_like(250, 200))).status_code == 200
    page = client.get("/dragon-gallery/image/2").text
    assert 'class="opening"' in page and 'style="width: 50.0%"' in page


def test_a_banned_visitor_is_told_even_if_the_bot_field_is_filled(make):
    # D-0007 #4: a browser autofilling the hidden field mustn't hide the ban notice.
    client, *_ = make()
    draw(client)
    ban(client, IP + "/32", scope="comment")
    assert comment(client, 1, "hi", website="x").headers["location"].endswith("c=banned&b=1#comments")


def test_the_comment_cap_holds_without_content_length(make):
    # D-0007 #2: the cap is counted while reading, not taken from the header.
    client, *_ = make(comment_max_chars=20)
    draw(client)
    def chunks():
        yield b"body="
        for _ in range(50):
            yield b"x" * 100
    r = client.post("/dragon-gallery/image/1/comments", content=chunks(),
                    headers={"CF-Connecting-IP": IP, "Content-Type": "application/x-www-form-urlencoded"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("c=long#comments") and client.comments.rows == {}



# --- polish round (operator msgs 534/535) -----------------------------------
def test_comments_can_carry_a_name(make):
    client, *_ = make()
    draw(client)
    comment(client, 1, "hello", name="  Sir   Dragon\x07 ")
    comment(client, 1, "anon one")
    comment(client, 1, "long", name="x" * 100)
    page = client.get("/dragon-gallery/image/1").text
    assert "Sir Dragon &middot;" in page and "Anonymous &middot;" in page
    assert [c["name"] for c in client.comments.rows.values()] == ["Sir Dragon", None, "x" * 40]
    assert comment(client, 1, "x", name="<b>bold</b>").status_code == 303
    assert "<b>bold</b>" not in client.get("/dragon-gallery/image/1").text


def test_times_carry_iso_for_local_rendering(make):
    client, *_ = make()
    draw(client)
    comment(client, 1, "hi")
    page = client.get("/dragon-gallery/image/1").text
    assert page.count('<time datetime="') == 2 and 'data-kind="date"' in page and "toLocaleString" in page


def test_reasons_are_kept_and_logged(make):
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "rude")
    did = drawings.rows[1][0]
    admin_post(client, f"/admin/drawings/{did}/hide", {"reason": "not a dragon"})
    admin_post(client, "/admin/comments/1/hide", {"reason": "rude\nreally"})
    page = client.get("/admin", headers=ADMIN).text
    assert "hidden: not a dragon" in page and "hidden: rude really" in page
    admin_post(client, "/admin/comments/1/delete", {"reason": "gone"})
    admin_post(client, f"/admin/drawings/{did}/delete", {"reason": "spam"})
    assert [(m.action, m.target, m.reason) for m in client.modlog.rows] == [
        ("hide drawing", "#1", "not a dragon"), ("hide comment", "comment 1", "rude really"),
        ("delete comment", "comment 1", "gone"), ("delete drawing", "#1", "spam")]
    assert "spam" in client.get("/admin", headers=ADMIN).text           # the log outlives the drawing


def test_unhide_clears_the_reason(make):
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client)
    did = drawings.rows[1][0]
    admin_post(client, f"/admin/drawings/{did}/hide", {"reason": "oops"})
    admin_post(client, f"/admin/drawings/{did}/unhide", {"reason": "ignored"})
    assert drawings.reasons[1] == ""


def test_submit_reply_links_the_gallery(make):
    client, drawings, *_ = make(public_base_url="https://canvas.example")
    r = submit(client, data_url(canvas_like()))
    assert f"https://canvas.example/dragon-gallery/d/{drawings.rows[1][0]}" in r.text


def test_the_lot_sign_and_comments_sit_inside_the_column(make):
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    content = page.index('<div class="content">')
    assert content < page.index('<div class="lot"') < page.index('<div class="comments"')
    assert "background-size: 100% 100%" in page


# --- ban notices (operator msgs 537/539/540) --------------------------------
def admin_ban_row(client, kind, ref, ip=IP, **extra):
    data = {"network": ip, "scope": "all", "duration": "1d", "reason": "be nice", "subject_kind": kind,
            "subject_ref": str(ref), **extra}
    return admin_post(client, "/admin/bans", data)


def expire(client, ban_id):
    client.bans.rows[ban_id]["expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)


def test_a_ban_from_a_comment_row_tells_the_banned_visitor_why(make):
    client, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "you all stink")
    admin_ban_row(client, "comment", 1)
    admin_post(client, "/admin/comments/1/delete")                  # the copy outlives the comment
    r = comment(client, 1, "again")
    assert r.headers["location"] == "/dragon-gallery/image/1?c=banned&b=1#comments"
    page = client.get(r.headers["location"], headers={"CF-Connecting-IP": IP}).text
    assert "in a timeout until" in page and "Reason: be nice" in page and "you all stink" in page
    d = submit(client, data_url(canvas_like()))
    assert d.status_code == 403 and "Reason: be nice" in d.text and 'your comment: "you all stink"' in d.text


def test_a_ban_from_a_drawing_row_names_the_drawing_time(make):
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client)
    admin_ban_row(client, "drawing", drawings.rows[1][0])
    d = submit(client, data_url(canvas_like()))
    stamp = drawings.rows[1][2].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    assert d.status_code == 403 and f"your drawing sent {stamp} UTC" in d.text


def test_nobody_else_sees_the_reason(make):
    client, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "rude")
    admin_ban_row(client, "comment", 1)
    for headers in ({"CF-Connecting-IP": "198.51.100.1"}, {}):
        page = client.get("/dragon-gallery/image/1?c=banned&b=1", headers=headers).text
        assert "be nice" not in page and "rude</q>" not in page


def test_a_ban_that_ended_unseen_is_told_once_then_erased(make):
    client, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "rude")
    admin_ban_row(client, "comment", 1)
    expire(client, 1)
    r = comment(client, 1, "sorry")                                     # goes through, with the notice
    assert r.headers["location"] == "/dragon-gallery/image/1?c=posted&b=1#comments"
    assert [c["body"] for c in client.comments.rows.values()] == ["rude", "sorry"]
    page = client.get(r.headers["location"], headers={"CF-Connecting-IP": IP}).text
    assert "you were in a timeout until" in page and "Reason: be nice" in page
    assert client.bans.rows == {}                                       # acknowledged: erased
    again = client.get(r.headers["location"], headers={"CF-Connecting-IP": IP}).text
    notices = again[again.index('<div id="notices">'):again.index('<div id="comment-list">')]
    assert "timeout" not in notices


def test_a_drawing_after_an_unseen_ban_carries_the_notice_once(make):
    client, *_ = make(admin_verifier=AllowAll())
    admin_post(client, "/admin/bans", {"network": IP, "scope": "all", "duration": "1h", "reason": "spam"})
    expire(client, 1)
    first = submit(client, data_url(canvas_like()))
    assert first.status_code == 200 and "Heads up: You were in a timeout until" in first.text and "spam" in first.text
    assert client.bans.rows == {}
    assert "Heads up" not in submit(client, data_url(canvas_like())).text


def test_a_ban_seen_while_active_is_purged_after_it_ends(make):
    client, *_ = make(admin_verifier=AllowAll())
    admin_post(client, "/admin/bans", {"network": IP, "scope": "all", "duration": "1h", "reason": "spam"})
    assert submit(client, data_url(canvas_like())).status_code == 403   # told while active
    expire(client, 1)
    assert "Heads up" not in submit(client, data_url(canvas_like())).text
    client.get("/admin", headers=ADMIN)
    assert client.bans.rows == {}


def test_the_admin_sees_reason_and_subject(make):
    client, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "you all stink")
    admin_ban_row(client, "comment", 1)
    page = client.get("/admin", headers=ADMIN).text
    assert "be nice" in page and "comment: <q>you all stink</q>" in page


def test_a_forged_subject_is_ignored(make):
    client, *_ = make(admin_verifier=AllowAll())
    admin_ban_row(client, "comment", 999)
    admin_ban_row(client, "drawing", "not-a-uuid", ip="198.51.100.1")
    admin_ban_row(client, "<script>", 1, ip="198.51.100.2")
    assert [b["subject_kind"] for b in client.bans.rows.values()] == ["", "", ""]



# --- numbers are positions (operator msg 542) -------------------------------
def test_deleting_the_oldest_renumbers_the_rest(make):
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client, 3)
    first, third = drawings.rows[1][0], drawings.rows[3][0]
    admin_post(client, f"/admin/drawings/{first}/delete")
    r = submit(client, data_url(canvas_like()))
    assert "#3" in r.text                                              # the newest of three, not #4
    page = client.get("/dragon-gallery/image/2").text
    assert f"/images/{third}.png" in page and ">Lot No. 2<" in page
    assert client.get("/dragon-gallery", follow_redirects=False).headers["location"] == "/dragon-gallery/image/1"


def test_hidden_drawings_keep_their_slot_and_comments(make):
    # msg 557: the slot stays, the picture goes, the comments stay up, the time stays.
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client, 3)
    comment(client, 2, "lovely")
    hidden_id = drawings.rows[2][0]
    admin_post(client, f"/admin/drawings/{hidden_id}/hide")
    page = client.get("/dragon-gallery/image/2").text
    assert ">Lot No. 2<" in page and "This lot has been hidden" in page and "lovely" in page
    assert f"/images/{hidden_id}.png" not in page and str(hidden_id) not in page
    assert 'data-kind="date"' in page                                    # its date still shows
    assert client.get(f"/images/{hidden_id}.png").status_code == 404
    assert f"/images/{drawings.rows[3][0]}.png" in client.get("/dragon-gallery/image/3").text


def test_older_imports_slot_in_first(make):
    import asyncio
    client, drawings, *_ = make()
    draw(client)
    newest = drawings.rows[1][0]
    asyncio.run(drawings.add(uuid.uuid4(), None, datetime(2025, 10, 25, tzinfo=timezone.utc)))
    assert f"/images/{newest}.png" in client.get("/dragon-gallery/image/2").text


def test_the_permanent_link_follows_the_number(make):
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client, 2)
    second = drawings.rows[2][0]
    assert client.get(f"/dragon-gallery/d/{second}", follow_redirects=False).headers["location"] == "/dragon-gallery/image/2"
    admin_post(client, f"/admin/drawings/{drawings.rows[1][0]}/delete")
    assert client.get(f"/dragon-gallery/d/{second}", follow_redirects=False).headers["location"] == "/dragon-gallery/image/1"
    admin_post(client, f"/admin/drawings/{second}/hide")                # its slot remains
    assert client.get(f"/dragon-gallery/d/{second}", follow_redirects=False).headers["location"] == "/dragon-gallery/image/1"
    assert client.get(f"/dragon-gallery/d/{uuid.uuid4()}").status_code == 404


def test_sign_and_placeholders(make):
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    assert "wooden_sign_dark.png" in page and 'placeholder="Name (Anonymous)"' in page
    assert 'placeholder="Aristocratic critique here"' in page
    lot_css = page[page.index(".lot {"):page.index("}", page.index(".lot {"))]
    assert "color: gold" in lot_css



def test_admin_refusals_say_why_in_the_log(make, caplog):
    client, drawings, *_ = make(admin_verifier=AllowAll())
    draw(client)
    with caplog.at_level("WARNING", logger="dragonmail"):
        client.get("/admin", headers={"Cf-Access-Jwt-Assertion": "forged"})
        admin_post(client, f"/admin/drawings/{drawings.rows[1][0]}/hide",
                   headers={**ADMIN, "Origin": "https://elsewhere.example"})
    text = caplog.text
    assert "admin refused: nope" in text
    assert "admin refused: noodle@example.com posted from origin 'https://elsewhere.example'" in text



def test_the_banner_shrinks_on_narrow_screens(make):
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page
    assert ".title img { flex: 0 1 auto; min-width: 0;" in page and "clamp(" in page



# --- jump to a number (operator msg 550) -----------------------------------
def test_the_jump_box(make):
    client, *_ = make()
    draw(client, 3)
    page = client.get("/dragon-gallery/image/2").text
    assert 'action="/dragon-gallery/go"' in page and '<span id="total">3</span>' in page and 'max="3"' in page
    go = lambda n: client.get("/dragon-gallery/go", params={"n": n}, follow_redirects=False).headers["location"]
    assert go("3") == "/dragon-gallery/image/3"
    assert go(" #2 ") == "/dragon-gallery/image/2" and go("No. 1") == "/dragon-gallery/image/1"
    for bad in ("", "abc", "-1", "0", "1.5", "99999999999", "1;DROP"):
        assert go(bad) == "/dragon-gallery", bad
    assert client.get("/dragon-gallery/go", follow_redirects=False).headers["location"] == "/dragon-gallery"
    missing = client.get("/dragon-gallery/image/42")
    assert missing.status_code == 404 and 'action="/dragon-gallery/go"' in missing.text


def test_no_jump_box_when_empty(make):
    client, *_ = make()
    assert "/dragon-gallery/go" not in client.get("/dragon-gallery").text



def test_the_frame_is_sized_so_the_drawing_shows_unscaled(make):
    # Opening 701px of the 900px frame; at 900*500/701 px the opening is 500px (msg 551).
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    assert "width: calc(900px * 500 / 701);" in page and "width: calc(100% * 701 / 900);" in page
    assert 900 * 500 / 701 * 701 / 900 == 500



# --- flipping without a reload, first/last (operator msg 553) ---------------
def test_first_and_last_links(make):
    client, drawings, *_ = make()
    draw(client, 4)
    drawings.rows[4] = drawings.rows[4][:3] + (True,)                  # hidden, but still the last slot
    mid = client.get("/dragon-gallery/image/2").text
    assert 'id="nav-first" href="/dragon-gallery/image/1" data-n="1"' in mid
    assert 'id="nav-last" href="/dragon-gallery/image/4" data-n="4"' in mid
    first = client.get("/dragon-gallery/image/1").text
    assert '<a id="nav-first" class="off">' in first and '<a id="nav-prev" class="off">' in first
    last = client.get("/dragon-gallery/image/4").text
    assert '<a id="nav-last" class="off">' in last and '<a id="nav-next" class="off">' in last


def test_the_page_json_has_what_the_page_shows_and_no_more(make):
    client, drawings, *_ = make()
    draw(client, 3)
    comment(client, 2, "<b>hi</b>", name="Sir Dragon")
    j = client.get("/dragon-gallery/api/image/2").json()
    assert j["number"] == 2 and j["image"] == f"/images/{drawings.rows[2][0]}.png"
    assert (j["first"], j["prev"], j["next"], j["last"], j["total"]) == (1, 1, 3, 3, 3)
    assert j["standard"] is True and j["scale_pct"] == 100
    (c,) = j["comments"]
    assert c["name"] == "Sir Dragon" and c["body"] == "<b>hi</b>" and set(c) == {"hidden", "name", "body", "created_at", "when"}
    assert IP not in client.get("/dragon-gallery/api/image/2").text
    assert client.get("/dragon-gallery/api/image/9").status_code == 404
    drawings.rows[3] = drawings.rows[3][:3] + (True,)
    h = client.get("/dragon-gallery/api/image/3").json()
    assert h["hidden"] is True and h["image"] is None and str(drawings.rows[3][0]) not in str(h)


def test_the_flip_script_uses_text_not_html(make):
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    script = page[page.index("Flip through the gallery"):]
    assert "/dragon-gallery/api/image/" in script and "history.pushState" in script
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script and "outerHTML" not in script
    assert 'id="comment-list"' in page and 'id="comment-form"' in page and 'id="lot"' in page


def test_the_jump_box_lines_up_with_the_other_blocks(make):
    # The column's blocks get margin from ".container div"; the jump box is a <form> (msg 555).
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    assert ".container div {\n            margin: 5px;" in page
    assert ".jump { background-color: black; padding: 8px; color: gold; margin: 5px; }" in page



def test_hidden_comments_keep_their_slot_but_not_their_words(make):
    # msg 557: "This comment has been hidden", time kept, text and name withheld everywhere.
    client, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "first one")
    comment(client, 1, "secret rude words", name="Rude Person")
    comment(client, 1, "third one")
    admin_post(client, "/admin/comments/2/hide", {"reason": "zz-mod-reason-zz"})
    page = client.get("/dragon-gallery/image/1").text
    assert "secret rude words" not in page and "Rude Person" not in page and "zz-mod-reason-zz" not in page
    assert page.count('class="comment gone"') == 1 and "This comment has been hidden" in page
    assert page.index("first one") < page.index("This comment has been hidden") < page.index("third one")
    gone = page[page.index('class="comment gone"'):page.index("This comment has been hidden")]
    assert "<time" in gone                                               # the time stays
    j = client.get("/dragon-gallery/api/image/1").json()
    assert j["comments"][1] == {"hidden": True, "created_at": j["comments"][1]["created_at"],
                                "when": j["comments"][1]["when"]}
    assert "secret" not in str(j) and "Rude Person" not in str(j)



def test_a_hidden_lot_keeps_its_frame_with_a_grey_card(make):
    # msg 558: frame stays; grey card, grey blackletter text "This lot has been hidden".
    client, drawings, *_ = make()
    draw(client)
    drawings.rows[1] = drawings.rows[1][:3] + (True,)
    page = client.get("/dragon-gallery/image/1").text
    assert "Square-Gold-Frame-PNG-908289183.png" in page
    note_css = page[page.index(".hidden-note {"):page.index("}", page.index(".hidden-note {"))]
    assert "UnifrakturMaguntia" in note_css and "background: #9a9a9a" in note_css and "color: #555" in note_css
    assert '<span id="hidden-note" class="hidden-note">This lot has been hidden</span>' in page



def test_right_click_reaches_the_drawing_not_the_frame(make):
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    frame_css = page[page.index(".framed .frame {"):page.index("}", page.index(".framed .frame {"))]
    assert "pointer-events: none" in frame_css


# --- the gussied-up gallery (operator msg 573) -------------------------------
def test_decor_assets_are_served(make):
    client, *_ = make()
    for path in ("/Assets/carpet_tile.png", "/Assets/carpet_border.png", "/Assets/tassel.png", "/Assets/quartet.png",
                 "/Assets/spike_table.png", "/Assets/wine_cursor.png", "/sounds/hmmm.mp3",
                 "/sounds/FREEDOOM-COPYING.txt"):
        assert client.get(path).status_code == 200, path
    for season in ("spring", "summer", "autumn", "winter"):
        r = client.get(f"/music/{season}.mp3", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100, season          # seekable


def test_the_gallery_has_the_carpet_and_both_corners(make):
    client, *_ = make()
    draw(client)
    for page in (client.get("/dragon-gallery/image/1").text, client.get("/dragon-gallery/image/9").text):
        assert 'id="quartet"' in page and 'id="spike"' in page
        assert "carpet_tile.png" in page and "carpet_border.png" in page and "wine_cursor.png" in page
        assert page.count('class="tassel ') == 4
        assert '"spring", "summer", "autumn", "winter"' in page and "/sounds/hmmm.mp3" in page


def test_the_freedoom_notice_ships_with_the_sound():
    from pathlib import Path
    notice = Path("app/static/sounds/FREEDOOM-COPYING.txt").read_text()
    assert "Contributors to the Freedoom project" in notice and "Redistribution and use" in notice
    credits = Path("app/static/Assets/SOURCE.md").read_text()
    for name in ("quartet.png", "Freedoom", "Modena", "spike_table.png", "carpet_tile.png"):
        assert name in credits, name


def test_corner_images_show_at_clean_scales(make):
    # Desktop shows each corner at 1x or 2x of its image; narrow screens at half that.
    import re
    from PIL import Image
    client, *_ = make()
    page = client.get("/dragon-gallery").text
    for side, name in (("left", "quartet"), ("right", "spike_table")):
        w = Image.open(f"app/static/Assets/{name}.png").width
        desk, narrow = [int(x) for x in re.findall(rf"\.corner\.{side} img {{ width: (\d+)px; }}", page)]
        assert desk in (w, 2 * w) and narrow * 2 == desk, (name, w, desk, narrow)



# --- posting a comment without a reload (operator msg 609) -------------------
FETCH = {"CF-Connecting-IP": IP, "X-Requested-With": "fetch"}


def test_the_page_script_gets_json_not_a_redirect(make):
    client, *_ = make()
    draw(client)
    r = client.post("/dragon-gallery/image/1/comments", data={"body": "hi"}, headers=FETCH, follow_redirects=False)
    assert r.status_code == 200 and r.json() == {"c": "posted", "notice": "Thanks! Your comment is up.", "bad": False}
    assert [c["body"] for c in client.comments.rows.values()] == ["hi"]
    r = client.post("/dragon-gallery/image/1/comments", data={"body": "  "}, headers=FETCH)
    assert r.json()["c"] == "empty" and r.json()["bad"] is True
    plain = client.post("/dragon-gallery/image/1/comments", data={"body": "plain"},
                        headers={"CF-Connecting-IP": IP}, follow_redirects=False)
    assert plain.status_code == 303                                  # no script: the old way


def test_the_json_tells_the_banned_visitor_and_follows_the_told_rules(make):
    client, *_ = make(admin_verifier=AllowAll())
    draw(client)
    comment(client, 1, "you all stink")
    admin_ban_row(client, "comment", 1)
    r = client.post("/dragon-gallery/image/1/comments", data={"body": "again"}, headers=FETCH).json()
    assert r["c"] == "banned" and r["ban"]["reason"] == "be nice" and r["ban"]["subject_text"] == "you all stink"
    assert r["ban"]["ended"] is False and client.bans.rows[1]["told"] is not None
    expire(client, 1)
    client.bans.rows[1]["told"] = None                                # ran out before they came back
    r = client.post("/dragon-gallery/image/1/comments", data={"body": "sorry"}, headers=FETCH).json()
    assert r["c"] == "posted" and r["ban"]["ended"] is True and client.bans.rows == {}


# --- cache-busting asset URLs (operator msg 616) -----------------------------
def test_every_asset_url_on_the_gallery_is_versioned_and_served(make):
    import hashlib
    import re
    from pathlib import Path
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    refs = set(re.findall(r'/(?:Assets|sounds|music)/[A-Za-z0-9_.-]+\.(?:png|gif|jpg|mp3)(?:\?v=[0-9a-f]+)?', page))
    assert refs, "no asset references found"
    for ref in refs:
        path, _, v = ref.partition("?v=")
        assert v, f"unversioned: {ref}"
        assert v == hashlib.sha256(Path("app/static", path.lstrip("/")).read_bytes()).hexdigest()[:10], ref
        assert client.get(ref).status_code == 200, ref
    for season in ("spring", "summer", "autumn", "winter", "minuet"):
        assert any(r.startswith(f"/music/{season}.mp3?v=") for r in refs), season


def test_phone_corners_are_pinned_and_tuck_away_from_the_drawing(make):
    # msg 625: on narrow screens the corners stay pinned bottom-left/right, but hide while the
    # drawing is in their strip. (Behaviour checked in jsdom; here, the pieces are present.)
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    narrow = page[page.index("@media (max-width: 1260px)"):]
    narrow = narrow[:narrow.index("body.wine")]
    assert "position: static" not in narrow and ".corner.tucked { opacity: 0; visibility: hidden; pointer-events: none; }" in narrow
    assert "function placeCorners()" in page and "dragonPlaceCorners" in page



def test_the_frame_fits_inside_the_column_at_1to1(make):
    # msg 627: the carpet's trim and padding had squeezed the content to 648px, so the 642px
    # frame got capped to the 628px blocks (same width as everything, and scaled down again).
    import re
    client, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    col = page[page.index("        .container {\n            position: relative;"):]
    col = col[:col.index("}")]
    max_w = int(re.search(r"max-width: (\d+)px", col).group(1))
    border = int(re.search(r"border: (\d+)px solid", col).group(1))
    pad_x = int(re.search(r"padding: \d+px (\d+)px", col).group(1))
    content = max_w - 2 * border - 2 * pad_x
    blocks = content - 2 * 5 - 2 * 5                      # .content margin + each block's margin
    frame = 900 * 500 / 701
    assert content == 700 and blocks > frame, (content, blocks, frame)


def test_the_carpet_is_pre_scaled_nearest_neighbour():
    # msg 629: blurry on phones. The files carry the 8x nearest-neighbour scaling themselves:
    # every 8x8 block is one flat colour.
    from PIL import Image
    for name in ("carpet_tile", "carpet_border", "tassel"):
        im = Image.open(f"app/static/Assets/{name}.png").convert("RGBA")
        assert im.width % 8 == 0 and im.height % 8 == 0, name
        px = im.load()
        for by in range(0, im.height, 8):
            for bx in range(0, im.width, 8):
                c = px[bx, by]
                assert all(px[bx + dx, by + dy] == c for dx in range(8) for dy in range(8)), (name, bx, by)



def test_hovering_the_date_shows_the_time(make):
    # msg 635: the plaque's date carries the full time on hover (UTC without JS, local with it).
    import re
    client, drawings, *_ = make()
    draw(client)
    page = client.get("/dragon-gallery/image/1").text
    plaque = page[page.index('id="plaque"'):]
    plaque = plaque[:plaque.index("</time>")]
    when = drawings.rows[1][2].astimezone(timezone.utc)
    assert f'title="{when:%Y-%m-%d %H:%M:%S} UTC"' in plaque
    assert 'dateStyle: "full", timeStyle: "medium"' in page



# --- refresh without a reload (operator msg 639) ------------------------------
def test_the_refresh_link_is_on_drawing_pages(make):
    client, *_ = make()
    draw(client, 2)
    page = client.get("/dragon-gallery/image/2").text
    assert '<a id="refresh" class="refresh" href="/dragon-gallery/image/2"' in page
    assert "function refresh()" in page
    assert 'id="refresh"' not in client.get("/dragon-gallery/image/9").text      # nothing to refresh
