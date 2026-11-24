"""Pump.fun Bot Sprint: Make Bot And Win.

A paper-trading bot for the agent-fun/replay/v2 harness. It reads the
harness stream (hello, batch, done) as newline-delimited JSON on stdin
and answers every batch with one reply line on stdout.

Idea: fresh-launch momentum snipe. New coins are watched from their
CreateEvent; the bot only buys in the first few slots, only when the
early tape looks organic (several distinct buyers, creator still
holding, buys clearly beating sells, price already drifting up), and
then rides with a take-profit, a stop-loss, a trailing stop and a
hard time limit. It never re-enters a coin it exited, never shorts,
never uses leverage, and never touches the network or the filesystem.
Standard library only; fully deterministic.
"""

import json
import sys

LAMPORTS_PER_SOL = 1_000_000_000

# --- tuning knobs (all in SOL unless noted) -------------------------
ENTRY_WINDOW_SLOTS = 3        # buy only this soon after creation
STAKE_SOL = 0.10              # target size of one first buy
MIN_REAL_SOL = 1.0            # ignore curves thinner than this
MAX_REAL_SOL = 30.0           # ignore curves thicker than this
MAX_DEV_BUY_SOL = 1.0         # creator's own launch buy must stay under
MIN_BUYERS = 1                # distinct non-creator buyers required
MAX_BUYERS = 2                # ... and no more than this (crowded = bad)
MIN_TRADES = 2                # trades seen before an entry
MIN_NET_FLOW_SOL = 0.005      # others' buys minus sells, at least this
MAX_SELL_SHARE = 0.10         # others' sells as a share of their buys
MAX_SINGLE_SELL_SHARE = 0.25  # one sell as a share of real SOL reserves
MIN_RUN_PCT = -0.05           # price may dip a little, no run-up needed
MAX_RUN_PCT = 2.00            # ... but not more than this above create
TAKE_PROFIT_PCT = 0.25        # sell whole position at +25% over cost
STOP_LOSS_PCT = 0.05          # sell whole position at -5% under cost
TRAIL_ARM_PCT = 0.10          # trailing stop arms once value is +10%
TRAIL_GIVEBACK_PCT = 0.08     # ... then exits on an 8% fall from peak
MAX_HOLD_SLOTS = 20           # give up after this many slots
STALE_SLOTS = 60              # exit when no trade for this long
MAX_DRAWDOWN_PCT = 0.20       # stop new entries past this equity dip
BUY_SLIPPAGE_BPS = 200
SELL_SLIPPAGE_BPS = 200


def to_sol(lamports):
    """Lamports (int, or a decimal string) as a float number of SOL."""
    return int(lamports) / LAMPORTS_PER_SOL


def curve_price(virtual_quote, virtual_token):
    """SOL per token from a bonding curve's virtual reserves."""
    token = int(virtual_token)
    if token <= 0:
        return 0.0
    return int(virtual_quote) / token


