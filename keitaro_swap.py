# -*- coding: utf-8 -*-
"""
keitaro_swap.py — массовая замена лендингов и офферов в кампаниях Keitaro.

Сценарий:
  1. Поиск по названию и выбор лендингов (можно пропустить — тогда ленды не трогаем).
  2. Поиск по названию и выбор офферов (можно пропустить — тогда офферы не трогаем).
  3. Отбор кампаний по вхождению подстроки (ID кабинета) в название кампании в КТ.
  4. Показ плана БЫЛО → СТАНЕТ по каждому потоку, подтверждение, запись.

Замена идёт в потоках (streams) со схемой "landings": массив landings/offers потока
полностью заменяется на выбранные, доли делятся поровну, state=active.
Потоки-редиректы и экшены не трогаются.

Настройка: переменные окружения KEITARO_URL и KEITARO_API_KEY,
либо впиши значения в константы ниже.
API-ключ: Keitaro → Настройки → API.

Запуск:
  python keitaro_swap.py          # боевой режим (с подтверждением)
  python keitaro_swap.py --dry    # dry-run: показать план, ничего не менять
"""

import json
import os
import ssl
import sys
import urllib.request
import urllib.error

# ──────────────────────────── настройки ────────────────────────────
KEITARO_URL = os.environ.get("KEITARO_URL", "").rstrip("/")      # например https://tracker.example.com
KEITARO_API_KEY = os.environ.get("KEITARO_API_KEY", "")
VERIFY_SSL = True   # False, если трекер на самоподписанном сертификате

DRY_RUN = "--dry" in sys.argv

# ──────────────────────────── HTTP-обвязка ─────────────────────────
_ssl_ctx = ssl.create_default_context()
if not VERIFY_SSL:
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE


