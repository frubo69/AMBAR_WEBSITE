# Support Chat — Integration Handoff

> **Who this is for:** you are Claude, working on a different Telegram Mini App / bot
> project. Your job is to integrate the support-chat system described here into that
> project using **exactly the same technology**, adapted to its codebase — and to
> decide **where the support entry points belong** in that app (see the final section,
> it is part of your task, not an afterthought).
>
> The owner will hand you the reference source files from the AMBAR project:
> `index-6.html` (frontend, single file), `api_server.py`, `support_bot.py`, `db.py`.
> This document explains how those pieces fit so you can port them faithfully.

---

## 1. What this system is

An **in-app support chat** for a Telegram Mini App, with the operator side living in
plain Telegram — no admin web UI, no third-party helpdesk:

- The **customer** chats inside the Mini App (a full-screen chat overlay: text +
  photos, RU/EN, per-order or general context).
- Every customer message is **forwarded to the support admins as a normal Telegram
  message** (via a dedicated support bot), prefixed with a customer info card.
- An admin answers by simply **replying to that forwarded message in Telegram**
  (native swipe-to-reply). The reply is written back into the conversation in the
  database, appears in the customer's in-app chat within seconds, and the customer
  gets a "new message from support" push from the main bot.
- Conversations are **threaded per context**: one thread per order, plus one
  "general" thread per user.

Three processes are involved:

| Process | Role |
|---|---|
| **API server** (aiohttp) | Serves the Mini App endpoints: send message, send image, fetch conversation. Forwards customer messages to admins. |
| **Support bot** (python-telegram-bot, polling) | Catches admin replies in Telegram and routes them back into the right conversation. Also handles users who DM the support bot directly. |
| **Main customer bot** (token only, no process needed) | Used to push "💬 New message from support" notifications to the customer. |

---

## 2. Data model (MongoDB, async via Motor)

Three collections. Shapes are exact — copy them.

### `support_messages` — the conversations
One document per conversation, messages embedded:

```js
{
  conv_key: "686932322_AMB1713977",   // "{telegram_uid}_{order_id}" or just "{telegram_uid}" / "{uid}_general"
  messages: [
    { role: "client",   type: "text",  text: "Здравствуйте! Заказ едет?", ts: "2026-07-08T18:02:00+00:00" },
    { role: "operator", type: "text",  text: "Да, курьер выехал",          ts: "2026-07-08T18:05:11+00:00" },
    { role: "client",   type: "photo", url: "/uploads/support/ab12cd34ef56.jpg", caption: "", ts: "…" }
  ]
}
```
- `conv_key` has a **unique index**.
- Appends are one atomic op: `update_one({conv_key}, {$push: {messages: msg}}, upsert=True)`.
- `ts` is an ISO-8601 UTC string and doubles as the message id (used for
  incremental fetch and reply-threading).

### `support_map` — forwarded message → conversation
Lets an admin's *native Telegram reply* find its conversation:

```js
{ fwd_msg_id: "1234",  user_id: 686932322, conv_key: "686932322_AMB1713977", order_id: "AMB1713977" }
```
- `fwd_msg_id` (string!) has a unique index. Written every time a customer message is
  forwarded to an admin chat.

