#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys
import urllib.request
from typing import Any, Dict, List, Optional

ANKI_CONNECT_URL = "http://127.0.0.1:8765"

# -------- CONFIG --------
SOURCE_SEGMENT = "::word2mean"
TARGET_SEGMENT = "::exercise"

# Exercise is the 3rd card type => usually ord=2 (0-based)
TARGET_ORD = 2

# Safety: only move cards currently in this deck. Set None to disable.
ONLY_MOVE_IF_CURRENT_DECK_IS = "Default"

# Restrict which source decks to consider by prefix. Set "" to disable.
DECK_PREFIX_FILTER = "1. VOCABULARY::02. 30 Chủ đề (full)"

CARDSINFO_BATCH = 200
# ------------------------


def anki_invoke(action: str, params: Optional[Dict[str, Any]] = None) -> Any:
    payload = {"action": action, "version": 6, "params": params or {}}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ANKI_CONNECT_URL, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(
            "Cannot reach AnkiConnect at {0}. Is Anki open and AnkiConnect installed?\n{1}".format(
                ANKI_CONNECT_URL, e
            )
        )
    if out.get("error") is not None:
        raise RuntimeError("AnkiConnect error on {0}: {1}".format(action, out["error"]))
    return out.get("result")


def deck_names() -> List[str]:
    return anki_invoke("deckNames")


def create_deck(deck: str) -> None:
    anki_invoke("createDeck", {"deck": deck})


def find_notes(query: str) -> List[int]:
    return anki_invoke("findNotes", {"query": query})


def notes_info(note_ids: List[int]) -> List[Dict[str, Any]]:
    return anki_invoke("notesInfo", {"notes": note_ids})


def cards_info(card_ids: List[int]) -> List[Dict[str, Any]]:
    return anki_invoke("cardsInfo", {"cards": card_ids})


def change_deck(card_ids: List[int], deck: str) -> None:
    anki_invoke("changeDeck", {"cards": card_ids, "deck": deck})


def chunks(xs: List[int], n: int) -> List[List[int]]:
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def is_source_deck(name: str, prefix: str) -> bool:
    if SOURCE_SEGMENT not in name:
        return False
    if prefix and not name.startswith(prefix):
        return False
    return True


def map_deck(src: str) -> str:
    return src.replace(SOURCE_SEGMENT, TARGET_SEGMENT, 1)


def get_note_ids_in_deck(deck: str) -> List[int]:
    q = 'deck:"{0}"'.format(deck)
    return find_notes(q)


def get_all_card_ids_from_notes(note_ids: List[int]) -> List[int]:
    if not note_ids:
        return []
    infos = notes_info(note_ids)
    out: List[int] = []
    for n in infos:
        out.extend(n.get("cards", []))
    return out


def collect_exercise_cards_by_ord(
    card_ids: List[int],
    exercise_ord: int,
    only_if_deck: Optional[str],
) -> List[int]:
    if not card_ids:
        return []

    selected: List[int] = []
    for batch in chunks(card_ids, CARDSINFO_BATCH):
        infos = cards_info(batch)
        for c in infos:
            # ord is 0-based card template index
            if c.get("ord") != exercise_ord:
                continue
            curr_deck = c.get("deckName", "")
            if only_if_deck is not None and curr_deck != only_if_deck:
                continue
            selected.append(c["cardId"])
    return selected


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Only print planned moves.")
    ap.add_argument("--prefix", type=str, default=None, help="Override DECK_PREFIX_FILTER.")
    ap.add_argument("--move-from", type=str, default=None, help="Override safety deck; 'ANY' disables.")
    ap.add_argument("--ord", type=int, default=None, help="Override TARGET_ORD (0-based).")
    args = ap.parse_args()

    prefix = DECK_PREFIX_FILTER if args.prefix is None else args.prefix

    only_from = ONLY_MOVE_IF_CURRENT_DECK_IS
    if args.move_from is not None:
        only_from = None if args.move_from.upper() == "ANY" else args.move_from

    exercise_ord = TARGET_ORD if args.ord is None else args.ord

    anki_invoke("version")

    decks = deck_names()
    src_decks = [d for d in decks if is_source_deck(d, prefix)]

    print("[INFO] Source decks found: {0}".format(len(src_decks)))
    if prefix:
        print("[INFO] Prefix filter: {0}".format(prefix))
    print("[INFO] Mapping segment: {0} -> {1}".format(SOURCE_SEGMENT, TARGET_SEGMENT))
    print("[INFO] Exercise ord (0-based): {0}".format(exercise_ord))
    if only_from is not None:
        print("[INFO] Safety: only moving cards currently in deck '{0}'".format(only_from))
    else:
        print("[WARN] Safety disabled: moving matching ord cards regardless of current deck.")

    total_planned = 0
    total_moved = 0

    for src in sorted(src_decks):
        tgt = map_deck(src)

        note_ids = get_note_ids_in_deck(src)
        if not note_ids:
            continue

        card_ids = get_all_card_ids_from_notes(note_ids)
        if not card_ids:
            continue

        ex_card_ids = collect_exercise_cards_by_ord(card_ids, exercise_ord, only_from)
        if not ex_card_ids:
            continue

        total_planned += len(ex_card_ids)

        if args.dry_run:
            print("[DRY] move={0} | {1} -> {2}".format(len(ex_card_ids), src, tgt))
            continue

        create_deck(tgt)
        change_deck(ex_card_ids, tgt)
        total_moved += len(ex_card_ids)
        print("[MOVE] moved={0} | {1} -> {2}".format(len(ex_card_ids), src, tgt))

    if args.dry_run:
        print("[DRY DONE] Planned total moves: {0} cards".format(total_planned))
    else:
        print("[DONE] Moved total: {0} cards".format(total_moved))

    return 0


if __name__ == "__main__":
    sys.exit(main())
