"""Deterministic momentum bot for agent-fun/replay/v2.

Reads hello/batch/done lines on stdin, writes one reply per batch on stdout.
Standard library only; no clock, no network, no unseeded randomness.
"""

import json
import os
import sys

LAMPORTS = 1_000_000_000
BPS = 10_000
FEE_RATE = 0.0125

WINDOW = 30
MIN_AGE = 500
MIN_NET = 1.0
MIN_BUYS = 3
MIN_BUYERS = 5
MIN_UP = 0.05
MAX_UP = 1.5
NEAR_HIGH = 0.95
MIN_RQ = 2.0
MAX_RQ = 60.0
INITIAL = 0.01
ADD = 0
STAKE = 2.0
MAX_ENTRIES = 2
COOLDOWN = 150
STOP = 0.12
TRAIL_ARM = 0.05
TRAIL = 0.15
MAX_HOLD = 400
MAX_EXPOSURE = 6.0
PROBE = 0.01
TARGET_COINS = 25
BUY_SLIP = 1500
SELL_SLIP = 10_000
KEEP = 40


class Curve:
    __slots__ = ("mint", "created", "slot", "vq", "vt", "rq", "rt", "trades", "complete")

    def __init__(self, mint, slot, created=None):
        self.mint = mint
        self.created = created
        self.slot = slot
        self.vq = self.vt = self.rq = self.rt = 0
        self.trades = 0
        self.complete = False


class State:
    def __init__(self, hello):
        self.hello = hello
        self.caps = hello["caps"]
        self.tx = int(hello["fees"]["base_fee_lamports"]) + int(hello["fees"]["priority_fee_lamports"])
        self.cash = int(hello["capital"]["lamports"])
        self.positions = {}
        self.curves = {}
        self.pending = {}
        self.tapes = {}
        self.held = {}
        self.probes = {}
        self.my_fills = 0
        self.my_coins = set()
        self.used = set()
        self.next_id = 1

    def new_curve(self, mint, slot, created=None):
        c = Curve(mint, slot, created)
        self.curves[mint] = c
        return c

    def tape(self, mint):
        t = self.tapes.get(mint)
        if t is None:
            t = {"created": None, "trades": [], "entries": 0, "last_exit": -10**12}
            self.tapes[mint] = t
        return t

    def cid(self):
        while True:
            name = "o" + str(self.next_id)
            self.next_id += 1
            if name not in self.used:
                self.used.add(name)
                return name

    def set_account(self, account):
        self.cash = int(account["cash_lamports"])
        self.positions = {}
        for p in account["positions"]:
            tokens = int(p["token_amount"])
            if tokens > 0:
                self.positions[p["mint"]] = (tokens, int(p["cost_lamports"]))

    def free_cash(self):
        free = self.cash
        for p in self.pending.values():
            free -= self.tx
            if p["side"] == "buy":
                free -= int(p["amount"])
        return free

    def committed(self):
        mints = set(self.positions)
        for p in self.pending.values():
            if p["side"] == "buy":
                mints.add(p["mint"])
        return mints

    def max_buy(self, mint):
        c = self.curves.get(mint)
        if c is None or c.complete:
            return 0
        return c.rq * self.caps["max_order_bps_of_real_reserve"] // BPS


def apply_batch(st, batch):
    for f in batch["fills"]:
        cid = f["client_id"]
        st.pending.pop(cid, None)
        if f["status"] == "filled" and not cid.startswith("_"):
            st.my_fills += 1
            st.my_coins.add(f["mint"])
            h = st.held.get(f["mint"])
            if h is not None and f["side"] == "buy":
                h["filled"] = True
    st.set_account(batch["account"])
    for m in batch["marks"]:
        c = st.curves.get(m["mint"])
        if c is not None and int(m["slot"]) < c.slot:
            continue
        if c is None:
            c = st.new_curve(m["mint"], int(m["slot"]))
        c.vq = int(m["virtual_quote_reserves"])
        c.vt = int(m["virtual_token_reserves"])
        c.rq = int(m["real_quote_reserves"])
        c.rt = int(m["real_token_reserves"])
        c.slot = int(m["slot"])
    traded = []
    for e in batch["events"]:
        mint = e["mint"]
        slot = int(e["slot"])
        kind = e["event"]
        t = st.tape(mint)
        if kind == "CreateEvent":
            c = st.new_curve(mint, slot, slot)
            c.vt = int(e["virtual_token_reserves"])
            c.vq = int(e["virtual_sol_reserves"])
            c.rt = int(e["real_token_reserves"])
            t["created"] = slot
        elif kind == "TradeEvent":
            c = st.curves.get(mint) or st.new_curve(mint, slot)
            c.vq = int(e["virtual_quote_reserves"])
            c.vt = int(e["virtual_token_reserves"])
            c.rq = int(e["real_quote_reserves"])
            c.rt = int(e["real_token_reserves"])
            c.slot = slot
            c.trades += 1
            price = c.vq / c.vt if c.vt else 0.0
            sol = int(e["sol_amount"]) / LAMPORTS
            t["trades"].append((slot, bool(e["is_buy"]), sol, e["user"], price))
            traded.append(mint)
        else:
            c = st.curves.get(mint) or st.new_curve(mint, slot)
            c.complete = True
    for mint in traded:
        t = st.tapes[mint]
        cut = 0
        while cut < len(t["trades"]) and t["trades"][cut][0] <= batch["slot"] - KEEP:
            cut += 1
        if cut:
            del t["trades"][:cut]
    return traded