### `support_fwd_ids` — message ts → forwarded msg id per admin
Enables two-directional threading (a customer replying to a specific operator
message renders as a native reply in the admin's Telegram, and vice versa):

```js
{ conv_key: "…", ts: "<message ts>", op_id: 7443111111, fwd_msg_id: 5678 }
```

The `db.py` helper functions to copy: `get_support_conv`, `append_support_msg`,
`save_support_map_entry`, `get_support_map_entry`, `save_support_fwd_id`,
`get_support_fwd_id` (and the index creation in `connect()`).

---

## 3. Backend endpoints (aiohttp, in the API server)

All three authenticate with Telegram WebApp `initData` (`Authorization: tma <initData>`
header), validated server-side with the **main bot's token** (standard HMAC check).

### `POST /api/support/send` — customer sends text
Body: `{ text, order_id?, reply_to_ts?, lang? }`

1. Validate auth → `uid`. Compose `conv_key = f"{uid}_{order_id}"` if an order
   context was passed, else `str(uid)`.
2. Append `{role:"client", type:"text", text, ts}` to `support_messages`.
3. Build the **admin header** (Markdown):
   ```
   📦 Контекст: заказ #AMB… | общий вопрос
   👤 Name (@username, ID: `123`)
   ⚠️ status tags (banned / not verified / …)   ← optional but very useful
   💬 <the text>
   ```
4. Send it to every admin id via the **support bot token**. Attach:
   - a **URL button** deep-linking into the operator bot's order card when the
     message has an order context (`https://t.me/<op_bot>?start=order_<id>`) —
     adapt or drop if your project has no operator bot;
   - `reply_parameters` pointing at the admin-side message being replied to, when
     the customer replied to a specific operator message (`reply_to_ts` →
     `get_support_fwd_id`).
5. For each successful forward, save `support_map` + `support_fwd_ids` rows.
6. (Optional, AMBAR-specific) complaint keyword scan + owner-app notifications.

### `POST /api/support/send-image` — customer sends a photo
`multipart/form-data`: fields `image` (file), `initData`, `order_id?`, `caption?`.
- Validate, stream to disk under `uploads/support/<uuid12>.jpg` (cap size ~8 MB),
  append `{role:"client", type:"photo", url:"/uploads/support/…", caption, ts}`,
  then forward to admins via `sendPhoto` with the same header/threading treatment.
- The API server must **serve `/uploads/…` statically** (with a path-traversal
  guard: resolve and require the path to stay inside the uploads dir).

### `GET /api/support/messages?conv_key=…&after=…` — the app fetches/polls
- Auth required; **authorization check: `conv_key` must start with the caller's own
  uid** (users can only read their own threads).
- Returns `{messages: [...]}`; with `after=<ts>` returns only newer ones
  (incremental polling).

**Hard-won details, do not skip:**
- Give every outbound Telegram HTTP call a **hard timeout (~20 s)** — a stalled
  call otherwise hangs the whole request chain silently.
- When the Telegram API answers `ok:false`, **log the full response** — silent
  drops are the #1 way messages vanish (bad Markdown/HTML entities, blocked bot,
  wrong chat id).
- Escape user-generated text if you send with `parse_mode` (a stray `<` or `_`
  kills the whole message). AMBAR sends the admin header in Markdown with raw
  user names — it survived, but HTML mode requires `html.escape()` on
  names/addresses/captions.

---

## 4. The support bot process (`support_bot.py`)

A tiny standalone polling bot (python-telegram-bot v20+, its own token, its own
systemd unit). Copy it nearly verbatim. Handlers, in registration order:

1. `CommandHandler("start")` — greets users (RU/EN by `language_code`); tells admins
   "reply to forwarded messages to answer".