class Launch:
    """Everything the bot remembers about one coin's early life."""

    def __init__(self, mint, slot, price, creator, dev_buy_sol):
        self.mint = mint
        self.slot = slot
        self.create_price = price
        self.creator = creator
        self.dev_buy_sol = dev_buy_sol
        self.buyers = {}
        self.trades = 0
        self.buy_sol = 0.0
        self.sell_sol = 0.0
        self.worst_sell_share = 0.0
        self.creator_sold = False
        self.last_seen_slot = slot
        self.last_price = price
        self.last_real_sol = 0.0

    def note_trade(self, event, slot, real_sol):
        """Fold one TradeEvent into this coin's early tape."""
        sol = to_sol(event["sol_amount"])
        self.trades += 1
        self.last_seen_slot = slot
        self.last_real_sol = real_sol
        price = curve_price(
            event["virtual_quote_reserves"], event["virtual_token_reserves"]
        )
        if price > 0:
            self.last_price = price
        if event["user"] == self.creator:
            if not event["is_buy"]:
                self.creator_sold = True
            return
        if event["is_buy"]:
            self.buyers[event["user"]] = True
            self.buy_sol += sol
        else:
            self.sell_sol += sol
            share = sol / real_sol if real_sol > 0 else 0.0
            if share > self.worst_sell_share:
                self.worst_sell_share = share

    def entry_ok(self, slot, price, real_sol):
        """True when this launch passes every entry filter right now."""
        if slot - self.slot > ENTRY_WINDOW_SLOTS:
            return False
        if not MIN_REAL_SOL <= real_sol <= MAX_REAL_SOL:
            return False
        if self.dev_buy_sol > MAX_DEV_BUY_SOL:
            return False
        if self.creator_sold:
            return False
        if len(self.buyers) < MIN_BUYERS:
            return False
        if len(self.buyers) > MAX_BUYERS:
            return False
        if self.trades < MIN_TRADES:
            return False
        if self.buy_sol - self.sell_sol < MIN_NET_FLOW_SOL:
            return False
        if self.buy_sol > 0 and self.sell_sol / self.buy_sol > MAX_SELL_SHARE:
            return False
        if self.worst_sell_share > MAX_SINGLE_SELL_SHARE:
            return False
        if self.create_price <= 0 or price <= 0:
            return False
        run = price / self.create_price - 1.0
        if run < MIN_RUN_PCT or run > MAX_RUN_PCT:
            return False
        return True


class Position:
    """One open paper position, with its exit bookkeeping."""

    def __init__(self, mint, slot, tokens, cost_sol, entry_price):
        self.mint = mint
        self.slot = slot
        self.tokens = tokens
        self.cost_sol = cost_sol
        self.entry_price = entry_price
        self.best_value_sol = cost_sol
        self.last_seen_slot = slot


