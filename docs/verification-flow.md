# Verification and delayed delivery

Both repositories can be upgraded independently. BetterForward passes optional
`bot_username` to the unchanged create endpoint. Older TGuard ignores this field;
the local **Check verification** button and message-triggered status check still
work. New TGuard leaves requests without this field unchanged. No webhook or
background polling is introduced.

TGuard includes a validated `return_bot` parameter in the verification URL. It
only builds a fixed `https://t.me/<username>?start=tg_<token>` URL for external
sessions. This navigation is a hint, never proof of verification. Telegram may
require pressing Start; the manual check button remains available. Telegram
group approval retains its existing behavior.

BetterForward validates token ownership against local pending state and checks
TGuard's completed/expired fields. Query requests are limited to one per user
per 3 seconds; create requests to one per minute, including failures. The
server's expires_at is honored with a maximum local retention of one hour;
missing expiry in older responses falls back to 600 seconds.

The first message is retained for every captcha method. During button, emoji
and TGuard challenges, subsequent business messages are retained too. Math,
Q&A and sticker answers are never queued. Commands are never queued. A normal
message confirming completion remains a business message and is processed
after the queued messages. Button/emoji chatter does not replace the challenge.

Delivery snapshots are RAM-only, bounded to 1,000 users, 5 messages or album
groups per user and 20 total messages per user. Deadlines are not extended by
new messages. Albums count as a group for the limit; delivery follows the
existing per-message pipeline. Snapshots include content needed by existing
permission, spam and auto-response checks; none of it is written to disk by
the replay buffer. Restart, TTL expiry or capacity eviction discards snapshots.
Use CAPTCHA_REPLAY=disable in the process environment to disable buffering;
the stored setting captcha_replay, if present, takes precedence.

After verification, snapshots re-enter the normal checks. Originals are copied
through Telegram and message mappings/statistics are recorded as usual. Missing
originals are skipped without resurrecting cached content. Edited pending
messages update their snapshots. Verification, callbacks and forwarding are
serialized per lock stripe; the buffer is claimed once before replay. Transient
copy failures are not automatically retried, avoiding ambiguous duplicate sends.
This is not a durable delivery queue or an exactly-once guarantee across crashes.

Manual release validation should cover Telegram Android, iOS and Desktop with
both mixed-version combinations, return links, Start confirmation, early close,
album limits, deleted originals and a bot blocked before confirmation.