2. `MessageHandler(filters.REPLY & filters.ALL, handle_admin_reply)` — **registered
   FIRST** so admin replies are consumed before the generic handler:
   - Only acts if `from_user.id in ADMIN_IDS` and the message is a reply.
   - Resolve the replied-to message id → conversation: first an in-memory map
     (direct-DM users), then `support_map` in Mongo (mini-app users).
   - **Mini-app conversation:** append `{role:"operator", …}` to
     `support_messages` (photos: download via `get_file()` to `uploads/support/`,
     store the URL); save `support_fwd_ids` so the customer can reply-thread; then
     notify the customer via the **main bot** HTTP API: *"💬 Новое сообщение от
     поддержки (по заказу #X). Откройте приложение…"*.
   - **Direct-DM conversation:** just `msg.copy(chat_id=user_id)`.
3. `MessageHandler(~COMMAND, handle_user_message)` — users who DM the support bot
   directly: confirm receipt (RU/EN), then forward to every admin as **two
   messages**: an info card (name, @username, id, language, ban status) followed by
   a `msg.forward()`, and remember `forwarded.message_id → user_id`.
4. Dedup guard: keep a set of processed `update_id`s (polling can re-deliver).

Env: `SUPPORT_BOT_TOKEN` (this bot), `BOT_TOKEN` (main bot, for user pushes),
`ADMIN_IDS` (comma-separated Telegram ids).

---

## 5. Frontend chat UI (inside the Mini App)

In AMBAR it's all in one HTML file; the parts to port:

**Support options sheet** (`openSupportOptions()`): a bottom sheet with two rows —
📞 **Call** (a `tel:` number) and 💬 **Send message** → opens the chat with the
`'general'` context. This is the generic entry point.

**Chat overlay** (`.support-overlay`, `openSupportChat(orderId)`):
- Full-screen overlay sliding up from the bottom (`transform: translateY(100%) → 0`),
  header with context ("Поддержка · заказ #X" / "Support"), back button to the
  options sheet, message list, input row (auto-growing textarea + 📎 photo button +
  send button).
- On open: sets `_supportConvKey = uid + '_' + orderId` (or `uid` for general),
  fetches the whole thread, renders bubbles (client right / operator left, photos
  as thumbnails, timestamps), scrolls to bottom, then **polls
  `GET /api/support/messages?after=<last ts>` every 3 s** while open.
- Sending is **optimistic**: the bubble renders immediately, then the POST runs;
  on failure mark it visually. Photos: client-side downscale/JPEG-compress before
  upload (keeps uploads snappy on mobile), thumbnail bubble immediately.
- **Background poll** (`_bgSupportPoll`, every ~30 s while the app is open, chat
  closed): checks recent-order threads + general for messages newer than a
  per-thread "last seen" cache and shows an unread dot on support buttons + an
  in-app toast/notification row. Keep this — it's what makes replies feel live
  without real push.
- i18n: every label exists in RU and EN, chosen by the app language.

**Entry points in AMBAR today** (for reference): header support icon (options
sheet), profile → "Поддержка" row, a "Support" button on every order card in the
orders list (opens that order's thread), an action on the active-order bottom
sheet, and in-app notification rows that deep-link to the order thread
(`n.action === 'support'`).

---

## 6. Integration checklist for your project

1. **Create a support bot** with @BotFather; put `SUPPORT_BOT_TOKEN` + `ADMIN_IDS`
   in the environment (never in code — AMBAR keeps all tokens in a git-ignored
   `.env` on the server only).
2. Copy the three `db.py` collections + helpers (or translate to your storage —
   the shapes above are the contract).
3. Mount the three endpoints in your API process; wire your initData validation.
4. Deploy `support_bot.py` as its own service (systemd unit, polling).
5. Port the chat overlay + options sheet into your frontend; wire your API base,
   auth, and language strings. Keep: 3 s foreground poll, incremental `after=`,
   optimistic sends, image compression, background unread poll.
6. Serve `/uploads/support/` statically with the traversal guard.
7. Adapt the admin-header deep-link button to whatever "open the related object"
   means in your project (or drop it).
8. Test the full loop both ways: app → admin Telegram; admin reply → app bubble +
   main-bot push; photo both directions; two contexts (general + object-bound);
   an admin replying to an OLD message (threading); a banned user (info card flag).

---

## 7. YOUR TASK: place the support entry points thoughtfully

Do not just drop one button somewhere. Study the host app's screens and flows,
then **think through every situation in which a user genuinely reaches for
support**, and make sure there's a natural entry point *in that moment*, with the
right conversation context pre-bound. Situations to reason about (adapt to what
the app actually does):

- **Something they're waiting for is late or stuck** — an order in progress, a
  payment pending confirmation, a delivery ETA blown. → contextual button on the
  live status card, opening the thread *for that object*.
- **Money problems** — payment failed, paid twice, wrong amount, refund questions.
  → entry point on/after the payment screens; consider auto-binding the
  payment/order id into the context.
- **Something arrived wrong** — wrong/missing item, quality complaint. → entry on
  the completed-order/history card (post-delivery is when complaints happen).
- **They're blocked** — verification pending/declined, account restricted, can't
  proceed. → offer support right inside the blocking screen, not hidden away.
- **The app itself failed** — an error state, an empty screen, a request that
  keeps failing. → error/empty states should offer "contact support" instead of a
  dead end.
- **Pre-purchase doubts** — questions before they commit (availability, delivery
  area, how something works). → one *always-findable* generic entry: header icon
  and/or profile row, opening the general thread.
- **Cancellations/changes** — mid-flow "I need to change/cancel this" moments.

Principles: one **persistent** generic entry (header or profile — discoverable in
under 3 seconds from anywhere), plus **contextual** entries exactly where friction
happens, each pre-binding the relevant `conv_key` context so the admin instantly
sees what the user is talking about. Don't over-plaster: a support button on every
screen trains users to ignore it. Present your placement plan (screen, trigger,
context passed, why) before implementing.
