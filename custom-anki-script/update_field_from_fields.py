#!/usr/bin/env python3
# Python 3.9 compatible

import argparse
import os
import re
import subprocess
import requests
from typing import Any, Dict, List, Optional

ANKI_CONNECT_URL = "http://127.0.0.1:8765"

FIELD_X_DEFAULT = "Synonyms"
FIELD_Y_DEFAULT = "Synonyms IPA"   # kept for backward-compat, not used
FIELD_Z_DEFAULT = "Synonyms"


def anki_invoke(action: str, params: Optional[Dict[str, Any]] = None) -> Any:
    payload = {"action": action, "version": 6, "params": params or {}}
    r = requests.post(ANKI_CONNECT_URL, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["result"]


def chunked(lst: List[int], size: int) -> List[List[int]]:
    return [lst[i: i + size] for i in range(0, len(lst), size)]


def build_query(deck_root: Optional[str], note_type: Optional[str]) -> str:
    parts: List[str] = []
    if note_type:
        parts.append(f'note:"{note_type}"')
    if deck_root:
        parts.append(f'deck:"{deck_root}*"')
    return " ".join(parts) if parts else "deck:*"


# -------------------------
# IPA via system espeak
# -------------------------

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")  # ZWSP, ZWNJ, ZWJ, BOM


def _resolve_espeak_cmd() -> List[str]:
    preferred = "/opt/homebrew/bin/espeak"
    if os.path.isfile(preferred) and os.access(preferred, os.X_OK):
        return [preferred]
    return ["espeak"]


def _build_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    stable_data = "/opt/homebrew/share/espeak-ng-data"
    if os.path.isdir(stable_data):
        env.setdefault("ESPEAK_DATA_PATH", stable_data)
    return env


def clean_ipa(s: str, strip_zero_width: bool) -> str:
    s = s or ""
    if strip_zero_width:
        s = _ZERO_WIDTH_RE.sub("", s)
    s = " ".join(s.split())
    return s.strip()


def ipa_of_text(text: str, lang: str = "en-us", *, strip_zero_width: bool = False) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    voice_map = {"en-us": "en-us", "en-gb": "en-gb"}
    voice = voice_map.get(lang.lower(), lang)

    cmd = _resolve_espeak_cmd() + ["-q", f"-v{voice}", "--ipa=3", text]
    env = _build_env()

    try:
        out = subprocess.check_output(cmd, env=env, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"espeak failed: {e.output.strip()}") from e

    return clean_ipa(out, strip_zero_width=strip_zero_width)


def make_target_value(
    x: str,
    y: Optional[str],
    *,
    lang: str = "en-us",
    strip_zero_width: bool = False,
    ipa_cache: Optional[Dict[str, str]] = None
) -> str:
    x = (x or "").strip()
    if not x:
        return ""

    items = [p.strip() for p in x.split(",") if p.strip()]
    if not items:
        return ""

    cache = ipa_cache if ipa_cache is not None else {}
    ipa_items: List[str] = []

    for item in items:
        k = f"{lang.lower()}|{int(strip_zero_width)}|{item}"
        if k in cache:
            ipa = cache[k]
        else:
            ipa = ipa_of_text(item, lang=lang, strip_zero_width=strip_zero_width)
            cache[k] = ipa
        ipa_items.append(ipa)

    ipa_joined = ", ".join([i for i in ipa_items if i])
    return f"{x} ({ipa_joined})" if ipa_joined else x


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Set FIELD_Z = FIELD_X (IPA(item1), IPA(item2), ...). "
            "Items are split by comma in FIELD_X. Uses system espeak."
        )
    )

    ap.add_argument("--deck-root", default=None,
                    help="Deck root to match (includes subdecks). If omitted, all decks.")
    ap.add_argument("--note-type", default=None,
                    help="Note type name to match. If omitted, all note types.")

    ap.add_argument("--field-x", default=FIELD_X_DEFAULT)
    ap.add_argument("--field-y", default=FIELD_Y_DEFAULT,
                    help="Kept for compatibility; NOT used in IPA mode.")
    ap.add_argument("--field-z", default=FIELD_Z_DEFAULT)

    ap.add_argument("--lang", default="en-us",
                    help='espeak voice, e.g. "en-us" or "en-gb". Default: en-us')
    ap.add_argument("--strip-zero-width", action="store_true",
                    help="Remove zero-width chars from IPA output.")

    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-if-z-empty", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="If > 0, only process first N matched notes (useful for testing).")

    args = ap.parse_args()

    query = build_query(args.deck_root, args.note_type)
    print(f"[INFO] Query: {query}")
    print(f"[INFO] IPA voice: {args.lang} | strip_zero_width={args.strip_zero_width}")
    print(f"[INFO] X -> Z: {args.field_x} -> {args.field_z}  (Z = X (IPA(items)))")

    note_ids: List[int] = anki_invoke("findNotes", {"query": query})
    print(f"[INFO] Notes matched: {len(note_ids)}")
    if not note_ids:
        return

    if args.limit and args.limit > 0:
        note_ids = note_ids[:args.limit]
        print(f"[INFO] Limiting to first {len(note_ids)} notes")

    BATCH = 200
    ipa_cache: Dict[str, str] = {}
    planned_updates: List[Dict[str, Any]] = []

    for batch in chunked(note_ids, BATCH):
        infos = anki_invoke("notesInfo", {"notes": batch})
        for info in infos:
            nid = info["noteId"]
            fields = info.get("fields", {})

            x = (fields.get(args.field_x, {}).get("value") or "").strip()
            y = (fields.get(args.field_y, {}).get("value") or "").strip()
            z = (fields.get(args.field_z, {}).get("value") or "").strip()

            if args.only_if_z_empty and z:
                continue
            if not x:
                continue

            new_z = make_target_value(
                x, y,
                lang=args.lang,
                strip_zero_width=args.strip_zero_width,
                ipa_cache=ipa_cache
            )

            if new_z and new_z != z:
                planned_updates.append({
                    "id": nid,
                    "fields": {args.field_z: new_z}
                })

    print(f"[INFO] Notes to update: {len(planned_updates)}")

    if args.dry_run:
        print("[DRY-RUN] Showing first 10 updates:")
        for u in planned_updates[:10]:
            print(u)
        return

    if not planned_updates:
        print("[OK] Nothing to update.")
        return

    # Use AnkiConnect action: updateNoteFields (supported widely)
    # For speed, send in multi batches.
    MULTI_BATCH = 50
    for sub in chunked(planned_updates, MULTI_BATCH):
        actions = [
            {"action": "updateNoteFields", "params": {"note": u}}
            for u in sub
        ]
        anki_invoke("multi", {"actions": actions})

    print("[OK] Updated successfully.")


if __name__ == "__main__":
    main()