def api(method, path, body=None):
    """Запрос к Admin API Keitaro. path — например '/offers' или '/streams/15'."""
    url = f"{KEITARO_URL}/admin_api/v1{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Api-Key", KEITARO_API_KEY)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        print(f"\n[ОШИБКА] {method} {path} → HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
        raise SystemExit(1)
    except urllib.error.URLError as e:
        print(f"\n[ОШИБКА] нет связи с трекером: {e.reason}")
        raise SystemExit(1)


# ──────────────────────────── утилиты CLI ──────────────────────────
def ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nПрервано.")
        raise SystemExit(0)


def pick_items(items, kind):
    """Поиск по названию и выбор нескольких элементов. items: [{id, name}].
    Возвращает список выбранных или [] (пропуск)."""
    while True:
        q = ask(f"\nПоиск {kind} по названию (Enter — пропустить замену {kind}): ")
        if not q:
            return []
        found = [i for i in items if q.lower() in (i.get("name") or "").lower()]
        if not found:
            print("  Ничего не найдено, попробуй другую подстроку.")
            continue
        found = found[:50]
        for n, it in enumerate(found, 1):
            print(f"  {n:>3}. [{it['id']}] {it['name']}")
        raw = ask("Номера через запятую (a — все найденные, Enter — искать заново): ")
        if not raw:
            continue
        if raw.lower() in ("a", "all", "все"):
            return found
        try:
            idx = [int(x) for x in raw.replace(" ", "").split(",") if x]
            sel = [found[i - 1] for i in idx if 1 <= i <= len(found)]
        except (ValueError, IndexError):
            print("  Не понял номера, заново.")
            continue
        if sel:
            return sel


def shares(n):
    """Поровну на n элементов, остаток — первому. Сумма всегда 100."""
    base = 100 // n
    rest = 100 - base * n
    return [base + rest if i == 0 else base for i in range(n)]


def names(items):
    return ", ".join(f"[{i['id']}] {i['name']}" for i in items) or "—"


# ──────────────────────────── основной сценарий ────────────────────
def main():
    global KEITARO_URL, KEITARO_API_KEY
    if not KEITARO_URL:
        KEITARO_URL = ask("URL трекера (https://...): ").rstrip("/")
    if not KEITARO_API_KEY:
        KEITARO_API_KEY = ask("API-ключ: ")

    print("\nЗагружаю справочники из КТ…")
    landings = [l for l in api("GET", "/landing_pages") if l.get("state") != "deleted"]
    offers = [o for o in api("GET", "/offers") if o.get("state") != "deleted"]
    campaigns = [c for c in api("GET", "/campaigns") if c.get("state") != "deleted"]
    print(f"  Лендингов: {len(landings)} · офферов: {len(offers)} · кампаний: {len(campaigns)}")

    # 1–2. выбор лендингов и офферов
    new_landings = pick_items(landings, "ЛЕНДИНГОВ")
    new_offers = pick_items(offers, "ОФФЕРОВ")
    if not new_landings and not new_offers:
        print("Не выбрано ни лендингов, ни офферов — менять нечего.")
        return
    print(f"\nК замене:\n  ленды:  {names(new_landings)}\n  офферы: {names(new_offers)}")

    # 3. отбор кампаний по вхождению ID кабинета в название
    while True:
        raw = ask("\nID кабинета(ов) / подстроки в названии кампании КТ, через запятую: ")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            continue
        matched = [c for c in campaigns if any(k in (c.get("name") or "") for k in keys)]
        if not matched:
            print("  Ни одна кампания не подошла.")
            continue
        print(f"\nНайдено кампаний: {len(matched)}")
        for n, c in enumerate(matched, 1):
            print(f"  {n:>3}. [{c['id']}] {c['name']} (state={c.get('state')})")
        raw = ask("Номера через запятую (a — все, Enter — искать заново): ")
        if not raw:
            continue
        if raw.lower() in ("a", "all", "все"):
            targets = matched
        else:
            try:
                idx = [int(x) for x in raw.replace(" ", "").split(",") if x]
                targets = [matched[i - 1] for i in idx if 1 <= i <= len(matched)]
            except (ValueError, IndexError):
                print("  Не понял номера, заново.")
                continue
        if targets:
            break

    # 4. план по потокам
    land_payload = [
        {"landing_id": l["id"], "share": s, "state": "active"}
        for l, s in zip(new_landings, shares(len(new_landings)))
    ] if new_landings else None
    offer_payload = [
        {"offer_id": o["id"], "share": s, "state": "active"}
        for o, s in zip(new_offers, shares(len(new_offers)))
    ] if new_offers else None

    land_by_id = {l["id"]: l.get("name", "?") for l in landings}
    offer_by_id = {o["id"]: o.get("name", "?") for o in offers}
    plan = []  # (campaign, stream, old_land_str, old_offer_str)

    print("\n──── ПЛАН ────")
    for c in targets:
        streams = api("GET", f"/streams?campaign_id={c['id']}")
        flows = [s for s in streams if s.get("schema") == "landings" and s.get("state") != "deleted"]
        if not flows:
            print(f"[{c['id']}] {c['name']} — нет потоков со схемой landings, пропуск")
            continue
        print(f"[{c['id']}] {c['name']}")
        for s in flows:
            old_l = ", ".join(land_by_id.get(x.get("landing_id"), str(x.get("landing_id")))
                              for x in (s.get("landings") or [])) or "—"
            old_o = ", ".join(offer_by_id.get(x.get("offer_id"), str(x.get("offer_id")))
                              for x in (s.get("offers") or [])) or "—"
            print(f"   поток [{s['id']}] {s.get('name', '')}")
            if land_payload is not None:
                print(f"      ленды:  {old_l}  →  {names(new_landings)}")
            if offer_payload is not None:
                print(f"      офферы: {old_o}  →  {names(new_offers)}")
            plan.append((c, s))

    if not plan:
        print("Менять нечего.")
        return
    if DRY_RUN:
        print(f"\nDRY-RUN: изменений не внесено. Потоков к правке: {len(plan)}.")
        return

    if ask(f"\nПрименить к {len(plan)} поток(ам)? (yes/no): ").lower() not in ("y", "yes", "да"):
        print("Отменено, ничего не изменено.")
        return

    # 5. запись
    ok, fail = 0, 0
    for c, s in plan:
        body = {}
        if land_payload is not None:
            body["landings"] = land_payload
        if offer_payload is not None:
            body["offers"] = offer_payload
        try:
            api("PUT", f"/streams/{s['id']}", body)
            ok += 1
            print(f"  OK: кампания [{c['id']}] поток [{s['id']}]")
        except SystemExit:
            fail += 1
            print(f"  FAIL: кампания [{c['id']}] поток [{s['id']}] — смотри ошибку выше")

    print(f"\nГотово: обновлено потоков {ok}, ошибок {fail}.")


if __name__ == "__main__":
    main()