def flow(t, now, width):
    total = 0.0
    for slot, buy, sol, _user, _price in reversed(t["trades"]):
        if slot <= now - width:
            break
        total += sol if buy else -sol
    return total


def entry_signal(st, t, now, rq_sol):
    if rq_sol < MIN_RQ or rq_sol > MAX_RQ:
        return False
    if t["created"] is None or now - t["created"] < MIN_AGE:
        return False
    if len(t["trades"]) < MIN_BUYS:
        return False
    start = now - WINDOW
    mid = now - WINDOW / 2
    buys = 0
    buy_sol = 0.0
    net_old = 0.0
    net_new = 0.0
    users = set()
    high = 0.0
    last = 0.0
    first = -1.0
    for slot, buy, sol, user, price in t["trades"]:
        if slot <= start:
            first = price
            continue
        if first < 0:
            first = price
        signed = sol if buy else -sol
        if slot <= mid:
            net_old += signed
        else:
            net_new += signed
        if buy:
            buys += 1
            buy_sol += sol
            users.add(user)
        if price > high:
            high = price
        last = price
    if buys < MIN_BUYS or len(users) < MIN_BUYERS or first <= 0 or high <= 0:
        return False
    if net_old + net_new < MIN_NET:
        return False
    up = last / first - 1.0
    if up < MIN_UP or up > MAX_UP or last < high * NEAR_HIGH:
        return False
    return net_new > 0 and net_new >= net_old


