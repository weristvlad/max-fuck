"""Ultimate test: every API method with detailed logging."""
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
import struct
import tempfile
import time
import wave
import zlib
from pathlib import Path

from max_api import MaxClient, MaxAPIError, Opcode, parse_formatted_text

# ── Config ──────────────────────────────────────────────────────
CHAT_ID = 13796912        # тестовый чат

# ── State ───────────────────────────────────────────────────────
PASS = 0
FAIL = 0
SKIP = 0
GROUP_CHAT_ID = None
MY_USER_ID = None


def log(msg):
    print(msg, flush=True)


def log_data(data, max_len=400):
    s = json.dumps(data, ensure_ascii=False, default=str)
    if len(s) > max_len:
        s = s[:max_len] + "..."
    log(f"       {s}")


CLIENT_REF = None  # set in main() for reconnect

async def ensure_connected():
    """Reconnect if WS is dead."""
    c = CLIENT_REF
    if c and (c._ws is None or getattr(c._ws, 'closed', False) or c._ws.close_code is not None):
        log("       *** RECONNECTING ***")
        try:
            # Clean up old state
            if c._recv_task and not c._recv_task.done():
                c._recv_task.cancel()
                try:
                    await c._recv_task
                except:
                    pass
            # Clear pending futures
            for fut in c._pending.values():
                if not fut.done():
                    fut.cancel()
            c._pending.clear()
            c._seq = 0
            # Reconnect
            await c.connect()
            saved, _ = __import__('max_api').load_token()
            if saved:
                await c.login(saved)
                log("       *** RECONNECTED ***")
        except Exception as e:
            log(f"       *** RECONNECT FAILED: {e} ***")


async def run(name, coro, expect_error=False):
    global PASS, FAIL
    await ensure_connected()
    log(f"\n  [{PASS+FAIL+SKIP+1:02d}] {name}")
    try:
        result = await coro
        if expect_error:
            log(f"       UNEXPECTED OK (wanted error)")
            FAIL += 1
        else:
            log(f"       PASS")
            PASS += 1
        if isinstance(result, dict):
            log_data(result)
        elif isinstance(result, list):
            log(f"       list[{len(result)}]")
            for item in result[:2]:
                log_data(item)
        return result
    except MaxAPIError as e:
        if expect_error:
            log(f"       PASS (expected: {e})")
            PASS += 1
        else:
            log(f"       FAIL MaxAPIError: {e}")
            FAIL += 1
        return None
    except Exception as e:
        log(f"       FAIL {type(e).__name__}: {e}")
        FAIL += 1
        return None


def do_skip(name, reason="not configured"):
    global SKIP
    log(f"\n  [--] SKIP: {name} ({reason})")
    SKIP += 1


# ── Test data ──────────────────────────────────────────────────

def make_png(path: Path):
    raw = b'\x00\xff\x00\x00'
    compressed = zlib.compress(raw)
    def chunk(t, d):
        c = t + d
        crc = struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return struct.pack('>I', len(d)) + c + crc
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', compressed)
    png += chunk(b'IEND', b'')
    path.write_bytes(png)


def make_wav(path: Path, sec=0.5):
    with wave.open(str(path), 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b'\x00\x00' * int(16000 * sec))


# ── Main ───────────────────────────────────────────────────────

