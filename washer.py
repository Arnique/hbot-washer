from datetime import datetime
from decimal import Decimal
from numpy import random
from copy import deepcopy
from typing import List

from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.event.events import BuyOrderCreatedEvent, OrderFilledEvent, SellOrderCreatedEvent, BuyOrderCompletedEvent, SellOrderCompletedEvent, MarketOrderFailureEvent

line = "-" * 40

from enum import Enum
 
class PriceMode(Enum):
    SPREAD = 2
    MID = 3
    
class BotMode(Enum):
    LIVE = 1
    PAPER = 2
        
class CherryWasher(ScriptStrategyBase):
    # Washer mode
    bot_mode = BotMode.LIVE
    
    # Market
    exchange = "gate_io" if bot_mode == BotMode.LIVE else "gate_io_paper_trade"
    price_mode = PriceMode.MID if bot_mode == BotMode.LIVE else PriceMode.SPREAD
    
    decimals = 7
    base = "TRX"
    quote = "USDT"
    trading_pair = f"{base}-{quote}"
    markets = { exchange: { trading_pair } }

    # Accounting
    daily_loss = 0
    daily_profit = 0
    daily_vol = 0
    daily_bal = 0
    max_daily_loss = 200

    # Random order range
    min_order_usd = 10
    max_order_usd = 10
    order_amount_usd = 0
    
    # Equal amount to match in e.g 0.9
    # tip: mismatched amounts can fool some exchanges
    amt_match = Decimal(1)

    # Order types
    price_source = PriceType.MidPrice
    sell_type = OrderType.LIMIT
    buy_type = OrderType.LIMIT
    
    # Pricing
    last_price = 0
    mid_price = 0
    best_bid = 0
    best_ask = 0
    best_buy = 0
    best_sell = 0
    cur_spread = 0
    cur_spread_pct = 0
    sell_price = 0
    buy_price = 0
    
    # Number of times to split order
    buy_order_len = 1
    sell_order_len = 1
    
    # Random interval range in seconds
    min_wash_interval = 5
    max_wash_interval = 15

    # Defaults
    trade_cost = 0
    trade_vol = 0
    trade_cost_pct = 0
    
    sell_done = 0
    buy_done = 0
    
    sell_created = 0
    buy_created = 0
    
    last_trade_ts = 0
    wash_interval = 0
    
    pos_closed = True
    today = datetime.now()
    started = False
    
    # Errors
    last_error_ts = None
    last_error_count = 0
    retry_interval = 15
    max_retries = 20
    
    def on_tick(self):
        # Ready 
        if not self.started and self.ready_to_trade:
            self.init_params()
            self.wash_interval = 0
            self.started = True
            # self.daily_summary()
        
        # Monitor completed trades
        if self.sell_done == self.sell_order_len and self.buy_done == self.buy_order_len :
            self.done()
            return
        
        self.check_day()
        elapsed = self.current_timestamp - self.last_trade_ts

        # Periodic trades
        if (not self.pos_closed) or elapsed < self.wash_interval: return

        # Monitor daily loss
        if abs(self.daily_loss) >= self.max_daily_loss:
            msg = f"[ERROR] Max daily loss of ${self.max_daily_loss} reached!"
            self.logger().error(msg)
            self.notify_hb_app(msg)
            self.abort()
            return
        
        self.exec_trades()
    
    def refresh_prices(self) :
        try:
            self.last_price = round(self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.LastTrade), self.decimals)
            self.mid_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.MidPrice)
            self.best_ask = self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestAsk)
            self.best_bid = self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestBid)

            self.cur_spread = round(self.best_ask - self.best_bid, self.decimals)
            self.cur_spread_pct = round(self.cur_spread / self.best_bid * 100, 2)
            
            msg = f"\nLast Price: ${self.last_price}\n"
            msg += f"Ask Price: ${self.best_ask}\n"
            msg += f"Bid Price: ${self.best_bid}\n"
            msg += f"Spread: ${self.cur_spread}({self.cur_spread_pct}%)\n\n{line}"
            
            self.notify_hb_app(msg)
            return True
            
        except Exception as e:
            err = f"[PRICE ERROR] {err}"
            self.logger().error(err)
            # self.notify_hb_app(err)
            
            return False
                
    def exec_trades(self) :
        # If price error retry after x seconds
        if self.last_error_count > 0 :
            elapsed = round((datetime.now() - self.last_error_ts).total_seconds())
            if elapsed < self.retry_interval : return
        
        # Try to refresh prices
        if not self.refresh_prices() :
            self.last_error_ts = datetime.now()
            self.last_error_count += 1
            m = f"[TRADE ABORTED] Failed fetching prices ({self.last_error_count}/{self.max_retries}). Retrying in {self.retry_interval}s..."
            
            self.logger().error(m)
            self.notify_hb_app(m)
            
            if self.last_error_count == self.max_retries :
                m = f"[ABORT] Max retries of ({self.max_retries}) reached. Aborting..."
                self.logger().error(m)
                self.notify_hb_app(m)
                self.abort()
            
            return
        
        # Reset price errors
        self.last_error_ts = None
        self.last_error_count = 0
        
        # SPREAD gurantees fills but at current spread
        if self.price_mode == self.price_mode.SPREAD :
            self.sell_price = self.best_bid
            self.buy_price = self.best_ask
            
        # MID uses mid price
        if self.price_mode == self.price_mode.MID :
            self.sell_price = self.mid_price
            self.buy_price = self.mid_price
            
        sell_amt = Decimal(round(self.order_amount_usd / self.last_price))
        buy_amt = sell_amt * self.amt_match 

        # Split orders to fool exchanges
        try:
            sell_orders = self.make_orders(sell_amt, self.sell_price, "SELL")
            buy_orders = self.make_orders(buy_amt, self.buy_price, "BUY")
            
            msg = f"\nReady to Execute ${self.order_amount_usd} in trades...\n\n"
            msg += f"Pair: {self.trading_pair}\n"
            msg += f"Exchange: {self.exchange}\n"
            msg += f"Price Mode: {self.price_mode.name}\n\n"
            msg += f"({len(sell_orders)}) Sell Order(s) @ ${self.sell_price}\n"
            msg += f"({len(buy_orders)}) Buy Order(s) @ ${self.buy_price}\n\n{line}\n"
            self.notify_hb_app(msg)
            
            for sell_order in sell_orders :
                self.sell(
                    connector_name = self.exchange,
                    trading_pair = self.trading_pair,
                    amount = sell_order.amount,
                    order_type = sell_order.order_type,
                    price = sell_order.price
                )
            
            for buy_order in buy_orders :
                self.buy(
                    connector_name = self.exchange,
                    trading_pair = self.trading_pair,
                    amount = buy_order.amount,
                    order_type = buy_order.order_type,
                    price = buy_order.price
                )

            self.trade_cost = 0
            self.trade_vol = round(sell_amt * self.sell_price, 2)
            self.pos_closed = False

        except Exception as e:
            self.logger().info(f"[ORDER ERROR] {e}")

    def did_create_sell_order(self, event: SellOrderCreatedEvent):
        self.logger().info(event)
        self.sell_created += 1
        msg = f"[SELL] ({self.sell_created}/{self.sell_order_len}) {round(event.amount, 2)} of {event.trading_pair} @ ${round(event.price, self.decimals)}"
        self.notify_hb_app(msg)

    def did_create_buy_order(self, event: BuyOrderCreatedEvent):
        self.logger().info(event)
        self.buy_created += 1
        msg = f"[BUY] ({self.buy_created}/{self.buy_order_len}) {round(event.amount, 2)} of {event.trading_pair} @ ${round(event.price, self.decimals)}"
        self.notify_hb_app(msg)

    def did_complete_sell_order(self, event: SellOrderCompletedEvent) :
        self.trade_cost += event.quote_asset_amount
        self.trade_cost = round(self.trade_cost, 2)
        self.sell_done += 1
        
        msg = f"[âœ“ SOLD] ({self.sell_done}/{self.sell_order_len}) {round(event.base_asset_amount, 2)} {event.base_asset} : ${round(event.quote_asset_amount, 2)}"
        self.logger().info(event)
        self.notify_hb_app(msg)

    def did_complete_buy_order(self, event: BuyOrderCompletedEvent) :
        self.trade_cost -= event.quote_asset_amount
        self.trade_cost = round(self.trade_cost, 2)
        self.buy_done += 1
        
        msg = f"[âœ“ BOUGHT] ({self.buy_done}/{self.buy_order_len}) {round(event.base_asset_amount, 2)} {event.base_asset} : ${round(event.quote_asset_amount, 2)}"
        self.logger().info(event)
        self.notify_hb_app(msg)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = f"[FILLED:{event.order_type}] {event.order_id} price: {event.price} amnt: {event.amount} fee: {event.trade_fee.percent}"
        self.logger().info(msg)

    def done(self) :
        self.init_params()
        self.daily_loss += self.trade_cost
        self.last_trade_ts = self.current_timestamp
        self.daily_vol += self.trade_vol
        self.daily_vol = round(self.daily_vol, 2)
        self.trade_cost_pct = abs(round(self.trade_cost / self.trade_vol * 100, 2))
        
        self.trade_summary()
        self.daily_summary()
        
        self.pos_closed = True
        self.sell_done = 0
        self.buy_done = 0
        self.sell_created = 0
        self.buy_created = 0
        self.trade_cost = 0
        self.trade_cost_pct = 0
        
        msg = f"\n{line}\nNext Trade for ${self.order_amount_usd} in {self.wash_interval}s...\n{line}"
        self.notify_hb_app(msg)
        
    def trade_summary(self):
        msg = f"\n{line}\n\n"
        msg += f"Volume: ${self.trade_vol}\n"
        msg += f"Cost(fees + slip): ${abs(self.trade_cost)}({self.trade_cost_pct}%)\n"
        msg += f"Buy Order(s): {self.buy_order_len}\n"
        msg += f"Sell Order(s): {self.sell_order_len}"

        self.notify_hb_app(msg)
        return
    
    def daily_summary(self):
        usdt = round(self.get_bal("USDT"), 2)
        
        msg = f"\n{line}\n\n"
        msg += f"Daily Volume: ${self.daily_vol}\n"
        msg += f"Daily Cost: ${abs(self.daily_loss)}\n"
        msg += f"USDT BAL: ${usdt}"

        self.notify_hb_app(msg)
        return

    def did_fail_order(self, event: MarketOrderFailureEvent):
        self.notify_hb_app(f"[FAIL] ${event}")

    def make_orders(self, amount, price, side) -> List[OrderCandidate]:
        order = OrderCandidate(
            trading_pair = self.trading_pair,
            amount = amount,
            order_type = self.sell_type if side == 'SELL' else self.buy_type,
            price = price,
            is_maker = side == 'SELL',
            order_side = TradeType.SELL if side == 'SELL' else TradeType.BUY)

        adj = self.connectors[self.exchange].budget_checker.adjust_candidate(order, all_or_none=True)

        if adj.amount == Decimal(0) :
            msg = f"[ERROR] Insufficient balance. {side} {order.amount} {order.trading_pair} @ ${order.price} aborted!"
            self.logger().error(msg)
            self.notify_hb_app(msg)
            self.abort()
            return
        
        order_len = self.sell_order_len if side == 'SELL' else self.buy_order_len
        orders = self.split_order(amount, order_len)
        self.logger().info(orders)
        
        for i, x in enumerate(orders):
            v = deepcopy(order)
            v.amount = Decimal(x)
            orders[i] = v
            
        return orders
    
    def split_order(self, amt = 0, size = 0):
        if size == 1 : return [amt]
        
        arr = [round(random.random() * 100) for i in range(0,size)]
        arr_sum = Decimal(sum(arr))
        last = len(arr) - 1
        
        for i, x in enumerate(arr):
            x = Decimal(x)
            if i == last:
                arr[i] = amt - sum(arr[:last])
            else:
                arr[i] = Decimal(round(x / arr_sum * amt))
            
        return arr

    def get_bal(self, asset):
        df = self.get_balance_df()
        idx = df.index[df['Asset'] == asset][0]
        return df["Total Balance"][idx]

    def check_day(self):
        now = datetime.now()
        if now.day != self.today.day :
            self.today = now
            self.daily_loss = 0
            self.daily_vol = 0

            msg = f"New Day! Counters reset"
            self.logger().error(msg)
            self.notify_hb_app(msg)

    def init_params(self) :
        self.wash_interval = random.randint(self.min_wash_interval, self.max_wash_interval)
        self.order_amount_usd = (self.min_order_usd 
                                 if self.min_order_usd == self.max_order_usd 
                                 else random.randint(self.min_order_usd, self.max_order_usd))

    def abort(self):
        msg = "[ABORT] Stopping..."
        HummingbotApplication.main_application().stop()