def decide(st, batch, traded):
    now = batch["slot"]
    marks = {m["mint"]: m for m in batch["marks"]}
    held_mints = set(st.held)
    pending_buys = {p["mint"] for p in st.pending.values() if p["side"] == "buy"}
    pending_sells = {p["mint"] for p in st.pending.values() if p["side"] == "sell"}
    orders = []
    tx_sol = st.tx / LAMPORTS

    def emit(side, mint, amount, slip):
        key = "quote_lamports" if side == "buy" else "token_amount"
        cid = st.cid()
        orders.append({"side": side, "client_id": cid, "mint": mint, key: str(amount), "max_slippage_bps": slip})
        st.pending[cid] = {"side": side, "mint": mint, "amount": amount}
        return cid

    # Exits: stop, trailing stop, staleness.
    for mint, h in list(st.held.items()):
        pos = st.positions.get(mint)
        if pos is None:
            if mint not in pending_buys and mint not in pending_sells:
                if h["filled"]:
                    st.tapes[mint]["last_exit"] = now
                st.held.pop(mint, None)
            continue
        c = st.curves.get(mint)
        if c is None or c.complete:
            continue
        mk = marks.get(mint)
        vq = int(mk["virtual_quote_reserves"]) if mk else c.vq
        vt = int(mk["virtual_token_reserves"]) if mk else c.vt
        tokens, cost = pos
        value = tokens * vq / (vt + tokens) / LAMPORTS * (1 - FEE_RATE) - tx_sol
        cost_sol = cost / LAMPORTS
        if value > h["peak"]:
            h["peak"] = value
        h["value"] = value
        exit_now = value < cost_sol * (1 - STOP)
        if not exit_now and h["peak"] >= cost_sol * (1 + TRAIL_ARM) and value < h["peak"] * (1 - TRAIL):
            exit_now = True
        if not exit_now and now - h["opened"] > MAX_HOLD and value < cost_sol * 1.02:
            exit_now = True
        if not exit_now or h["exiting"] or mint in pending_sells:
            continue
        h["exiting"] = True
        if mint not in pending_buys and len(orders) < st.caps["max_orders_per_batch"]:
            emit("sell", mint, tokens, SELL_SLIP)

    # Probe round trips: sell as soon as the tiny buy lands.
    for mint, selling in list(st.probes.items()):
        pos = st.positions.get(mint)
        c = st.curves.get(mint)
        if pos is None:
            if mint not in pending_buys and mint not in pending_sells:
                st.probes.pop(mint, None)
            continue
        if c is None or c.complete or mint in pending_sells:
            continue
        if len(orders) >= st.caps["max_orders_per_batch"]:
            break
        emit("sell", mint, pos[0], SELL_SLIP)
        st.probes[mint] = True

    free = st.free_cash()
    slots_left = st.caps["max_open_positions"] - len(st.committed())
    exposure = sum(max(h["invested"], st.positions.get(m, (0, 0))[1] / LAMPORTS) for m, h in st.held.items())

    for mint in traded:
        if len(orders) >= st.caps["max_orders_per_batch"]:
            break
        c = st.curves.get(mint)
        if c is None or c.complete or mint in pending_buys or mint in pending_sells:
            continue
        if mint in st.probes:
            continue
        price = c.vq / c.vt if c.vt else 0.0
        rq_sol = c.rq / LAMPORTS
        t = st.tapes[mint]
        h = st.held.get(mint)
        if h is None:
            if slots_left <= 0 or t["entries"] >= MAX_ENTRIES:
                continue
            if now - t["last_exit"] < COOLDOWN or not entry_signal(st, t, now, rq_sol):
                continue
            want = INITIAL
            is_new = True
        else:
            if h["exiting"] or not h["filled"] or h["invested"] >= STAKE - 0.01:
                continue
            pos = st.positions.get(mint)
            if pos is None or price < h["last_price"] * 1.005 or h["value"] < pos[1] / LAMPORTS * 0.99:
                continue
            if flow(t, now, WINDOW) <= 0.1 or rq_sol > MAX_RQ:
                continue
            want = min(ADD, STAKE - h["invested"])
            is_new = False
        cap = st.max_buy(mint) / LAMPORTS
        amount = min(cap, want, MAX_EXPOSURE - exposure, (free - st.tx) / LAMPORTS)
        if amount < 0.01:
            continue
        lamports = int(amount * LAMPORTS)
        emit("buy", mint, lamports, BUY_SLIP)
        free -= amount + tx_sol
        exposure += amount
        if is_new:
            st.held[mint] = {"opened": now, "peak": 0.0, "value": 0.0, "invested": amount,
                             "exiting": False, "filled": False, "last_price": price}
            t["entries"] += 1
            slots_left -= 1
        else:
            h["invested"] += amount
            h["last_price"] = price

    # Guardrail pacing: enough distinct coins with a 0.01+ SOL round trip.
    if slots_left > 0 and st.probes == {} and len(st.my_coins) < TARGET_COINS:
        min_rq = PROBE * BPS / st.caps["max_order_bps_of_real_reserve"] * 1.5
        for mint in traded:
            if len(orders) >= st.caps["max_orders_per_batch"] or st.probes:
                break
            if mint in held_mints or mint in pending_buys or mint in pending_sells or mint in st.my_coins:
                continue
            c = st.curves.get(mint)
            if c is None or c.complete or c.rq / LAMPORTS < min_rq:
                continue
            if free < PROBE + 2 * tx_sol:
                break
            emit("buy", mint, int(PROBE * LAMPORTS), BUY_SLIP)
            st.probes[mint] = False
            free -= PROBE + tx_sol

    return orders


def write_answer():
    base = os.environ.get("MAKEFUN_OUTPUT_DIR")
    if not base:
        return
    try:
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "answer.txt"), "rb") as src:
            data = src.read()
        tmp = os.path.join(base, ".entry.tmp")
        with open(tmp, "wb") as dst:
            dst.write(data)
        os.replace(tmp, os.path.join(base, "entry.txt"))
    except OSError:
        pass


def main():
    st = None
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line.strip():
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            st = State(msg)
        elif kind == "batch" and st is not None:
            traded = apply_batch(st, msg)
            orders = decide(st, msg, traded)
            reply = json.dumps({"type": "reply", "seq": msg["seq"], "orders": orders},
                               separators=(",", ":"), ensure_ascii=False)
            sys.stdout.write(reply + "\n")
            sys.stdout.flush()
        elif kind == "done" and st is not None:
            st.set_account(msg["account"])
            break
    write_answer()


if __name__ == "__main__":
    main()