class SprintBot:
    """The bot: hello in, batches in, one reply out per batch."""

    def __init__(self):
        self.capital_sol = 0.0
        self.cash_sol = 0.0
        self.max_positions = 5
        self.max_order_bps = 100
        self.max_orders = 16
        self.launches = {}
        self.positions = {}
        self.closed = {}
        self.pending_buys = {}
        self.pending_sells = {}
        self.counter = 0
        self.peak_equity_sol = 0.0

    def run(self):
        """Consume stdin line by line until the harness says done."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            kind = message.get("type")
            if kind == "hello":
                self.on_hello(message)
            elif kind == "batch":
                reply = self.on_batch(message)
                sys.stdout.write(json.dumps(reply) + "\n")
                sys.stdout.flush()
            elif kind == "done":
                break

    def on_hello(self, message):
        """Store the window's capital and caps from the hello message."""
        self.capital_sol = to_sol(message["capital"]["lamports"])
        self.cash_sol = self.capital_sol
        caps = message["caps"]
        self.max_positions = int(caps["max_open_positions"])
        self.max_order_bps = int(caps["max_order_bps_of_real_reserve"])
        self.max_orders = int(caps["max_orders_per_batch"])
        self.peak_equity_sol = self.capital_sol

    def prune_launches(self, slot):
        """Drop old launches that can never be entered or exited.

        A launch stops being entry-eligible a few slots after creation,
        and a launch with no position behind it is dead weight after
        that. Keeping the tape small matters over a 24h window with
        thousands of coins: per-batch work stays proportional to the
        coins created in the last few slots, not to the whole day.
        """
        horizon = ENTRY_WINDOW_SLOTS + STALE_SLOTS
        for mint in list(self.launches):
            launch = self.launches[mint]
            if mint in self.positions:
                continue
            if slot - launch.slot > horizon:
                self.launches.pop(mint, None)

    def on_batch(self, message):
        """Update the tape, decide exits and entries, reply with orders."""
        slot = int(message["slot"])
        seq = int(message["seq"])
        orders = []
        self.apply_events(message, slot)
        self.apply_fills(message)
        self.apply_account(message)
        self.apply_marks(message, slot)
        self.prune_launches(slot)
        orders.extend(self.decide_exits(slot))
        orders.extend(self.decide_entries(message, slot))
        return {"type": "reply", "seq": seq, "orders": orders[: self.max_orders]}

    def apply_events(self, message, slot):
        """Fold this batch's events into the per-coin tapes."""
        for event in message.get("events", []):
            kind = event.get("event")
            if kind == "CreateEvent":
                mint = event["mint"]
                if mint in self.launches or mint in self.closed:
                    continue
                price = curve_price(
                    event["virtual_quote_reserves"],
                    event["virtual_token_reserves"],
                )
                dev_buy = 0.0
                # The creator's launch buy is the first trade; until it
                # arrives the dev buy is unknown, so start at zero and
                # let note_trade fill it in from the creator's trades.
                self.launches[mint] = Launch(
                    mint, slot, price, event["creator"], dev_buy
                )
            elif kind == "TradeEvent":
                launch = self.launches.get(event["mint"])
                if launch is None:
                    continue
                real_sol = to_sol(event["real_quote_reserves"])
                launch.note_trade(event, slot, real_sol)
                if event["user"] == launch.creator and event["is_buy"]:
                    launch.dev_buy_sol += to_sol(event["sol_amount"])
            elif kind == "CompleteEvent":
                self.launches.pop(event["mint"], None)

    def apply_fills(self, message):
        """Learn entry prices and costs from our own filled orders.

        Fills arrive for every decided order, filled or not. A buy is
        only really ours once it fills: until then it is pending, and
        a rejected buy frees the coin for another try instead of
        burning it. A sell likewise only closes the position on its
        fill; a rejected sell leaves the position tracked so the exit
        can be retried next batch.
        """
        for fill in message.get("fills", []):
            mint = fill["mint"]
            if fill["side"] == "buy":
                self.pending_buys.pop(mint, None)
                if fill.get("status") != "filled":
                    continue
                if mint not in self.launches:
                    continue
                tokens = int(fill["token_amount"])
                quote_lamports = int(fill["quote_lamports"])
                quote = quote_lamports / LAMPORTS_PER_SOL
                if tokens > 0 and quote > 0:
                    # Entry price in lamports per raw token unit, the
                    # same units curve_price returns, so values compare.
                    entry_price = quote_lamports / tokens
                    self.positions[mint] = Position(
                        mint,
                        int(fill["slot"]),
                        tokens,
                        quote,
                        entry_price,
                    )
                    self.closed[mint] = True
            elif fill["side"] == "sell":
                self.pending_sells.pop(mint, None)
                if fill.get("status") != "filled":
                    continue
                self.positions.pop(mint, None)

    def apply_account(self, message):
        """Trust the harness account snapshot for cash and holdings."""
        account = message.get("account") or {}
        self.cash_sol = to_sol(account.get("cash_lamports", "0"))
        live = {}
        for held in account.get("positions", []):
            live[held["mint"]] = held
        for mint in list(self.positions):
            if mint not in live:
                self.positions.pop(mint, None)
                self.pending_sells.pop(mint, None)
        for mint, held in live.items():
            position = self.positions.get(mint)
            if position is None:
                continue
            position.tokens = int(held["token_amount"])
            position.cost_sol = to_sol(held["cost_lamports"])

    def apply_marks(self, message, slot):
        """Refresh each position's value from the batch's marks."""
        for mark in message.get("marks", []):
            position = self.positions.get(mark["mint"])
            if position is None:
                continue
            price = curve_price(
                mark["virtual_quote_reserves"], mark["virtual_token_reserves"]
            )
            if price <= 0:
                continue
            value = position.tokens / LAMPORTS_PER_SOL * price
            if value > position.best_value_sol:
                position.best_value_sol = value
            position.last_seen_slot = slot
            launch = self.launches.get(mark["mint"])
            if launch is not None:
                launch.last_seen_slot = slot
                launch.last_price = price
        self.refresh_peak_equity(message)

    def refresh_peak_equity(self, message):
        """Track peak equity so the drawdown guard can bite."""
        equity = self.cash_sol
        marks = {m["mint"]: m for m in message.get("marks", [])}
        for mint, position in self.positions.items():
            mark = marks.get(mint)
            if mark is None:
                equity += position.cost_sol
                continue
            price = curve_price(
                mark["virtual_quote_reserves"], mark["virtual_token_reserves"]
            )
            equity += position.tokens / LAMPORTS_PER_SOL * price
        if equity > self.peak_equity_sol:
            self.peak_equity_sol = equity

    def decide_exits(self, message_slot):
        """Sell whole positions whose exit rule has fired."""
        orders = []
        for mint in list(self.positions):
            if mint in self.pending_sells:
                continue
            position = self.positions[mint]
            launch = self.launches.get(mint)
            price = None
            if launch is not None:
                price = launch.last_price
            if price is None or price <= 0:
                continue
            value = position.tokens / LAMPORTS_PER_SOL * price
            if position.cost_sol <= 0:
                continue
            gain = value / position.cost_sol - 1.0
            reason = None
            if gain >= TAKE_PROFIT_PCT:
                reason = "take-profit"
            elif gain <= -STOP_LOSS_PCT:
                reason = "stop-loss"
            elif (
                position.best_value_sol >= position.cost_sol * (1.0 + TRAIL_ARM_PCT)
                and value <= position.best_value_sol * (1.0 - TRAIL_GIVEBACK_PCT)
            ):
                reason = "trailing-stop"
            elif message_slot - position.slot >= MAX_HOLD_SLOTS:
                reason = "time-limit"
            elif launch is not None and launch.creator_sold:
                reason = "creator-sold"
            elif (
                launch is not None
                and launch.worst_sell_share >= MAX_SINGLE_SELL_SHARE
            ):
                reason = "big-sell"
            elif (
                launch is not None
                and message_slot - launch.last_seen_slot >= STALE_SLOTS
            ):
                reason = "stale"
            if reason is None:
                continue
            self.counter += 1
            client_id = "x%d-%s" % (self.counter, reason)
            orders.append(
                {
                    "side": "sell",
                    "client_id": client_id,
                    "mint": mint,
                    "token_amount": str(position.tokens),
                    "max_slippage_bps": SELL_SLIPPAGE_BPS,
                }
            )
            # The position stays tracked until the sell's fill
            # arrives; only the pending flag stops a second order.
            self.pending_sells[mint] = client_id
        return orders

    def decide_entries(self, message, slot):
        """Buy fresh launches that pass every filter, within the caps."""
        open_count = len(self.positions) + len(self.pending_buys)
        if open_count >= self.max_positions:
            return []
        equity = self.cash_sol
        for position in self.positions.values():
            equity += position.cost_sol
        if self.peak_equity_sol > 0:
            dip = 1.0 - equity / self.peak_equity_sol
            if dip >= MAX_DRAWDOWN_PCT:
                return []
        orders = []
        for mint, launch in self.launches.items():
            if mint in self.positions or mint in self.closed:
                continue
            if mint in self.pending_buys:
                continue
            if slot - launch.slot > ENTRY_WINDOW_SLOTS:
                continue
            # Price and reserves come from the launch's own tape, not
            # from marks: the harness only marks mints the bot already
            # holds (or has pending orders for), so a fresh coin has
            # no mark until after the bot buys it.
            real_sol = launch.last_real_sol
            price = launch.last_price
            if not launch.entry_ok(slot, price, real_sol):
                continue
            cap_sol = real_sol * self.max_order_bps / 10000.0 * 0.9
            stake = min(STAKE_SOL, cap_sol)
            if stake < 0.01 or stake > self.cash_sol - 0.02:
                continue
            self.counter += 1
            client_id = "b%d" % self.counter
            orders.append(
                {
                    "side": "buy",
                    "client_id": client_id,
                    "mint": mint,
                    "quote_lamports": str(int(stake * LAMPORTS_PER_SOL)),
                    "max_slippage_bps": BUY_SLIPPAGE_BPS,
                }
            )
            # Pending, not closed: the coin is only spent once its
            # buy fills, and a rejected buy leaves it tryable again.
            self.pending_buys[mint] = client_id
            self.cash_sol -= stake
            if len(self.positions) + len(self.pending_buys) >= self.max_positions:
                break
        return orders


def main():
    SprintBot().run()


if __name__ == "__main__":
    main()