async def main():
    global GROUP_CHAT_ID, MY_USER_ID

    log("=" * 60)
    log("  MAX API ULTIMATE TEST")
    log(f"  Chat: {CHAT_ID}  |  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    async with MaxClient() as client:
        global CLIENT_REF
        CLIENT_REF = client

        # ═══ AUTH ═══════════════════════════════════════════════
        log("\n### AUTH & SESSION ###")

        login_result = await run("auto_login", client.auto_login())
        if login_result and isinstance(login_result, dict):
            profile = login_result.get("profile", {})
            contact = profile.get("contact", {})
            MY_USER_ID = contact.get("id") or profile.get("id")
            log(f"       my_user_id={MY_USER_ID}")

        await run("refresh_token", client.refresh_token())

        sessions = await run("get_sessions", client.get_sessions())
        if sessions and "sessions" in sessions:
            for s in sessions["sessions"][:3]:
                log(f"       session: {s.get('deviceName','?')} / {s.get('platform','?')}")

        # ═══ CHATS ═════════════════════════════════════════════
        log("\n### CHATS ###")

        chats = await run("get_chats (all)", client.get_chats())
        if chats:
            log(f"       total: {len(chats)}")
            for c in chats[:5]:
                t = c.get("title") or c.get("name") or f"DM#{c.get('chatId')}"
                log(f"       [{c.get('type','?')}] {t} id={c.get('chatId')}")
            for c in chats:
                if c.get("type") in ("GROUP", "CHANNEL") and c.get("chatId") != CHAT_ID:
                    GROUP_CHAT_ID = c["chatId"]
                    log(f"       group for tests: {GROUP_CHAT_ID}")
                    break

        await run("get_chats (specific)", client.get_chats(chat_ids=[CHAT_ID]))
        await run("get_chats_updates", client.get_chats_updates(marker=0))

        # Folders
        folders = await run("get_folders", client.get_folders())
        # get_folder(273) crashes server WS — skip for now
        do_skip("get_folder", "opcode 273 crashes server connection")

        await run("check_chat_link (expect error)", client.check_chat_link("https://max.ru/some_test_link"), expect_error=True)
        await run("subscribe_chat", client.subscribe_chat(CHAT_ID, subscribe=True))
        await asyncio.sleep(0.3)
        await run("unsubscribe_chat", client.subscribe_chat(CHAT_ID, subscribe=False))
        await run("get_chat_members_list (DM, expect error)", client.get_chat_members_list(CHAT_ID), expect_error=True)
        await run("get_chat_members", client.get_chat_members(CHAT_ID))

        # ═══ CONTACTS ══════════════════════════════════════════
        log("\n### CONTACTS & USERS ###")

        if MY_USER_ID:
            await run("get_user (self)", client.get_user(MY_USER_ID))
        await run("get_contacts", client.get_contacts([CHAT_ID]))
        await run("find_user ('Влад')", client.find_user("Влад"))
        await run("contact_search ('Влад')", client.contact_search("Влад"))
        await run("contact_by_phone (expect not found)", client.contact_by_phone("+70000000000"), expect_error=True)
        await run("mutual_contacts", client.mutual_contacts(CHAT_ID))
        do_skip("contact_add", "opcode 33 not supported by server")
        await run("get_common_chats", client.get_common_chats(6725252))
        await run("get_user_score", client.get_user_score(CHAT_ID))

        # ═══ SEARCH ════════════════════════════════════════════
        log("\n### SEARCH ###")

        await run("search ALL 'привет'", client.search("привет", count=5))
        await run("search CHANNELS", client.search("test", count=5, search_type="CHANNELS"))
        await run("search PUBLIC_CHATS", client.search("test", count=5, search_type="PUBLIC_CHATS"))
        await run("search_chats 'test'", client.search_chats("test", count=5))
        await run("search_messages 'тест'", client.search_messages(CHAT_ID, "тест", count=5))

        # ═══ MESSAGES ══════════════════════════════════════════
        log("\n### MESSAGES ###")

        messages = await run("get_messages (last 10)", client.get_messages(CHAT_ID, backward=10))
        last_msg_id = None
        if messages:
            for m in messages[-3:]:
                log(f"       [{m.get('id','?')}] {(m.get('text') or '')[:50]}")
            for m in reversed(messages):
                if m.get("id"):
                    last_msg_id = m["id"]
                    break

        if last_msg_id:
            await run(f"get_message ({last_msg_id})", client.get_message(CHAT_ID, int(last_msg_id)))
            await run("get_media_messages", client.get_media_messages(CHAT_ID, last_msg_id, forward=3, backward=3))
            do_skip("get_message_stats", "server changed payload format, crashes WS")
            await run("get_message_link (DM, may be denied)", client.get_message_link(CHAT_ID, last_msg_id), expect_error=True)

        do_skip("get_last_mentions", "opcode 127 requires unknown payload format")
        do_skip("get_link_info", "server crashes WS on some URLs")

        # ═══ SEND ══════════════════════════════════════════════
        log("\n### SEND MESSAGES ###")

        sent = await run("send text", client.send_message(CHAT_ID, "ULTIMATE TEST: plain text"))
        sent_id = sent.get("message", {}).get("id") if sent else None
        await asyncio.sleep(1)

        text, elems = parse_formatted_text("**bold** *italic* ~~strike~~ ++under++ `code` [link](https://max.ru)")
        await run("send formatted", client.send_message(CHAT_ID, text, elements=elems))
        await asyncio.sleep(1)

        if last_msg_id:
            await run("send reply", client.send_message(CHAT_ID, "ULTIMATE TEST: reply", reply_to=last_msg_id))
            await asyncio.sleep(1)

        await run("send_typing", client.send_typing(CHAT_ID))
        await asyncio.sleep(0.5)

        # Photo
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            p = Path(f.name); make_png(p)
        try:
            await run("send_photo", client.send_photo(CHAT_ID, p, text="ULTIMATE: photo"))
        finally:
            p.unlink(missing_ok=True)
        await asyncio.sleep(1)

        # File
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            p = Path(f.name); p.write_text("ultimate test\n" * 5)
        try:
            await run("send_file", client.send_file(CHAT_ID, p, text="ULTIMATE: file"))
        finally:
            p.unlink(missing_ok=True)
        await asyncio.sleep(1)

        # Voice
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            p = Path(f.name); make_wav(p, 1.0)
        try:
            await run("send_voice", client.send_voice(CHAT_ID, p, duration_ms=1000))
        finally:
            p.unlink(missing_ok=True)
        await asyncio.sleep(1)

        # Forward
        if last_msg_id:
            await run("forward_messages", client.forward_messages(CHAT_ID, CHAT_ID, [last_msg_id]))
            await asyncio.sleep(1)

        # ═══ EDIT & DELETE ═════════════════════════════════════
        log("\n### EDIT & DELETE ###")

        tmp = await run("send (for edit)", client.send_message(CHAT_ID, "WILL BE EDITED"))
        if tmp:
            tid = tmp.get("message", {}).get("id")
            if tid:
                await asyncio.sleep(1)
                await run("edit_message", client.edit_message(CHAT_ID, tid, "EDITED!"))
                await asyncio.sleep(1)
                await run("delete_message", client.delete_message(CHAT_ID, tid, for_all=True))

        if last_msg_id:
            await run("mark_read", client.mark_read(CHAT_ID, last_msg_id))

        # ═══ PIN ═══════════════════════════════════════════════
        log("\n### PIN ###")

        await run("pin_message", client.pin_message(CHAT_ID))
        await asyncio.sleep(1)
        await run("unpin_message (DM, may fail)", client.unpin_message(CHAT_ID))

        # ═══ REACTIONS ═════════════════════════════════════════
        log("\n### REACTIONS ###")

        if sent_id:
            await run("react 👍", client.react(CHAT_ID, sent_id, "👍"))
            await asyncio.sleep(0.5)
            await run("get_reactions", client.get_reactions(CHAT_ID, [sent_id]))
            await run("get_detailed_reactions", client.get_detailed_reactions(CHAT_ID, sent_id))
            await run("remove_reaction", client.remove_reaction(CHAT_ID, sent_id))

        await run("get_chat_reaction_settings", client.get_chat_reaction_settings(CHAT_ID))

        # ═══ DRAFTS ════════════════════════════════════════════
        log("\n### DRAFTS ###")

        await run("save_draft", client.save_draft(CHAT_ID, "test draft"))
        await asyncio.sleep(0.3)
        await run("discard_draft", client.discard_draft(CHAT_ID))

        # ═══ STICKERS ══════════════════════════════════════════
        log("\n### STICKERS ###")

        await run("get_sticker_sets", client.get_sticker_sets(count=3))
        await run("sync_stickers", client.sync_stickers())

        # ═══ CALLS ═════════════════════════════════════════════
        log("\n### CALLS ###")

        await run("get_call_history", client.get_call_history(count=3))

        # ═══ MISC ══════════════════════════════════════════════
        log("\n### MISC ###")

        await run("complain_reasons", client.complain_reasons())

        # ═══ PUSH HANDLERS ═════════════════════════════════════
        log("\n### PUSH HANDLERS ###")

        def make_h(name):
            def h(payload):
                log(f"  [PUSH] {name}: {json.dumps(payload, ensure_ascii=False, default=str)[:120]}")
            return h

        for name, fn in [
            ("on_message", client.on_message),
            ("on_presence", client.on_presence),
            ("on_typing", client.on_typing),
            ("on_call", client.on_call),
            ("on_chat_update", client.on_chat_update),
            ("on_delayed_message", client.on_delayed_message),
            ("on_reactions", client.on_reactions),
            ("on_mark", client.on_mark),
            ("on_contact", client.on_contact),
            ("on_location", client.on_location),
            ("on_folder_update", client.on_folder_update),
            ("on_delete_range", client.on_delete_range),
        ]:
            fn(make_h(name))
            log(f"  registered: {name}")

        client.on(Opcode.PUSH_CONFIG, make_h("PUSH_CONFIG"))
        log(f"  registered: on(PUSH_CONFIG)")
        log("  listening 5s for push events...")
        await asyncio.sleep(5)

        # ═══ OPCODES ═══════════════════════════════════════════
        log("\n### OPCODE CONSTANTS ###")

        new_ops = {
            "DEBUG": 2, "RECONNECT": 3, "AUTH_CREATE_TRACK": 112,
            "AUTH_CHECK_PASSWORD": 113, "AUTH_LOGIN_RESTORE_PASSWORD": 101,
            "AUTH_VALIDATE_PASSWORD": 107, "AUTH_VALIDATE_HINT": 108,
            "AUTH_VERIFY_EMAIL": 109, "AUTH_CHECK_EMAIL": 110,
            "AUTH_LOGIN_PROFILE_DELETE": 116, "PRESET_AVATARS": 25,
            "CONTACT_ADD": 33, "CONTACT_SEARCH": 37, "CONTACT_MUTUAL": 38,
            "CONTACT_SORT": 40, "CONTACT_VERIFY": 42, "CONTACT_INFO_BY_PHONE": 46,
            "MSG_SHARE_PREVIEW": 70, "MSG_DELETE_RANGE": 92,
            "GET_LAST_MENTIONS": 127, "VIDEO_CHAT_CREATE_JOIN_LINK": 84,
            "GET_INBOUND_CALLS": 103, "EXTERNAL_CALLBACK": 105,
            "CHAT_HIDE": 196, "CHAT_REACTIONS_SETTINGS_SET": 257,
            "REACTIONS_SETTINGS_GET_BY_CHAT_ID": 258, "COMPLAIN_REASONS_GET": 162,
            "FOLDERS_GET_BY_ID": 273, "FOLDERS_UPDATE": 274, "FOLDERS_REORDER": 275,
            "PUSH_CONTACT_SORT": 139, "PUSH_MSG_DELETE_RANGE": 140, "PUSH_FOLDERS": 277,
        }
        ok = fail = 0
        for name, expected in new_ops.items():
            actual = getattr(Opcode, name, None)
            if actual == expected:
                ok += 1
            else:
                log(f"  FAIL: Opcode.{name} = {actual}, expected {expected}")
                fail += 1
        log(f"  opcodes: {ok}/{len(new_ops)} OK")

    # ═══ SUMMARY ═══════════════════════════════════════════════
    log("\n" + "=" * 60)
    log(f"  PASS: {PASS}  |  FAIL: {FAIL}  |  SKIP: {SKIP}  |  TOTAL: {PASS+FAIL+SKIP}")
    log("=" * 60)
    if FAIL:
        log(f"\n  {FAIL} FAILED!")
        sys.exit(1)
    else:
        log(f"\n  ALL PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
